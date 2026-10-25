"""Unit tests for FastAPI application instantiation and lifespan management in backend/src/api/main.py."""

import signal
from unittest.mock import patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from src.api.main import app, register_signal_handlers
from src.models.base import Base, create_db_engine, get_db


@pytest.fixture
def client():
    """Fixture providing a TestClient for the main FastAPI application."""
    with TestClient(app) as test_client:
        yield test_client


def test_app_health_endpoint(client):
    """Verify health endpoint responds with 200 OK via main application."""
    with patch("src.api.health._check_redis", return_value="connected"):
        response = client.get("/api/v1/health")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database"] == "connected"
        assert data["redis"] == "connected"


def test_trace_id_middleware(client):
    """Verify trace ID middleware generates and attaches X-Trace-ID header to responses."""
    with patch("src.api.health._check_redis", return_value="connected"):
        response = client.get("/api/v1/health", headers={"X-Trace-ID": "test-trace-12345"})
        assert response.status_code == status.HTTP_200_OK
        assert response.headers.get("X-Trace-ID") == "test-trace-12345"

        response_gen = client.get("/api/v1/health")
        assert "X-Trace-ID" in response_gen.headers
        assert len(response_gen.headers["X-Trace-ID"]) > 0


def test_cors_middleware(client):
    """Verify CORS middleware responds with correct access control headers."""
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type",
    }
    response = client.options("/api/v1/auth/register", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_register_signal_handlers():
    """Verify signal handlers registration catches SIGTERM and SIGINT without raising exceptions."""
    with patch("signal.signal") as mock_signal:
        register_signal_handlers()
        assert mock_signal.call_count == 2
        calls = [call[0][0] for call in mock_signal.call_args_list]
        assert signal.SIGTERM in calls
        assert signal.SIGINT in calls


@pytest.mark.asyncio
async def test_lifespan_startup_and_shutdown():
    """Verify lifespan context manager executes schema creation and engine disposal."""
    from src.api.main import lifespan, app

    with patch("src.api.main.Base.metadata.create_all") as mock_create_all, patch(
        "src.api.main.engine.dispose"
    ) as mock_dispose:
        async with lifespan(app):
            mock_create_all.assert_called_once()
        mock_dispose.assert_called_once()


def test_dashboard_orm_metadata_registered():
    """Verify dashboard ORM metadata models are registered on Base.metadata."""
    table_names = list(Base.metadata.tables.keys())
    assert "dashboard_metric_cache" in table_names
    assert "dashboard_historical_snapshots" in table_names


def test_clm_orm_metadata_registered():
    """Verify CLM ORM metadata models are registered on Base.metadata."""
    table_names = list(Base.metadata.tables.keys())
    clm_tables = [
        "contracts",
        "clause_highlights",
        "sla_commitments",
        "document_versions",
        "amendment_audit_logs",
        "esignature_requests",
        "renewal_notification_logs",
    ]
    for table_name in clm_tables:
        assert table_name in table_names


def test_ticket_orm_metadata_registered():
    """Verify ticket and SLA ORM metadata models are registered on Base.metadata."""
    table_names = list(Base.metadata.tables.keys())
    ticket_tables = [
        "tickets",
        "ticket_events",
        "csat_responses",
    ]
    for table_name in ticket_tables:
        assert table_name in table_names


def test_ticket_routes_registered():
    """Verify ticket and report API routers are registered on the application instance."""
    routes = [route.path for route in app.routes]
    assert "/api/v1/tickets" in routes
    assert "/api/v1/tickets/{id}" in routes
    assert "/api/v1/tickets/{id}/replies" in routes
    assert "/api/v1/tickets/{id}/status" in routes
    assert "/api/v1/tickets/{id}/csat" in routes
    assert "/api/v1/reports/performance" in routes


def test_kb_orm_metadata_registered():
    """Verify Knowledge Base ORM metadata models are registered on Base.metadata."""
    table_names = list(Base.metadata.tables.keys())
    kb_tables = [
        "kb_categories",
        "kb_resources",
        "kb_tutorial_steps",
        "kb_bookmarks",
        "kb_user_progress",
    ]
    for table_name in kb_tables:
        assert table_name in table_names


def test_kb_routes_registered():
    """Verify Knowledge Base API router endpoints are registered on the application instance."""
    routes = [route.path for route in app.routes]
    assert "/api/v1/kb/resources" in routes
    assert "/api/v1/kb/resources/{id}" in routes
    assert "/api/v1/kb/categories" in routes
    assert "/api/v1/kb/bookmarks" in routes
    assert "/api/v1/kb/bookmarks/{resource_id}" in routes
    assert "/api/v1/kb/progress" in routes


def test_src_main_entrypoint_reexports():
    """Verify backend/src/main.py re-exports app, create_app, lifespan, and register_signal_handlers."""
    import src.main as main_mod

    assert hasattr(main_mod, "app")
    assert hasattr(main_mod, "create_app")
    assert hasattr(main_mod, "lifespan")
    assert hasattr(main_mod, "register_signal_handlers")

