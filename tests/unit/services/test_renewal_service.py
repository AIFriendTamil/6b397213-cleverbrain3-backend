"""Unit tests for Contract Lifecycle Management renewal service (renewal_service.py)."""

from datetime import date as date_type, datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.contract import Contract, ContractStatus, RenewalNotificationLog, RenewalWarningStage
from src.services.clm.renewal_service import (
    RenewalPermissionDeniedError,
    RenewalValidationError,
    calculate_days_until_expiration,
    determine_warning_stage,
    dispatch_renewal_notification,
    get_expiring_contracts,
    is_notification_dispatched_for_stage,
    is_notification_dispatched_today,
    list_renewal_notification_logs,
    normalize_roles,
    process_contract_renewals,
    validate_renewal_access,
)


@pytest.fixture
def test_db_session():
    """Fixture providing an in-memory SQLite database session for renewal service tests."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_normalize_roles_and_validate_renewal_access():
    """Test RBAC role normalization and access validation for renewal service operations."""
    assert normalize_roles("ACCOUNT_MANAGER, legal_manager") == {"account_manager", "legal_manager"}
    assert normalize_roles(None) == set()

    # Valid read access
    assert validate_renewal_access("stakeholder", "read") == {"stakeholder"}
    assert validate_renewal_access("account_manager", "read") == {"account_manager"}

    # Valid write access
    assert validate_renewal_access("legal_manager", "write") == {"legal_manager"}
    assert validate_renewal_access("admin", "write") == {"admin"}

    # Valid execute access
    assert validate_renewal_access("worker", "execute") == {"worker"}
    assert validate_renewal_access("system", "execute") == {"system"}

    # Unauthorized role access
    with pytest.raises(RenewalPermissionDeniedError):
        validate_renewal_access("unauthorized_role", "read")

    with pytest.raises(RenewalPermissionDeniedError):
        validate_renewal_access("stakeholder", "write")

    with pytest.raises(RenewalPermissionDeniedError):
        validate_renewal_access("stakeholder", "execute")

    with pytest.raises(RenewalValidationError):
        validate_renewal_access("admin", "invalid_action")


def test_calculate_days_until_expiration_and_leap_years():
    """Test days remaining calculation including month length variance and leap years."""
    base_date = date_type(2026, 1, 1)
    exp_date = date_type(2026, 1, 31)
    assert calculate_days_until_expiration(exp_date, as_of_date=base_date) == 30

    # Past expiration
    exp_past = date_type(2025, 12, 25)
    assert calculate_days_until_expiration(exp_past, as_of_date=base_date) == -7

    # Leap year calculation: 2028 is a leap year (Feb has 29 days)
    leap_start = date_type(2028, 2, 1)
    leap_end = date_type(2028, 3, 1)
    assert calculate_days_until_expiration(leap_end, as_of_date=leap_start) == 29

    # Non-leap year calculation: 2027 is not a leap year (Feb has 28 days)
    non_leap_start = date_type(2027, 2, 1)
    non_leap_end = date_type(2027, 3, 1)
    assert calculate_days_until_expiration(non_leap_end, as_of_date=non_leap_start) == 28


def test_determine_warning_stage_windows_and_exact_milestones():
    """Test warning stage determination across 90, 60, 30 day thresholds and exact milestones."""
    as_of = date_type(2026, 6, 1)

    # Range window mode (default)
    assert determine_warning_stage(as_of + timedelta(days=90), as_of_date=as_of) == RenewalWarningStage.DAYS_90
    assert determine_warning_stage(as_of + timedelta(days=75), as_of_date=as_of) == RenewalWarningStage.DAYS_90
    assert determine_warning_stage(as_of + timedelta(days=60), as_of_date=as_of) == RenewalWarningStage.DAYS_60
    assert determine_warning_stage(as_of + timedelta(days=45), as_of_date=as_of) == RenewalWarningStage.DAYS_60
    assert determine_warning_stage(as_of + timedelta(days=30), as_of_date=as_of) == RenewalWarningStage.DAYS_30
    assert determine_warning_stage(as_of + timedelta(days=10), as_of_date=as_of) == RenewalWarningStage.DAYS_30
    assert determine_warning_stage(as_of + timedelta(days=0), as_of_date=as_of) == RenewalWarningStage.DAYS_30

    # Outside warning threshold (> 90 days)
    assert determine_warning_stage(as_of + timedelta(days=91), as_of_date=as_of) is None

    # Already expired (< 0 days)
    assert determine_warning_stage(as_of - timedelta(days=1), as_of_date=as_of) is None

    # Exact day milestone mode
    assert determine_warning_stage(as_of + timedelta(days=90), as_of_date=as_of, exact_day_only=True) == RenewalWarningStage.DAYS_90
    assert determine_warning_stage(as_of + timedelta(days=89), as_of_date=as_of, exact_day_only=True) is None
    assert determine_warning_stage(as_of + timedelta(days=60), as_of_date=as_of, exact_day_only=True) == RenewalWarningStage.DAYS_60
    assert determine_warning_stage(as_of + timedelta(days=30), as_of_date=as_of, exact_day_only=True) == RenewalWarningStage.DAYS_30


def test_dispatch_renewal_notification_idempotency(test_db_session: Session):
    """Test idempotent notification dispatch preventing duplicate alerts within 24h or stage."""
    today = date_type(2026, 6, 1)
    contract = Contract(
        contract_number="CNT-RENEW-001",
        title="SaaS Subscription Master Agreement",
        counterparty="Apex Solutions Inc",
        status=ContractStatus.ACTIVE,
        effective_on=today - timedelta(days=275),
        expires_on=today + timedelta(days=90),
        auto_renew=False,
    )
    test_db_session.add(contract)
    test_db_session.commit()

    # Initial dispatch for DAYS_90 stage
    log1 = dispatch_renewal_notification(
        db_session=test_db_session,
        contract=contract,
        warning_stage=RenewalWarningStage.DAYS_90,
        as_of_date=today,
    )
    assert log1 is not None
    assert log1.contract_id == contract.id
    assert log1.warning_stage == RenewalWarningStage.DAYS_90
    assert log1.dispatched_at_date == today
    assert log1.recipient_target == "Apex Solutions Inc"

    # Verify idempotency check functions
    assert is_notification_dispatched_today(test_db_session, contract.id, RenewalWarningStage.DAYS_90, today) is True
    assert is_notification_dispatched_for_stage(test_db_session, contract.id, RenewalWarningStage.DAYS_90) is True

    # Immediate second dispatch attempt on same day should return existing log without creating duplicate
    log2 = dispatch_renewal_notification(
        db_session=test_db_session,
        contract=contract,
        warning_stage=RenewalWarningStage.DAYS_90,
        as_of_date=today,
    )
    assert log2 is not None
    assert log2.id == log1.id

    logs_in_db = test_db_session.query(RenewalNotificationLog).all()
    assert len(logs_in_db) == 1

    # Second dispatch attempt on subsequent day for SAME stage should also be skipped by stage idempotency
    next_day = today + timedelta(days=1)
    log3 = dispatch_renewal_notification(
        db_session=test_db_session,
        contract=contract,
        warning_stage=RenewalWarningStage.DAYS_90,
        as_of_date=next_day,
    )
    assert log3 is not None
    assert log3.id == log1.id
    assert len(test_db_session.query(RenewalNotificationLog).all()) == 1


def test_dispatch_renewal_notification_revised_contract(test_db_session: Session):
    """Test notification dispatch when a contract expiration date is extended/revised."""
    today = date_type(2026, 6, 1)
    contract = Contract(
        contract_number="CNT-RENEW-002",
        title="Extended Enterprise Agreement",
        counterparty="Global Corp",
        status=ContractStatus.ACTIVE,
        effective_on=today - timedelta(days=200),
        expires_on=today + timedelta(days=90),
        auto_renew=False,
    )
    test_db_session.add(contract)
    test_db_session.commit()

    # Dispatch 90-day warning for initial expiration date
    log1 = dispatch_renewal_notification(
        db_session=test_db_session,
        contract=contract,
        warning_stage=RenewalWarningStage.DAYS_90,
        as_of_date=today,
    )
    assert log1 is not None

    # Later, contract is extended to a new expiration date
    future_date = today + timedelta(days=400)
    contract.expires_on = future_date
    contract.updated_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    test_db_session.add(contract)
    test_db_session.commit()

    # A year later, contract enters 90-day window again
    new_check_date = future_date - timedelta(days=90)
    log2 = dispatch_renewal_notification(
        db_session=test_db_session,
        contract=contract,
        warning_stage=RenewalWarningStage.DAYS_90,
        as_of_date=new_check_date,
    )
    assert log2 is not None
    assert log2.id != log1.id
    assert log2.dispatched_at_date == new_check_date

    logs_in_db = test_db_session.query(RenewalNotificationLog).all()
    assert len(logs_in_db) == 2


def test_process_contract_renewals_batch(test_db_session: Session):
    """Test batch daily renewal processing evaluating contracts and dispatching stage alerts."""
    eval_date = date_type(2026, 6, 1)

    # 1. Active contract in 90-day window
    c1 = Contract(
        contract_number="CNT-BATCH-90",
        title="Contract 90 Days Left",
        counterparty="Vendor A",
        status=ContractStatus.ACTIVE,
        effective_on=eval_date - timedelta(days=200),
        expires_on=eval_date + timedelta(days=85),
        auto_renew=False,
    )
    # 2. Active contract in 60-day window
    c2 = Contract(
        contract_number="CNT-BATCH-60",
        title="Contract 60 Days Left",
        counterparty="Vendor B",
        status=ContractStatus.ACTIVE,
        effective_on=eval_date - timedelta(days=200),
        expires_on=eval_date + timedelta(days=50),
        auto_renew=False,
    )
    # 3. Active contract in 30-day window
    c3 = Contract(
        contract_number="CNT-BATCH-30",
        title="Contract 30 Days Left",
        counterparty="Vendor C",
        status=ContractStatus.ACTIVE,
        effective_on=eval_date - timedelta(days=200),
        expires_on=eval_date + timedelta(days=15),
        auto_renew=False,
    )
    # 4. Draft contract in 30-day window (should be ignored)
    c4 = Contract(
        contract_number="CNT-BATCH-DRAFT",
        title="Draft Contract",
        counterparty="Vendor D",
        status=ContractStatus.DRAFT,
        effective_on=eval_date - timedelta(days=200),
        expires_on=eval_date + timedelta(days=15),
        auto_renew=False,
    )
    # 5. Active contract expiring in 180 days (outside warning thresholds)
    c5 = Contract(
        contract_number="CNT-BATCH-FAR",
        title="Contract 180 Days Left",
        counterparty="Vendor E",
        status=ContractStatus.ACTIVE,
        effective_on=eval_date - timedelta(days=100),
        expires_on=eval_date + timedelta(days=180),
        auto_renew=False,
    )

    test_db_session.add_all([c1, c2, c3, c4, c5])
    test_db_session.commit()

    # Execute batch process
    result = process_contract_renewals(
        db_session=test_db_session,
        as_of_date=eval_date,
    )

    assert result["as_of_date"] == eval_date.isoformat()
    assert result["evaluated_contracts_count"] == 4  # Only active contracts evaluated (c1, c2, c3, c5)
    assert result["dispatched_count"] == 3  # c1 (90d), c2 (60d), c3 (30d)
    assert result["skipped_count"] == 0

    logs = list_renewal_notification_logs(test_db_session)
    assert len(logs) == 3

    stages = {log.warning_stage for log in logs}
    assert stages == {RenewalWarningStage.DAYS_90, RenewalWarningStage.DAYS_60, RenewalWarningStage.DAYS_30}

    # Re-running batch process on same day should yield 0 new dispatches (all skipped due to idempotency)
    result2 = process_contract_renewals(
        db_session=test_db_session,
        as_of_date=eval_date,
    )
    assert result2["evaluated_contracts_count"] == 4
    assert result2["dispatched_count"] == 0
    assert result2["skipped_count"] == 3


def test_get_expiring_contracts_query_filter(test_db_session: Session):
    """Test querying active contracts expiring within specified renewal timeframes (FR-7)."""
    today = date_type(2026, 6, 1)

    c1 = Contract(
        contract_number="CNT-EXP-20",
        title="Expires in 20 days",
        counterparty="Vendor 20",
        status=ContractStatus.ACTIVE,
        effective_on=today - timedelta(days=100),
        expires_on=today + timedelta(days=20),
        auto_renew=False,
    )
    c2 = Contract(
        contract_number="CNT-EXP-50",
        title="Expires in 50 days",
        counterparty="Vendor 50",
        status=ContractStatus.ACTIVE,
        effective_on=today - timedelta(days=100),
        expires_on=today + timedelta(days=50),
        auto_renew=False,
    )
    c3 = Contract(
        contract_number="CNT-EXP-100",
        title="Expires in 100 days",
        counterparty="Vendor 100",
        status=ContractStatus.ACTIVE,
        effective_on=today - timedelta(days=100),
        expires_on=today + timedelta(days=100),
        auto_renew=False,
    )

    test_db_session.add_all([c1, c2, c3])
    test_db_session.commit()

    # Query contracts expiring within 30 days
    exp_30 = get_expiring_contracts(test_db_session, renewal_days=30, as_of_date=today)
    assert len(exp_30) == 1
    assert exp_30[0].contract_number == "CNT-EXP-20"

    # Query contracts expiring within 60 days
    exp_60 = get_expiring_contracts(test_db_session, renewal_days=60, as_of_date=today)
    assert len(exp_60) == 2
    assert [c.contract_number for c in exp_60] == ["CNT-EXP-20", "CNT-EXP-50"]

    # Query contracts expiring within 90 days
    exp_90 = get_expiring_contracts(test_db_session, renewal_days=90, as_of_date=today)
    assert len(exp_90) == 2

    # Query without timeframe filter returns all active contracts ordered by expiration
    exp_all = get_expiring_contracts(test_db_session, as_of_date=today)
    assert len(exp_all) == 3
    assert exp_all[0].contract_number == "CNT-EXP-20"
    assert exp_all[1].contract_number == "CNT-EXP-50"
    assert exp_all[2].contract_number == "CNT-EXP-100"
