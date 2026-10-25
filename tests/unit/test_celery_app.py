"""Unit tests for Celery background task processing application configuration."""

from src.workers.celery_app import app, celery_app, ping_task


def test_celery_app_instance():
    """Test that celery_app is correctly instantiated and app alias matches."""
    assert celery_app is not None
    assert app is celery_app
    assert celery_app.main == "portal_workers"


def test_celery_app_configuration():
    """Test Celery configuration settings."""
    conf = celery_app.conf
    assert conf.task_serializer == "json"
    assert conf.result_serializer == "json"
    assert conf.accept_content == ["json"]
    assert conf.timezone == "UTC"
    assert conf.enable_utc is True
    assert conf.task_track_started is True
    assert conf.broker_connection_retry_on_startup is True


def test_ping_task():
    """Test ping task execution and result."""
    result = ping_task()
    assert result == "pong"
