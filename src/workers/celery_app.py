"""Celery background task processing application initialization and configuration."""

from celery import Celery

from src.lib.config import settings

# Initialize Celery application instance with Redis broker and result backend
celery_app = Celery(
    "portal_workers",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

# Configure task serialization, timezones, beat schedule, and connection behavior
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    imports=[
        "src.workers.dashboard_refresh",
        "src.workers.clm_tasks",
        "src.workers.escalation_worker",
        "src.workers.sla_tasks",
        "src.workers.notification_tasks",
    ],
    beat_schedule={
        "recalculate-dashboard-metrics-periodic": {
            "task": "src.workers.dashboard_refresh.recalculate_dashboard_metrics",
            "schedule": float(settings.DASHBOARD_REFRESH_INTERVAL_SECONDS),
        },
        "evaluate-contract-expirations-daily": {
            "task": "src.workers.clm_tasks.evaluate_contract_expirations",
            "schedule": 86400.0,
        },
        "evaluate-overdue-tasks-periodic": {
            "task": "src.workers.escalation_worker.evaluate_overdue_tasks",
            "schedule": 3600.0,
        },
        "evaluate-sla-breaches-periodic": {
            "task": "src.workers.sla_tasks.evaluate_sla_breaches",
            "schedule": 60.0,
        },
    },
)


@celery_app.task(name="tasks.ping")
def ping_task() -> str:
    """Health check task to verify Celery task execution."""
    return "pong"


# Alias for standard Celery CLI auto-discovery
app = celery_app
