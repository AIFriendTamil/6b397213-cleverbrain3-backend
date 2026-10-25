"""FastAPI endpoint router and Pydantic validation schemas for operational performance and CSAT reporting queries."""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from src.api.deps import get_current_session
from src.models.base import get_db
from src.models.session import Session as SessionModel
from src.models.ticket import TicketSeverity
from src.services.escalation.reporting_service import generate_performance_report

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


# --- Schemas ---


class MTTRMetricsSchema(BaseModel):
    """Pydantic schema for Mean Time To Resolution (MTTR) metrics."""

    total_resolved_tickets: int = Field(..., ge=0, description="Total number of resolved or closed support tickets")
    average_seconds: float = Field(..., ge=0.0, description="Mean time to resolution in seconds")
    average_hours: float = Field(..., ge=0.0, description="Mean time to resolution in hours")
    formatted_mttr: str = Field(..., description="Formatted MTTR duration string (e.g. '1d 2h 30m')")

    model_config = ConfigDict(from_attributes=True)


class SLAMetricsSchema(BaseModel):
    """Pydantic schema for SLA compliance metrics."""

    total_tickets: int = Field(..., ge=0, description="Total tickets evaluated for SLA compliance")
    compliant_tickets: int = Field(..., ge=0, description="Number of tickets meeting SLA requirements")
    breached_tickets: int = Field(..., ge=0, description="Number of tickets breaching SLA requirements")
    compliance_percentage: float = Field(..., ge=0.0, le=100.0, description="SLA compliance percentage rate")

    model_config = ConfigDict(from_attributes=True)


class CSATMetricsSchema(BaseModel):
    """Pydantic schema for Customer Satisfaction (CSAT) survey metrics."""

    total_responses: int = Field(..., ge=0, description="Total CSAT survey responses submitted")
    average_rating: float = Field(..., ge=0.0, le=5.0, description="Average CSAT rating score (1 to 5 scale)")
    satisfied_count: int = Field(..., ge=0, description="Number of satisfied survey responses (rating >= 4)")
    satisfaction_percentage: float = Field(..., ge=0.0, le=100.0, description="Percentage of satisfied survey responses")
    rating_distribution: Dict[int, int] = Field(..., description="Distribution count of ratings for scores 1 through 5")

    model_config = ConfigDict(from_attributes=True)


class SeverityPerformanceSchema(BaseModel):
    """Pydantic schema for severity-level performance breakdown."""

    total_tickets: int = Field(..., ge=0, description="Total tickets within this severity level")
    mttr: MTTRMetricsSchema = Field(..., description="MTTR metrics for this severity level")
    sla_compliance: SLAMetricsSchema = Field(..., description="SLA compliance metrics for this severity level")
    csat: CSATMetricsSchema = Field(..., description="CSAT metrics for this severity level")

    model_config = ConfigDict(from_attributes=True)


class PerformanceReportResponseSchema(BaseModel):
    """Pydantic schema for aggregate operational performance report."""

    start_time: Optional[str] = Field(None, description="Start timestamp cutoff string (ISO 8601)")
    end_time: Optional[str] = Field(None, description="End timestamp cutoff string (ISO 8601)")
    organization_id: Optional[str] = Field(None, description="Organization ID filter applied")
    total_tickets: int = Field(..., ge=0, description="Total support tickets evaluated")
    open_tickets: int = Field(..., ge=0, description="Total open support tickets")
    resolved_tickets: int = Field(..., ge=0, description="Total resolved/closed support tickets")
    mttr: MTTRMetricsSchema = Field(..., description="Aggregate Mean Time To Resolution (MTTR) metrics")
    sla_compliance: SLAMetricsSchema = Field(..., description="Aggregate SLA compliance metrics")
    csat: CSATMetricsSchema = Field(..., description="Aggregate Customer Satisfaction (CSAT) survey metrics")
    severity_breakdown: Dict[str, SeverityPerformanceSchema] = Field(
        ..., description="Performance metrics broken down by severity level (P1, P2, P3)"
    )

    model_config = ConfigDict(from_attributes=True)


# --- Helper Functions ---


def check_tenant_boundary(target_org_id: Optional[str], session: SessionModel) -> str:
    """Verify tenant isolation boundary between active session and requested organization.

    Args:
        target_org_id: Requested target organization ID.
        session: Active user session.

    Returns:
        Validated target organization ID string.

    Raises:
        HTTPException: 403 Forbidden if tenant isolation boundary is breached.
    """
    if not target_org_id:
        return session.tenant_id

    org_clean = target_org_id.strip()
    session_org = (session.tenant_id or "").strip()

    if session_org.lower() in {"*", "admin", "superadmin", "all", "system"}:
        return org_clean

    if org_clean != session_org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "TENANT_MISMATCH",
                "message": f"Access denied: Session tenant '{session_org}' cannot access organization '{org_clean}'.",
                "details": {
                    "session_tenant_id": session_org,
                    "target_tenant_id": org_clean,
                },
            },
        )
    return org_clean


def _validate_date_range(
    start_time: Optional[datetime],
    end_time: Optional[datetime],
) -> None:
    """Validate start and end timestamp range query parameters at the API boundary."""
    if start_time and end_time:
        st = start_time.replace(tzinfo=timezone.utc) if start_time.tzinfo is None else start_time
        et = end_time.replace(tzinfo=timezone.utc) if end_time.tzinfo is None else end_time
        if st > et:
            logger.warning(
                "Invalid timestamp range query: start_time (%s) > end_time (%s)",
                start_time,
                end_time,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_DATE_RANGE",
                    "message": "start_time cannot be greater than end_time",
                    "details": {
                        "start_time": start_time.isoformat(),
                        "end_time": end_time.isoformat(),
                    },
                },
            )


# --- Endpoints ---


@router.get(
    "/performance",
    response_model=PerformanceReportResponseSchema,
    summary="Query operational performance metrics",
    description=(
        "Retrieve Mean Time To Resolution (MTTR), SLA compliance percentages, "
        "aggregate CSAT survey statistics, and severity breakdown for support tickets."
    ),
)
def get_performance_report_endpoint(
    start_time: Optional[datetime] = Query(
        None, description="Start timestamp cutoff filter for performance reporting (ISO 8601 format)"
    ),
    end_time: Optional[datetime] = Query(
        None, description="End timestamp cutoff filter for performance reporting (ISO 8601 format)"
    ),
    organization_id: Optional[str] = Query(
        None, description="Optional organization ID filter"
    ),
    severity: Optional[TicketSeverity] = Query(
        None, description="Optional severity filter (P1, P2, P3)"
    ),
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> PerformanceReportResponseSchema:
    """Query operational performance metrics and CSAT performance statistics.

    Enforces tenant boundary checks and parameter validation.
    """
    _validate_date_range(start_time, end_time)

    target_org_id = check_tenant_boundary(organization_id, session) if organization_id else session.tenant_id

    try:
        report = generate_performance_report(
            db=db,
            start_time=start_time,
            end_time=end_time,
            organization_id=target_org_id,
            severity=severity,
        )
        return PerformanceReportResponseSchema.model_validate(report.to_dict())
    except ValueError as err:
        logger.warning("Invalid value in performance report query: %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )
    except Exception as err:
        logger.error("Error generating operational performance report: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_SERVER_ERROR",
                "message": "Failed to generate operational performance report.",
                "details": {},
            },
        )
