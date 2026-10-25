"""Unit tests for Celery daily background worker task in clm_tasks.py."""

from datetime import date as date_type, timedelta
from unittest.mock import patch
import pytest
from sqlalchemy.orm import Session

from src.lib.logging import get_trace_id
from src.models.base import Base, create_db_engine
from src.models.contract import Contract, ContractStatus, RenewalNotificationLog, RenewalWarningStage
from src.workers.celery_app import celery_app
from src.workers.clm_tasks import evaluate_contract_expirations


@pytest.fixture
def test_db_session():
    """Fixture providing an in-memory SQLite database session for worker task tests."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_evaluate_contract_expirations_success(test_db_session: Session):
    """Test successful evaluation and dispatch of contract renewal warnings."""
    eval_date = date_type(2026, 6, 1)

    c1 = Contract(
        contract_number="CNT-TASK-90",
        title="Contract expiring in 85 days",
        counterparty="Vendor A",
        status=ContractStatus.ACTIVE,
        effective_on=eval_date - timedelta(days=200),
        expires_on=eval_date + timedelta(days=85),
        auto_renew=False,
    )
    c2 = Contract(
        contract_number="CNT-TASK-30",
        title="Contract expiring in 15 days",
        counterparty="Vendor B",
        status=ContractStatus.ACTIVE,
        effective_on=eval_date - timedelta(days=200),
        expires_on=eval_date + timedelta(days=15),
        auto_renew=False,
    )

    test_db_session.add_all([c1, c2])
    test_db_session.commit()

    result = evaluate_contract_expirations(
        as_of_date_str=eval_date.isoformat(),
        correlation_id="test-cid-12345",
        db_session=test_db_session,
    )

    assert result["status"] == "completed"
    assert result["correlation_id"] == "test-cid-12345"
    assert result["evaluated_contracts_count"] == 2
    assert result["dispatched_count"] == 2
    assert result["skipped_count"] == 0

    logs = test_db_session.query(RenewalNotificationLog).all()
    assert len(logs) == 2
    stages = {log.warning_stage for log in logs}
    assert stages == {RenewalWarningStage.DAYS_90, RenewalWarningStage.DAYS_30}


def test_evaluate_contract_expirations_idempotency(test_db_session: Session):
    """Test idempotency of daily evaluation task preventing duplicate alerts on repeat execution."""
    eval_date = date_type(2026, 6, 1)

    c1 = Contract(
        contract_number="CNT-IDEM-60",
        title="Contract expiring in 50 days",
        counterparty="Vendor C",
        status=ContractStatus.ACTIVE,
        effective_on=eval_date - timedelta(days=150),
        expires_on=eval_date + timedelta(days=50),
        auto_renew=False,
    )
    test_db_session.add(c1)
    test_db_session.commit()

    # First run dispatches warning
    res1 = evaluate_contract_expirations(
        as_of_date_str=eval_date,
        correlation_id="idempotent-run-1",
        db_session=test_db_session,
    )
    assert res1["status"] == "completed"
    assert res1["dispatched_count"] == 1
    assert res1["skipped_count"] == 0

    # Second run on same day skips duplicate dispatch (NFR-4)
    res2 = evaluate_contract_expirations(
        as_of_date_str=eval_date,
        correlation_id="idempotent-run-2",
        db_session=test_db_session,
    )
    assert res2["status"] == "completed"
    assert res2["dispatched_count"] == 0
    assert res2["skipped_count"] == 1


def test_evaluate_contract_expirations_invalid_date_format():
    """Test handling of invalid date string format."""
    result = evaluate_contract_expirations(
        as_of_date_str="2026/13/45",
        correlation_id="bad-date-cid",
    )
    assert result["status"] == "failed"
    assert "Invalid date string format" in result["error"]
    assert result["correlation_id"] == "bad-date-cid"


def test_evaluate_contract_expirations_exception_handling(test_db_session: Session):
    """Test worker task error handling and session rollback when an exception occurs."""
    with patch("src.workers.clm_tasks.process_contract_renewals", side_effect=RuntimeError("Database error")):
        result = evaluate_contract_expirations(
            correlation_id="err-cid-999",
            db_session=test_db_session,
        )

        assert result["status"] == "failed"
        assert result["error"] == "Database error"
        assert result["correlation_id"] == "err-cid-999"


def test_evaluate_contract_expirations_clears_trace_id():
    """Test that trace ID context is cleared after task execution completes."""
    evaluate_contract_expirations(correlation_id="trace-test-id")
    assert get_trace_id() is None


def test_celery_app_clm_task_registration():
    """Test that evaluate_contract_expirations task and beat schedule are registered on celery_app."""
    registered_tasks = celery_app.tasks
    assert "src.workers.clm_tasks.evaluate_contract_expirations" in registered_tasks

    beat_schedule = celery_app.conf.beat_schedule
    assert "evaluate-contract-expirations-daily" in beat_schedule
    assert beat_schedule["evaluate-contract-expirations-daily"]["task"] == "src.workers.clm_tasks.evaluate_contract_expirations"
    assert beat_schedule["evaluate-contract-expirations-daily"]["schedule"] == 86400.0
