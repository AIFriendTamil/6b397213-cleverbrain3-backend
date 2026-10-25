"""Metric aggregation, directional trend math, empty state handling, and domain error isolation."""

from datetime import datetime, timezone
import logging
from typing import Any, Iterable, Optional, Union

from sqlalchemy.orm import Session

from src.lib.config import settings
from src.models.dashboard import DashboardHistoricalSnapshot, DashboardMetricCache
from src.services.dashboard.permissions import (
    ALL_DOMAINS,
    DOMAIN_FINANCE,
    DOMAIN_LEADS,
    DOMAIN_SALES,
    DOMAIN_SUPPORT,
    get_authorized_domains,
)

logger = logging.getLogger(__name__)

DOMAIN_DISPLAY_NAMES: dict[str, str] = {
    DOMAIN_SALES: "Sales Pipeline",
    DOMAIN_LEADS: "Lead Conversion",
    DOMAIN_SUPPORT: "Customer Support",
    DOMAIN_FINANCE: "Finance Overview",
}

DOMAIN_DETAIL_URLS: dict[str, str] = {
    DOMAIN_SALES: "/sales",
    DOMAIN_LEADS: "/leads",
    DOMAIN_SUPPORT: "/support",
    DOMAIN_FINANCE: "/finance",
}

KPI_METADATA_MAP: dict[str, dict[str, str]] = {
    "pipeline_value": {"label": "Pipeline Value", "unit": "currency"},
    "deals_closed": {"label": "Deals Closed", "unit": "count"},
    "win_rate": {"label": "Win Rate", "unit": "percentage"},
    "total_leads": {"label": "Total Leads", "unit": "count"},
    "conversion_rate": {"label": "Conversion Rate", "unit": "percentage"},
    "cost_per_lead": {"label": "Cost Per Lead", "unit": "currency"},
    "open_tickets": {"label": "Open Tickets", "unit": "count"},
    "avg_response_time": {"label": "Avg Response Time (hrs)", "unit": "count"},
    "csat_score": {"label": "CSAT Score", "unit": "percentage"},
    "revenue": {"label": "Total Revenue", "unit": "currency"},
    "net_margin": {"label": "Net Margin", "unit": "percentage"},
    "cash_flow": {"label": "Operating Cash Flow", "unit": "currency"},
}


def calculate_trend(
    current_value: Optional[float],
    baseline_value: Optional[float],
) -> tuple[str, float]:
    """Calculate directional trend and percentage change relative to baseline snapshot.

    Args:
        current_value: Current metric numeric value.
        baseline_value: Historical baseline metric value.

    Returns:
        Tuple of (trend_direction, trend_percentage) where direction is 'up', 'down', or 'flat'
        and percentage is a rounded float.
    """
    if current_value is None or baseline_value is None:
        return "flat", 0.0

    curr = float(current_value)
    base = float(baseline_value)

    if base == 0.0:
        if curr > 0.0:
            return "up", 100.0
        elif curr < 0.0:
            return "down", -100.0
        else:
            return "flat", 0.0

    diff = curr - base
    raw_pct = (diff / abs(base)) * 100.0
    trend_pct = round(raw_pct, 2)

    if trend_pct > 0.0:
        direction = "up"
    elif trend_pct < 0.0:
        direction = "down"
    else:
        direction = "flat"

    return direction, trend_pct


def get_kpi_label(metric_key: str) -> str:
    """Get human-readable display label for a metric key."""
    if metric_key in KPI_METADATA_MAP:
        return KPI_METADATA_MAP[metric_key]["label"]
    return metric_key.replace("_", " ").title()


def update_domain_metric_cache(
    db_session: Session,
    domain_name: str,
    metric_key: str,
    current_value: float,
    unit_type: str,
) -> DashboardMetricCache:
    """Insert or update a metric cache entry in the database.

    Args:
        db_session: Synchronous SQLAlchemy session.
        domain_name: Domain name (e.g., 'sales').
        metric_key: Metric identifier (e.g., 'pipeline_value').
        current_value: Numerical metric value.
        unit_type: Formatting unit type ('currency', 'percentage', 'count').

    Returns:
        Updated or newly created DashboardMetricCache instance.
    """
    clean_domain = domain_name.strip().lower()
    clean_key = metric_key.strip().lower()

    existing = (
        db_session.query(DashboardMetricCache)
        .filter(
            DashboardMetricCache.domain_name == clean_domain,
            DashboardMetricCache.metric_key == clean_key,
        )
        .first()
    )

    if existing:
        existing.current_value = float(current_value)
        existing.unit_type = unit_type
        existing.updated_at = datetime.now(timezone.utc)
        record = existing
    else:
        record = DashboardMetricCache(
            domain_name=clean_domain,
            metric_key=clean_key,
            current_value=float(current_value),
            unit_type=unit_type,
            updated_at=datetime.now(timezone.utc),
        )
        db_session.add(record)

    db_session.commit()
    db_session.refresh(record)
    return record


def update_historical_snapshot(
    db_session: Session,
    domain_name: str,
    metric_key: str,
    baseline_period: str,
    snapshot_value: float,
) -> DashboardHistoricalSnapshot:
    """Insert or update a historical baseline snapshot entry in the database.

    Args:
        db_session: Synchronous SQLAlchemy session.
        domain_name: Domain name (e.g., 'sales').
        metric_key: Metric identifier.
        baseline_period: Comparison period (e.g., 'prior_month', 'prior_quarter').
        snapshot_value: Baseline metric numerical value.

    Returns:
        Updated or newly created DashboardHistoricalSnapshot instance.
    """
    clean_domain = domain_name.strip().lower()
    clean_key = metric_key.strip().lower()
    clean_period = baseline_period.strip().lower()

    existing = (
        db_session.query(DashboardHistoricalSnapshot)
        .filter(
            DashboardHistoricalSnapshot.domain_name == clean_domain,
            DashboardHistoricalSnapshot.metric_key == clean_key,
            DashboardHistoricalSnapshot.baseline_period == clean_period,
        )
        .first()
    )

    if existing:
        existing.snapshot_value = float(snapshot_value)
        existing.captured_at = datetime.now(timezone.utc)
        record = existing
    else:
        record = DashboardHistoricalSnapshot(
            domain_name=clean_domain,
            metric_key=clean_key,
            baseline_period=clean_period,
            snapshot_value=float(snapshot_value),
            captured_at=datetime.now(timezone.utc),
        )
        db_session.add(record)

    db_session.commit()
    db_session.refresh(record)
    return record


def aggregate_domain_metrics(
    db_session: Session,
    domain_name: str,
    baseline_period: str = "prior_month",
) -> dict[str, Any]:
    """Aggregate metrics for a single operational domain with error isolation and empty state handling.

    Args:
        db_session: Synchronous SQLAlchemy session.
        domain_name: Operational domain identifier (e.g., 'sales').
        baseline_period: Historical comparison period window.

    Returns:
        Dictionary containing domain metadata, status, detail_url, and list of KPI objects.
    """
    clean_domain = domain_name.strip().lower()
    display_name = DOMAIN_DISPLAY_NAMES.get(
        clean_domain, clean_domain.replace("_", " ").title()
    )
    detail_url = DOMAIN_DETAIL_URLS.get(clean_domain, f"/{clean_domain}")

    # Check feature flag configuration
    enabled_domains = getattr(settings, "enabled_domains", list(ALL_DOMAINS))
    if clean_domain not in enabled_domains:
        return {
            "domain_name": clean_domain,
            "display_name": display_name,
            "status": "unconfigured",
            "detail_url": detail_url,
            "kpis": [],
        }

    try:
        cache_records = (
            db_session.query(DashboardMetricCache)
            .filter(DashboardMetricCache.domain_name == clean_domain)
            .all()
        )

        if not cache_records:
            return {
                "domain_name": clean_domain,
                "display_name": display_name,
                "status": "no_data",
                "detail_url": detail_url,
                "kpis": [],
            }

        # Query baseline snapshots
        snapshots = (
            db_session.query(DashboardHistoricalSnapshot)
            .filter(
                DashboardHistoricalSnapshot.domain_name == clean_domain,
                DashboardHistoricalSnapshot.baseline_period == baseline_period,
            )
            .all()
        )
        snapshot_map = {s.metric_key: s.snapshot_value for s in snapshots}

        kpis = []
        for record in cache_records:
            baseline_val = snapshot_map.get(record.metric_key)
            direction, pct = calculate_trend(record.current_value, baseline_val)
            label = get_kpi_label(record.metric_key)

            kpis.append(
                {
                    "key": record.metric_key,
                    "label": label,
                    "value": record.current_value,
                    "unit": record.unit_type,
                    "trend_direction": direction,
                    "trend_percentage": pct,
                }
            )

        status = "active" if kpis else "no_data"

        return {
            "domain_name": clean_domain,
            "display_name": display_name,
            "status": status,
            "detail_url": detail_url,
            "kpis": kpis,
        }

    except Exception as exc:
        logger.error(
            "Domain error isolated: failed to aggregate metrics for domain '%s': %s",
            clean_domain,
            exc,
            exc_info=True,
        )
        return {
            "domain_name": clean_domain,
            "display_name": display_name,
            "status": "error",
            "detail_url": detail_url,
            "kpis": [],
        }


def aggregate_dashboard_metrics(
    db_session: Session,
    baseline_period: str = "prior_month",
    roles: Optional[Union[str, Iterable[str]]] = None,
    explicit_domains: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Aggregate consolidated executive dashboard metrics across all authorized domains.

    Enforces role-based domain filtering, per-domain error isolation, directional trend math,
    and empty state handling.

    Args:
        db_session: Synchronous SQLAlchemy DB session.
        baseline_period: Baseline snapshot window (default 'prior_month').
        roles: User role string or collection of role strings.
        explicit_domains: Explicit authorized domain overrides.

    Returns:
        Structured dictionary with 'last_updated_at' timestamp and list of authorized domain 'metrics'.
    """
    # 1. Determine authorized domains for the requesting user/roles
    if roles is not None or explicit_domains is not None:
        target_domains = get_authorized_domains(
            roles=roles, explicit_domains=explicit_domains
        )
    else:
        target_domains = set(ALL_DOMAINS)

    # Maintain canonical domain ordering: sales, leads, support, finance
    canonical_order = [DOMAIN_SALES, DOMAIN_LEADS, DOMAIN_SUPPORT, DOMAIN_FINANCE]
    ordered_domains = [d for d in canonical_order if d in target_domains]
    for d in sorted(target_domains):
        if d not in ordered_domains:
            ordered_domains.append(d)

    domain_results: list[dict[str, Any]] = []
    latest_timestamp: Optional[datetime] = None

    # 2. Query overall latest cache updated_at timestamp across authorized domains
    try:
        latest_record = (
            db_session.query(DashboardMetricCache)
            .filter(DashboardMetricCache.domain_name.in_(ordered_domains))
            .order_by(DashboardMetricCache.updated_at.desc())
            .first()
        )
        if latest_record and latest_record.updated_at:
            latest_timestamp = latest_record.updated_at
    except Exception as exc:
        logger.warning("Failed to fetch latest metric cache timestamp: %s", exc)

    # 3. Aggregate each domain with isolated error boundary
    for domain_name in ordered_domains:
        domain_data = aggregate_domain_metrics(
            db_session=db_session,
            domain_name=domain_name,
            baseline_period=baseline_period,
        )
        domain_results.append(domain_data)

    last_updated_iso = (
        latest_timestamp.isoformat()
        if latest_timestamp
        else datetime.now(timezone.utc).isoformat()
    )

    return {
        "last_updated_at": last_updated_iso,
        "metrics": domain_results,
    }
