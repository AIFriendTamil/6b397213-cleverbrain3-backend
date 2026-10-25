"""Unit tests for health check API endpoint in backend/src/api/health.py."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DBSession

from src.api.health import router as health_router
from src.models.base import Base, create_db_engine, get_db


@pytest.fixture
def test_db():
    """Fixture providing an isolated in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with DBSession(engine) as session:
        yield session


@pytest.fixture
def client(test_db):
    """Fixture providing a FastAPI TestClient with database session dependency overridden."""
    app = FastAPI()
    app.include_router(health_router)

    def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_health_check_healthy(client):
    """Verify health check returns 200 OK with healthy status when DB and Redis probes succeed."""
    with patch("src.api.health._check_redis", return_value="connected"):
        res = client.get("/api/v1/health")
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["status"] == "healthy"
        assert data["database"] == "connected"
        assert data["redis"] == "connected"


def test_health_check_db_failure(client, test_db):
    """Verify health check returns 503 Service Unavailable with unhealthy status when DB query fails."""
    mock_db = MagicMock(spec=DBSession)
    mock_db.execute.side_effect = SQLAlchemyError("Database connection lost")

    app = FastAPI()
    app.include_router(health_router)
    app.dependency_overrides[get_db] = lambda: mock_db

    with patch("src.api.health._check_redis", return_value="connected"):
        with TestClient(app) as mock_client:
            res = mock_client.get("/api/v1/health")
            assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
            data = res.json()
            assert data["status"] == "unhealthy"
            assert data["database"] == "disconnected"


def test_health_check_redis_failure(client):
    """Verify health check returns 503 Service Unavailable when Redis probe fails."""
    with patch("src.api.health._check_redis", return_value="disconnected"):
        res = client.get("/api/v1/health")
        assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        data = res.json()
        assert data["status"] == "unhealthy"
        assert data["redis"] == "disconnected"
