"""Unit tests for ArAgingSnapshot and ArrMetric ORM models."""

import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.financial import ArAgingSnapshot, ArrMetric


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_ar_aging_snapshot_creation_and_defaults(test_session: Session):
    """Verify ArAgingSnapshot creation, field mappings, defaults, and timestamps."""
    account_id = str(uuid.uuid4())

    snapshot = ArAgingSnapshot(
        account_id=account_id,
        current_balance=Decimal("10000.00"),
        bucket_30_balance=Decimal("5000.00"),
        bucket_60_balance=Decimal("2500.00"),
        bucket_90_plus_balance=Decimal("1200.50"),
    )
    test_session.add(snapshot)
    test_session.commit()

    retrieved = test_session.query(ArAgingSnapshot).filter_by(account_id=account_id).first()
    assert retrieved is not None
    assert retrieved.id is not None
    assert str(uuid.UUID(retrieved.id)) == retrieved.id
    assert retrieved.account_id == account_id
    assert retrieved.current_balance == Decimal("10000.00")
    assert retrieved.bucket_30_balance == Decimal("5000.00")
    assert retrieved.bucket_60_balance == Decimal("2500.00")
    assert retrieved.bucket_90_plus_balance == Decimal("1200.50")
    assert isinstance(retrieved.snapshot_timestamp, datetime)


def test_ar_aging_snapshot_defaults(test_session: Session):
    """Verify ArAgingSnapshot default balances when created without explicit values."""
    account_id = str(uuid.uuid4())

    snapshot = ArAgingSnapshot(account_id=account_id)
    test_session.add(snapshot)
    test_session.commit()

    retrieved = test_session.query(ArAgingSnapshot).filter_by(account_id=account_id).first()
    assert retrieved is not None
    assert retrieved.current_balance == Decimal("0.00")
    assert retrieved.bucket_30_balance == Decimal("0.00")
    assert retrieved.bucket_60_balance == Decimal("0.00")
    assert retrieved.bucket_90_plus_balance == Decimal("0.00")


def test_arr_metric_creation_and_defaults(test_session: Session):
    """Verify ArrMetric creation, field mappings, currency defaults, and timestamps."""
    account_id = str(uuid.uuid4())

    metric = ArrMetric(
        account_id=account_id,
        metric_period="2026-Q1",
        arr_value=Decimal("120000.00"),
        currency="USD",
    )
    test_session.add(metric)
    test_session.commit()

    retrieved = test_session.query(ArrMetric).filter_by(account_id=account_id).first()
    assert retrieved is not None
    assert retrieved.id is not None
    assert str(uuid.UUID(retrieved.id)) == retrieved.id
    assert retrieved.account_id == account_id
    assert retrieved.metric_period == "2026-Q1"
    assert retrieved.arr_value == Decimal("120000.00")
    assert retrieved.currency == "USD"
    assert isinstance(retrieved.recorded_at, datetime)


def test_arr_metric_query_by_period(test_session: Session):
    """Verify querying ArrMetric entries across multiple metric periods."""
    account_id = str(uuid.uuid4())

    m1 = ArrMetric(
        account_id=account_id,
        metric_period="2025-Q4",
        arr_value=Decimal("100000.00"),
    )
    m2 = ArrMetric(
        account_id=account_id,
        metric_period="2026-Q1",
        arr_value=Decimal("120000.00"),
    )
    test_session.add_all([m1, m2])
    test_session.commit()

    results = (
        test_session.query(ArrMetric)
        .filter_by(account_id=account_id)
        .order_by(ArrMetric.metric_period)
        .all()
    )
    assert len(results) == 2
    assert results[0].metric_period == "2025-Q4"
    assert results[0].arr_value == Decimal("100000.00")
    assert results[1].metric_period == "2026-Q1"
    assert results[1].arr_value == Decimal("120000.00")
