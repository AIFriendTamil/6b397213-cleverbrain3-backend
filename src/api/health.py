"""Synthetic operational health check API endpoint monitoring DB and Redis connectivity."""

import logging

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
import redis
from sqlalchemy import text
from sqlalchemy.orm import Session as DBSession

from src.lib.config import settings
from src.models.base import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/health", tags=["health"])


class HealthCheckResponse(BaseModel):
    """Response payload for system health check endpoint."""

    status: str = Field(
        ...,
        description="Overall system operational status",
        examples=["healthy", "unhealthy"],
    )
    database: str = Field(
        ...,
        description="Database connection reachability status",
        examples=["connected", "disconnected"],
    )
    redis: str = Field(
        ...,
        description="Redis connection reachability status",
        examples=["connected", "disconnected"],
    )


def _check_database(db: DBSession) -> str:
    """Execute synthetic database probe query."""
    try:
        db.execute(text("SELECT 1"))
        return "connected"
    except Exception as err:
        logger.error("Health check database probe failed: %s", err)
        return "disconnected"


def _check_redis() -> str:
    """Execute synthetic Redis ping probe."""
    try:
        client = redis.from_url(settings.REDIS_URL, socket_timeout=1.0)
        if client.ping():
            try:
                client.close()
            except Exception:
                pass
            return "connected"
    except Exception as err:
        logger.error("Health check Redis probe failed: %s", err)
    return "disconnected"


@router.get(
    "",
    response_model=HealthCheckResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "System, database, and Redis are healthy",
            "model": HealthCheckResponse,
        },
        503: {
            "description": "Database or Redis connection is unreachable or unhealthy",
            "model": HealthCheckResponse,
        },
    },
    summary="Synthetic health check endpoint",
)
def health_check(
    response: Response,
    db: DBSession = Depends(get_db),
) -> HealthCheckResponse:
    """Execute synthetic database and Redis probes and return operational status."""
    db_status = _check_database(db)
    redis_status = _check_redis()

    if db_status == "connected" and redis_status == "connected":
        overall_status = "healthy"
        response.status_code = status.HTTP_200_OK
    else:
        overall_status = "unhealthy"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthCheckResponse(
        status=overall_status,
        database=db_status,
        redis=redis_status,
    )
