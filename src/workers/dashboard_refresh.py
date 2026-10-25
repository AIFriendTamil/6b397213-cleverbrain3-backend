"""Celery background worker task for recalculating and updating dashboard metric caches."""

from datetime import datetime, timezone
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.lib.config import settings
from src.models.base import SessionLocal
from src.services.dashboard.aggregator import (
    update_domain_metric_cache,
    update_historical_snapshot,
)
from src.services.dashboard.permissions import ALL_DOMAINS
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Default domain metric specs for recalculation
DEFAULT_DOMAIN_METRICS: dict[str, list[dict[str, Any]]] = {
    "sales": [
        {
            "metric_key": "pipeline_value",
            "current_value": 1250000.0,
            "unit_type": "currency",
            "baselines": {"prior_month": 1000000.0, "prior_quarter": 950000.0},
        },
        {
            "metric_key": "deals_closed",
            "current_value": 42.0,
            "unit_type": "count",
            "baselines": {"prior_month": 35.0, "prior_quarter": 30.0},
        },
        {
            "metric_key": "win_rate",
            "current_value": 28.5,
            "unit_type": "percentage",
            "baselines": {"prior_month": 25.0, "prior_quarter": 22.0},
        },
    ],
    "leads": [
        {
            "metric_key": "total_leads",
            "current_value": 1420.0,
            "unit_type": "count",
            "baselines": {"prior_month": 1200.0, "prior_quarter": 1100.0},
        },
        {
            "metric_key": "conversion_rate",
            "current_value": 14.2,
            "unit_type": "percentage",
            "baselines": {"prior_month": 12.5, "prior_quarter": 11.0},
        },
        {
            "metric_key": "cost_per_lead",
            "current_value": 35.5,
            "unit_type": "currency",
            "baselines": {"prior_month": 38.0, "prior_quarter": 40.0},
        },
    ],
    "support": [
        {
            "metric_key": "open_tickets",
            "current_value": 18.0,
            "unit_type": "count",
            "baselines": {"prior_month": 24.0, "prior_quarter": 30.0},
        },
        {
            "metric_key": "avg_response_time",
            "current_value": 2.4,
            "unit_type": "count",
            "baselines": {"prior_month": 3.1, "prior_quarter": 3.5},
        },
        {
            "metric_key": "csat_score",
            "current_value": 94.5,
            "unit_type": "percentage",
            "baselines": {"prior_month": 91.0, "prior_quarter": 89.0},
        },
    ],
    "finance": [
        {
            "metric_key": "revenue",
            "current_value": 850000.0,
            "unit_type": "currency",
            "baselines": {"prior_month": 800000.0, "prior_quarter": 750000.0},
        },
        {
            "metric_key": "net_margin",
            "current_value": 22.4,
            "unit_type": "percentage",
            "baselines": {"prior_month": 20.0, "prior_quarter": 18.5},
        },
        {
            "metric_key": "cash_flow",
            "current_value": 310000.0,
            "unit_type": "currency",
            "baselines": {"prior_month": 280000.0, "prior_quarter": 260000.0},
        },
    ],
}


def refresh_domain_metrics(
    db_session: Session,
    domain_name: str,
    metric_items: Optional[list[dict[str, Any]]] = None,
) -> int:
    """Recalculate and update cache entries and historical snapshots for a single domain.

    Args:
        db_session: Synchronous SQLAlchemy session.
        domain_name: Operational domain identifier (e.g. 'sales').
        metric_items: Optional list of metric dictionary specifications.

    Returns:
        Number of updated metric cache entries for the domain.
    """
    clean_domain = domain_name.strip().lower()
    specs = (
        metric_items
        if metric_items is not None
        else DEFAULT_DOMAIN_METRICS.get(clean_domain, [])
    )

    count = 0
    for spec in specs:
        metric_key = spec["metric_key"]
        current_val = float(spec["current_value"])
        unit_type = spec["unit_type"]

        update_domain_metric_cache(
            db_session=db_session,
            domain_name=clean_domain,
            metric_key=metric_key,
            current_value=current_val,
            unit_type=unit_type,
        )
        count += 1

        # Process baseline snapshots if specified
        baselines = spec.get("baselines", {})
        for period_key, snapshot_val in baselines.items():
            update_historical_snapshot(
                db_session=db_session,
                domain_name=clean_domain,
                metric_key=metric_key,
                baseline_period=period_key,
                snapshot_value=float(snapshot_val),
            )

    return count


@celery_app.task(name="src.workers.dashboard_refresh.recalculate_dashboard_metrics")
def recalculate_dashboard_metrics(
    domains: Optional[list[str]] = None,
    metric_overrides: Optional[dict[str, Any]] = None,
    db_session: Optional[Session] = None,
) -> dict[str, Any]:
    """Background Celery task to recalculate and update metric caches across enabled domains.

    Args:
        domains: Optional list of target domain identifiers to refresh.
        metric_overrides: Optional dictionary mapping domain names to custom metric specs.
        db_session: Optional pre-existing synchronous SQLAlchemy session (for testing/direct calls).

    Returns:
        Summary dictionary containing status, processed_domains, metrics_updated, and timestamp.
    """
    logger.info("Starting dashboard metric cache recalculation background task")

    enabled = getattr(settings, "enabled_domains", list(ALL_DOMAINS))
    if domains is not None:
        target_domains = [
            d.strip().lower() for d in domains if d.strip().lower() in ALL_DOMAINS
        ]
    else:
        target_domains = [d for d in enabled if d in ALL_DOMAINS]

    owns_session = False
    session = db_session
    if session is None:
        session = SessionLocal()
        owns_session = True

    processed_domains: list[str] = []
    total_metrics_updated = 0
    domain_errors: dict[str, str] = {}

    try:
        for domain_name in target_domains:
            try:
                override_items = (
                    metric_overrides.get(domain_name)
                    if metric_overrides and domain_name in metric_overrides
                    else None
                )
                updated_count = refresh_domain_metrics(
                    db_session=session,
                    domain_name=domain_name,
                    metric_items=override_items,
                )
                processed_domains.append(domain_name)
                total_metrics_updated += updated_count
            except Exception as exc:
                logger.error(
                    "Error recalculating metrics for domain '%s': %s",
                    domain_name,
                    exc,
                    exc_info=True,
                )
                domain_errors[domain_name] = str(exc)
                if session and session.is_active:
                    session.rollback()

        status_str = "completed" if not domain_errors else "completed_with_errors"
        completion_iso = datetime.now(timezone.utc).isoformat()

        logger.info(
            "Completed dashboard metric cache recalculation: %d metrics updated across %d domains (%s)",
            total_metrics_updated,
            len(processed_domains),
            status_str,
        )

        result: dict[str, Any] = {
            "status": status_str,
            "processed_domains": processed_domains,
            "metrics_updated": total_metrics_updated,
            "timestamp": completion_iso,
        }
        if domain_errors:
            result["errors"] = domain_errors

        return result

    finally:
        if owns_session and session:
            session.close()
