"""FastAPI router for financial dashboard metrics, including AR aging buckets and ARR trends."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from src.models.base import get_db
from src.services.financial.dashboard import (
    ArAgingBucketSummary,
    ArrTrendDataPoint,
    get_ar_aging_summary,
    get_arr_trends,
    get_financial_dashboard_data,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/financial", tags=["financial-dashboard"])


# --- Schemas ---


class ArAgingResponseSchema(BaseModel):
    """Pydantic schema for AR aging bucket summary response."""

    account_id: str = Field(..., description="Target account UUID or 'ALL'")
    current_balance: float = Field(..., description="Current outstanding balance (due today or in future)")
    bucket_30_balance: float = Field(..., description="Outstanding balance 1-30 days overdue")
    bucket_60_balance: float = Field(..., description="Outstanding balance 31-60 days overdue")
    bucket_90_plus_balance: float = Field(..., description="Outstanding balance 90+ days overdue (>60 days)")
    total_outstanding: float = Field(..., description="Total outstanding AR balance across all aging buckets")
    as_of_timestamp: str = Field(..., description="ISO 8601 timestamp when aging snapshot was calculated")

    model_config = ConfigDict(from_attributes=True)


class ArrTrendDataPointSchema(BaseModel):
    """Pydantic schema for a single ARR trend data point."""

    id: str = Field(..., description="Unique ARR metric record identifier")
    account_id: str = Field(..., description="Target account UUID")
    metric_period: str = Field(..., description="Metric period identifier (e.g., '2026-Q1')")
    arr_value: float = Field(..., description="Calculated ARR monetary value in target currency")
    currency: str = Field(..., description="Target display currency ISO code")
    original_currency: str = Field(..., description="Original currency ISO code before conversion")
    recorded_at: str = Field(..., description="ISO 8601 timestamp when metric was recorded")

    model_config = ConfigDict(from_attributes=True)


class FinancialDashboardSummarySchema(BaseModel):
    """Pydantic schema for overall financial dashboard summary."""

    account_id: str = Field(..., description="Target account UUID or 'ALL'")
    ar_aging: ArAgingResponseSchema = Field(..., description="AR aging bucket summary")
    arr_trends: List[ArrTrendDataPointSchema] = Field(
        default_factory=list, description="Historical and current ARR trend data points"
    )
    total_arr: float = Field(..., description="Aggregate total ARR across returned trend points")
    target_currency: str = Field(..., description="Target display currency ISO code")
    calculated_at: str = Field(..., description="ISO 8601 timestamp when dashboard data was calculated")

    model_config = ConfigDict(from_attributes=True)


# --- Endpoint Routes ---


@router.get(
    "/ar-aging",
    response_model=ArAgingResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Get AR aging bucket balances",
    description="Retrieve outstanding Accounts Receivable balances categorized into current, 30, 60, and 90+ day overdue buckets.",
)
def get_ar_aging_endpoint(
    account_id: Optional[str] = Query(None, description="Optional account UUID filter"),
    force_recalculate: bool = Query(False, description="Whether to force dynamic recalculation from invoices"),
    db: DBSession = Depends(get_db),
) -> ArAgingResponseSchema:
    """API endpoint to retrieve AR aging bucket balances."""
    try:
        summary: ArAgingBucketSummary = get_ar_aging_summary(
            db,
            account_id=account_id,
            force_recalculate=force_recalculate,
        )
        return ArAgingResponseSchema(**summary.to_dict())
    except Exception as err:
        logger.error("Error retrieving AR aging summary: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "AR_AGING_ERROR",
                "message": f"Failed to retrieve AR aging summary: {str(err)}",
                "details": {},
            },
        )


@router.get(
    "/arr-trends",
    response_model=List[ArrTrendDataPointSchema],
    status_code=status.HTTP_200_OK,
    summary="Get ARR trend data points",
    description="Retrieve historical and current Annual Recurring Revenue (ARR) trend data points for commercial analysis.",
)
def get_arr_trends_endpoint(
    account_id: Optional[str] = Query(None, description="Optional account UUID filter"),
    period_range: Optional[str] = Query(
        None, description="Optional comma-separated list of metric periods (e.g. '2026-Q1,2026-Q2')"
    ),
    target_currency: str = Query(
        "USD", description="Target currency ISO code for display conversion (default 'USD')"
    ),
    db: DBSession = Depends(get_db),
) -> List[ArrTrendDataPointSchema]:
    """API endpoint to retrieve ARR trend data points."""
    try:
        trend_points: List[ArrTrendDataPoint] = get_arr_trends(
            db,
            account_id=account_id,
            period_range=period_range,
            target_currency=target_currency,
        )
        return [ArrTrendDataPointSchema(**pt.to_dict()) for pt in trend_points]
    except Exception as err:
        logger.error("Error retrieving ARR trend metrics: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "ARR_TRENDS_ERROR",
                "message": f"Failed to retrieve ARR trend metrics: {str(err)}",
                "details": {},
            },
        )


@router.get(
    "/dashboard",
    response_model=FinancialDashboardSummarySchema,
    status_code=status.HTTP_200_OK,
    summary="Get comprehensive financial dashboard data",
    description="Retrieve aggregated financial dashboard data including AR aging buckets, ARR trends, and total ARR.",
)
def get_financial_dashboard_endpoint(
    account_id: Optional[str] = Query(None, description="Optional account UUID filter"),
    target_currency: str = Query(
        "USD", description="Target currency ISO code for display conversion (default 'USD')"
    ),
    db: DBSession = Depends(get_db),
) -> FinancialDashboardSummarySchema:
    """API endpoint to retrieve comprehensive financial dashboard overview data."""
    try:
        data: Dict[str, Any] = get_financial_dashboard_data(
            db,
            account_id=account_id,
            target_currency=target_currency,
        )
        return FinancialDashboardSummarySchema(
            account_id=data["account_id"],
            ar_aging=ArAgingResponseSchema(**data["ar_aging"]),
            arr_trends=[ArrTrendDataPointSchema(**pt) for pt in data["arr_trends"]],
            total_arr=data["total_arr"],
            target_currency=data["target_currency"],
            calculated_at=data["calculated_at"],
        )
    except Exception as err:
        logger.error("Error retrieving financial dashboard overview: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "FINANCIAL_DASHBOARD_ERROR",
                "message": f"Failed to retrieve financial dashboard overview: {str(err)}",
                "details": {},
            },
        )
