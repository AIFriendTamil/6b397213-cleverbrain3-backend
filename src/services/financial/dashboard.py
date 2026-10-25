"""Domain service for AR aging calculations and ARR trend aggregation."""

from dataclasses import asdict, dataclass
from datetime import date as date_type, datetime, timezone
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.financial import ArAgingSnapshot, ArrMetric
from src.models.invoice import Invoice, PaymentStatus

logger = logging.getLogger(__name__)

# Standard exchange rates to USD for multi-currency ARR conversions
EXCHANGE_RATES_TO_USD: Dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "CAD": 0.74,
    "AUD": 0.65,
    "JPY": 0.0067,
}


@dataclass
class ArAgingBucketSummary:
    """Dataclass representing AR aging balances for account buckets."""

    account_id: str
    current_balance: float
    bucket_30_balance: float
    bucket_60_balance: float
    bucket_90_plus_balance: float
    total_outstanding: float
    as_of_timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert dataclass to dictionary representation."""
        return asdict(self)


@dataclass
class ArrTrendDataPoint:
    """Dataclass representing a single ARR metric trend data point."""

    id: str
    account_id: str
    metric_period: str
    arr_value: float
    currency: str
    original_currency: str
    recorded_at: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert dataclass to dictionary representation."""
        return asdict(self)


def convert_currency_amount(
    amount: Decimal | float | int,
    from_currency: str,
    to_currency: str = "USD",
) -> float:
    """Convert an amount from a source currency to a target currency.

    Args:
        amount: Numerical monetary value.
        from_currency: Source currency string (e.g. 'EUR').
        to_currency: Target currency string (default 'USD').

    Returns:
        Converted amount as a floating point number.
    """
    if amount is None:
        return 0.0

    val = float(amount)
    from_curr = (from_currency or "USD").upper()
    to_curr = (to_currency or "USD").upper()

    if from_curr == to_curr:
        return round(val, 2)

    from_rate = EXCHANGE_RATES_TO_USD.get(from_curr)
    to_rate = EXCHANGE_RATES_TO_USD.get(to_curr)

    if from_rate is None or to_rate is None or to_rate == 0.0:
        logger.warning(
            "Missing or invalid exchange rate for %s -> %s; returning unconverted value",
            from_curr,
            to_curr,
        )
        return round(val, 2)

    converted = (val * from_rate) / to_rate
    return round(converted, 2)


def calculate_ar_aging(
    db: Session,
    account_id: Optional[str] = None,
    as_of_date: Optional[date_type] = None,
    save_snapshot: bool = True,
) -> ArAgingSnapshot:
    """Calculate AR aging bucket balances from outstanding invoices and create a snapshot.

    Buckets:
        - current_balance: overdue_days <= 0 (due today or in future)
        - bucket_30_balance: 1 <= overdue_days <= 30
        - bucket_60_balance: 31 <= overdue_days <= 60
        - bucket_90_plus_balance: overdue_days > 60

    Args:
        db: Synchronous SQLAlchemy database session.
        account_id: Optional account UUID filter.
        as_of_date: Reference date for age calculation (defaults to current UTC date).
        save_snapshot: Whether to persist snapshot in the database.

    Returns:
        ArAgingSnapshot ORM entity containing calculated balances.
    """
    if as_of_date is None:
        as_of_date = datetime.now(timezone.utc).date()

    stmt = select(Invoice).where(Invoice.payment_status != PaymentStatus.PAID)
    if account_id:
        stmt = stmt.where(Invoice.account_id == account_id)

    unpaid_invoices = db.scalars(stmt).all()

    curr_bal = Decimal("0.00")
    b30_bal = Decimal("0.00")
    b60_bal = Decimal("0.00")
    b90_bal = Decimal("0.00")

    for inv in unpaid_invoices:
        amt = inv.amount if inv.amount is not None else Decimal("0.00")
        overdue_days = (as_of_date - inv.due_date).days

        if overdue_days <= 0:
            curr_bal += amt
        elif 1 <= overdue_days <= 30:
            b30_bal += amt
        elif 31 <= overdue_days <= 60:
            b60_bal += amt
        else:
            b90_bal += amt

    snapshot = ArAgingSnapshot(
        account_id=account_id or "ALL",
        current_balance=curr_bal,
        bucket_30_balance=b30_bal,
        bucket_60_balance=b60_bal,
        bucket_90_plus_balance=b90_bal,
        snapshot_timestamp=datetime.now(timezone.utc),
    )

    if save_snapshot:
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)

    return snapshot


def get_ar_aging_summary(
    db: Session,
    account_id: Optional[str] = None,
    force_recalculate: bool = False,
) -> ArAgingBucketSummary:
    """Retrieve the latest AR aging bucket summary or recalculate dynamically.

    Args:
        db: Synchronous SQLAlchemy database session.
        account_id: Optional account UUID filter.
        force_recalculate: If True, forces dynamic recalculation from invoices.

    Returns:
        ArAgingBucketSummary dataclass instance.
    """
    snapshot: Optional[ArAgingSnapshot] = None

    if not force_recalculate:
        stmt = select(ArAgingSnapshot)
        if account_id:
            stmt = stmt.where(ArAgingSnapshot.account_id == account_id)
        stmt = stmt.order_by(ArAgingSnapshot.snapshot_timestamp.desc())
        snapshot = db.scalars(stmt).first()

    if snapshot is None:
        snapshot = calculate_ar_aging(db, account_id=account_id, save_snapshot=True)

    curr = float(snapshot.current_balance)
    b30 = float(snapshot.bucket_30_balance)
    b60 = float(snapshot.bucket_60_balance)
    b90 = float(snapshot.bucket_90_plus_balance)
    total_out = round(curr + b30 + b60 + b90, 2)

    return ArAgingBucketSummary(
        account_id=snapshot.account_id,
        current_balance=curr,
        bucket_30_balance=b30,
        bucket_60_balance=b60,
        bucket_90_plus_balance=b90,
        total_outstanding=total_out,
        as_of_timestamp=snapshot.snapshot_timestamp.isoformat(),
    )


def record_arr_metric(
    db: Session,
    account_id: str,
    metric_period: str,
    arr_value: Decimal | float | int,
    currency: str = "USD",
) -> ArrMetric:
    """Record a new or updated Annual Recurring Revenue (ARR) metric.

    Args:
        db: Synchronous SQLAlchemy database session.
        account_id: Target account UUID string.
        metric_period: Period identifier (e.g. '2026-Q1').
        arr_value: ARR metric monetary value.
        currency: ISO currency code (default 'USD').

    Returns:
        Created and persisted ArrMetric ORM instance.
    """
    val = Decimal(str(arr_value)) if arr_value is not None else Decimal("0.00")
    metric = ArrMetric(
        account_id=account_id,
        metric_period=metric_period,
        arr_value=val,
        currency=currency.upper() if currency else "USD",
        recorded_at=datetime.now(timezone.utc),
    )
    db.add(metric)
    db.commit()
    db.refresh(metric)
    return metric


def get_arr_trends(
    db: Session,
    account_id: Optional[str] = None,
    period_range: Optional[str] = None,
    target_currency: str = "USD",
) -> List[ArrTrendDataPoint]:
    """Retrieve historical and current ARR trend data points.

    Args:
        db: Synchronous SQLAlchemy database session.
        account_id: Optional account UUID filter.
        period_range: Optional comma-separated list or pattern of metric periods.
        target_currency: Target currency for converted trend outputs.

    Returns:
        List of ArrTrendDataPoint objects ordered by period and timestamp.
    """
    stmt = select(ArrMetric)
    if account_id:
        stmt = stmt.where(ArrMetric.account_id == account_id)

    if period_range:
        periods = [p.strip() for p in period_range.split(",") if p.strip()]
        if periods:
            stmt = stmt.where(ArrMetric.metric_period.in_(periods))

    stmt = stmt.order_by(ArrMetric.metric_period.asc(), ArrMetric.recorded_at.asc())
    metrics = db.scalars(stmt).all()

    trend_points: List[ArrTrendDataPoint] = []
    for m in metrics:
        converted_val = convert_currency_amount(
            m.arr_value,
            from_currency=m.currency,
            to_currency=target_currency,
        )
        point = ArrTrendDataPoint(
            id=m.id,
            account_id=m.account_id,
            metric_period=m.metric_period,
            arr_value=converted_val,
            currency=target_currency.upper(),
            original_currency=m.currency,
            recorded_at=m.recorded_at.isoformat(),
        )
        trend_points.append(point)

    return trend_points


def get_financial_dashboard_data(
    db: Session,
    account_id: Optional[str] = None,
    target_currency: str = "USD",
) -> Dict[str, Any]:
    """Retrieve aggregate financial dashboard data including AR aging and ARR trends.

    Args:
        db: Synchronous SQLAlchemy database session.
        account_id: Optional account UUID filter.
        target_currency: Currency for display values.

    Returns:
        Dictionary containing AR aging summary, ARR trends array, and overall metrics.
    """
    ar_aging = get_ar_aging_summary(db, account_id=account_id)
    arr_trends = get_arr_trends(db, account_id=account_id, target_currency=target_currency)

    total_arr = sum(p.arr_value for p in arr_trends) if arr_trends else 0.0

    return {
        "account_id": account_id or "ALL",
        "ar_aging": ar_aging.to_dict(),
        "arr_trends": [p.to_dict() for p in arr_trends],
        "total_arr": round(total_arr, 2),
        "target_currency": target_currency.upper(),
        "calculated_at": datetime.now(timezone.utc).isoformat(),
    }
