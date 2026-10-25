"""Dashboard API endpoint routes for metrics aggregation, baseline filtering, and RBAC pruning."""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from src.api.deps import get_current_session
from src.models.base import get_db
from src.models.session import Session as SessionModel
from src.services.dashboard.aggregator import aggregate_dashboard_metrics
from src.services.dashboard.permissions import (
    DashboardPermissionDeniedError,
    check_dashboard_access,
    get_authorized_domains,
)
from src.workers.dashboard_refresh import recalculate_dashboard_metrics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


# --- Response Schemas ---


class KPIData(BaseModel):
    """Schema for individual KPI metric data points."""

    key: str = Field(..., description="Metric key identifier", examples=["pipeline_value"])
    label: str = Field(..., description="Human-readable metric label", examples=["Pipeline Value"])
    value: float = Field(..., description="Current numerical metric value", examples=[150000.0])
    unit: str = Field(..., description="Formatting unit type", examples=["currency"])
    trend_direction: str = Field(
        ..., description="Directional trend ('up', 'down', 'flat')", examples=["up"]
    )
    trend_percentage: float = Field(
        ..., description="Calculated percentage change vs baseline", examples=[20.0]
    )


class DomainMetrics(BaseModel):
    """Schema for operational domain aggregated metrics."""

    domain_name: str = Field(
        ..., description="Operational domain identifier", examples=["sales"]
    )
    display_name: str = Field(
        ..., description="Human-readable domain title", examples=["Sales Pipeline"]
    )
    status: str = Field(
        ...,
        description="Domain operational status ('active', 'no_data', 'error', 'unconfigured')",
        examples=["active"],
    )
    detail_url: str = Field(
        ..., description="Deep-link target URL", examples=["/sales"]
    )
    kpis: list[KPIData] = Field(
        default_factory=list, description="List of domain KPI objects"
    )


class DashboardMetricsResponse(BaseModel):
    """Response payload for GET /api/v1/dashboard/metrics endpoint."""

    last_updated_at: str = Field(
        ...,
        description="ISO 8601 timestamp of metric cache calculation",
        examples=["2026-10-24T12:00:00+00:00"],
    )
    metrics: list[DomainMetrics] = Field(
        default_factory=list, description="Array of authorized domain metric objects"
    )


class RefreshDashboardRequest(BaseModel):
    """Optional request payload for POST /api/v1/dashboard/refresh endpoint."""

    domains: Optional[list[str]] = Field(
        None,
        description="Optional list of target operational domain names to recalculate",
        examples=[["sales", "leads"]],
    )


class DashboardRefreshResponse(BaseModel):
    """Response payload for POST /api/v1/dashboard/refresh endpoint."""

    status: str = Field(
        ...,
        description="Execution status string indicator",
        examples=["refresh_initiated"],
    )
    job_id: str = Field(
        ...,
        description="Background Celery task execution string ID",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )


# --- Endpoint Handlers ---


@router.get(
    "/metrics",
    response_model=DashboardMetricsResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "Successfully retrieved dashboard metrics",
            "model": DashboardMetricsResponse,
        },
        401: {
            "description": "Authentication required or session invalid",
        },
        403: {
            "description": "Access denied - no authorized operational domains",
        },
    },
    summary="Get executive dashboard metrics",
    description=(
        "Retrieves consolidated KPI metrics and directional trends across operational domains "
        "authorized for the calling user's role."
    ),
)
def get_dashboard_metrics(
    baseline: str = Query(
        "prior_month",
        description="Comparison baseline period for trend calculation (e.g., 'prior_month', 'prior_quarter')",
    ),
    roles: Optional[str] = Query(
        None,
        description="Optional role filter override (comma-separated string)",
    ),
    x_user_roles: Optional[str] = Header(
        None,
        alias="X-User-Roles",
        description="User roles passed in HTTP headers",
    ),
    x_user_role: Optional[str] = Header(
        None,
        alias="X-User-Role",
        description="Single user role passed in HTTP headers",
    ),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    """Retrieve aggregated dashboard metrics with baseline filtering and RBAC pruning.

    Args:
        baseline: Baseline snapshot window name (default: 'prior_month').
        roles: Query parameter for explicit role filtering.
        x_user_roles: Header containing user roles.
        x_user_role: Header containing single user role.
        session: Active user session dependency.
        db: Synchronous SQLAlchemy database session.

    Returns:
        Structured dictionary containing 'last_updated_at' and list of authorized domain 'metrics'.

    Raises:
        HTTPException: HTTP 401 if unauthenticated, HTTP 403 if user has no authorized domains.
    """
    # 1. Determine effective user roles from query params, headers, or session/user context
    effective_roles = roles or x_user_roles or x_user_role
    if effective_roles is None and session.user:
        if hasattr(session.user, "roles") and getattr(session.user, "roles"):
            effective_roles = getattr(session.user, "roles")
        elif hasattr(session.user, "role") and getattr(session.user, "role"):
            effective_roles = getattr(session.user, "role")

    # 2. Check RBAC permissions
    if effective_roles is not None:
        authorized_domains = get_authorized_domains(roles=effective_roles)
        if not authorized_domains:
            logger.warning(
                "RBAC access denied for user %s with roles '%s': no authorized domains",
                session.user_id if session else "unknown",
                effective_roles,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FORBIDDEN",
                    "message": f"User roles '{effective_roles}' have no authorized dashboard domains.",
                    "details": {},
                },
            )

    # 3. Aggregate dashboard metrics for authorized domains
    try:
        metrics_data = aggregate_dashboard_metrics(
            db_session=db,
            baseline_period=baseline,
            roles=effective_roles,
        )
        return metrics_data
    except DashboardPermissionDeniedError as err:
        logger.warning("Permission denied during metric aggregation: %s", err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )


@router.post(
    "/refresh",
    response_model=DashboardRefreshResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {
            "description": "Background metric cache recalculation task initiated successfully",
            "model": DashboardRefreshResponse,
        },
        401: {
            "description": "Authentication required or session invalid",
        },
        403: {
            "description": "Access denied - no authorized operational domains",
        },
    },
    summary="Trigger dashboard metric cache refresh",
    description=(
        "Triggers an immediate background Celery cache refresh task to recalculate "
        "and update metric cache entries for authorized operational domains."
    ),
)
def trigger_dashboard_refresh(
    request_body: Optional[RefreshDashboardRequest] = Body(None),
    roles: Optional[str] = Query(
        None,
        description="Optional role filter override (comma-separated string)",
    ),
    x_user_roles: Optional[str] = Header(
        None,
        alias="X-User-Roles",
        description="User roles passed in HTTP headers",
    ),
    x_user_role: Optional[str] = Header(
        None,
        alias="X-User-Role",
        description="Single user role passed in HTTP headers",
    ),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    """Trigger an immediate background metric cache recalculation task.

    Args:
        request_body: Optional request payload with target domains.
        roles: Query parameter for explicit role filtering.
        x_user_roles: Header containing user roles.
        x_user_role: Header containing single user role.
        session: Active user session dependency.
        db: Synchronous SQLAlchemy database session.

    Returns:
        Structured dictionary containing 'status' ("refresh_initiated") and 'job_id'.

    Raises:
        HTTPException: HTTP 401 if unauthenticated, HTTP 403 if user has no authorized domains.
    """
    # 1. Determine effective user roles
    effective_roles = roles or x_user_roles or x_user_role
    if effective_roles is None and session.user:
        if hasattr(session.user, "roles") and getattr(session.user, "roles"):
            effective_roles = getattr(session.user, "roles")
        elif hasattr(session.user, "role") and getattr(session.user, "role"):
            effective_roles = getattr(session.user, "role")

    # 2. Check RBAC permissions and determine authorized target domains
    target_domains: Optional[list[str]] = None
    if effective_roles is not None:
        authorized_domains = get_authorized_domains(roles=effective_roles)
        if not authorized_domains:
            logger.warning(
                "RBAC access denied for refresh trigger by user %s with roles '%s': no authorized domains",
                session.user_id if session else "unknown",
                effective_roles,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FORBIDDEN",
                    "message": f"User roles '{effective_roles}' have no authorized dashboard domains.",
                    "details": {},
                },
            )
        target_domains = list(authorized_domains)

    # Filter requested domains from body if present
    if request_body and request_body.domains:
        requested = [d.strip().lower() for d in request_body.domains if d and d.strip()]
        if target_domains is not None:
            target_domains = [d for d in requested if d in target_domains]
            if not target_domains:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "FORBIDDEN",
                        "message": "None of the requested domains are authorized for your roles.",
                        "details": {},
                    },
                )
        else:
            target_domains = requested

    # 3. Trigger background Celery task
    try:
        task_result = recalculate_dashboard_metrics.delay(domains=target_domains)
        job_id = (
            str(task_result.id)
            if (task_result and getattr(task_result, "id", None))
            else "refresh-job-initiated"
        )
    except Exception as exc:
        logger.warning(
            "Celery task dispatch warning for refresh trigger, falling back: %s", exc
        )
        try:
            # Fallback synchronous execution if Celery broker unavailable
            recalculate_dashboard_metrics(domains=target_domains, db_session=db)
            job_id = "sync-refresh-fallback-id"
        except Exception as inner_exc:
            logger.error("Failed fallback refresh execution: %s", inner_exc)
            job_id = "job-dispatch-failed"

    return {
        "status": "refresh_initiated",
        "job_id": job_id,
    }
