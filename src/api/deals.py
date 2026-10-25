"""FastAPI endpoint router and Pydantic validation schemas for deal pipelines, milestones, stakeholders, and forecasts."""

import logging
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from src.api.deps import get_current_session
from src.models.base import get_db
from src.models.deal import CriticalTask, Deal, DealStage, Milestone, MilestoneStatus, Stakeholder
from src.models.session import Session as SessionModel
from src.services.deals.escalation_service import evaluate_milestone_statuses, transition_deal_stage
from src.services.deals.forecasting_service import get_deal_timeline_forecast
from src.services.deals.network_service import get_deal_stakeholder_graph
from src.services.deals.pipeline_service import filter_deals, get_pipeline_funnel_metrics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/deals", tags=["deals"])


# --- Pipeline Funnel Schemas ---


class PipelineFilterParams(BaseModel):
    """Query parameter validation schema for pipeline funnel filtering."""

    start_date: Optional[datetime] = Field(None, description="Start date filter threshold")
    end_date: Optional[datetime] = Field(None, description="End date filter threshold")
    min_value: Optional[float] = Field(None, ge=0.0, description="Minimum deal monetary value filter")
    max_value: Optional[float] = Field(None, ge=0.0, description="Maximum deal monetary value filter")
    stage_id: Optional[str] = Field(None, description="Specific deal stage ID filter")
    team_member_id: Optional[str] = Field(None, description="Assigned team member ID filter")


class StageFunnelMetricsSchema(BaseModel):
    """Schema for aggregated stage funnel metrics."""

    stage_id: str = Field(..., description="Unique identifier for the deal stage")
    stage_name: str = Field(..., description="Name of the deal stage")
    display_order: int = Field(..., description="Display order sequence of stage")
    deal_count: int = Field(..., ge=0, description="Total deals currently in stage")
    total_monetary_value: float = Field(..., description="Sum of monetary values for deals in stage")
    average_monetary_value: float = Field(..., description="Average monetary value per deal in stage")
    average_win_probability: float = Field(..., ge=0.0, le=1.0, description="Average win probability in stage")
    weighted_monetary_value: float = Field(..., description="Monetary value weighted by win probability")
    average_deal_age_days: float = Field(..., ge=0.0, description="Average age of deals in stage in days")
    conversion_rate_from_previous: float = Field(..., description="Conversion rate percentage from prior stage")

    model_config = ConfigDict(from_attributes=True)


class PipelineFunnelSummarySchema(BaseModel):
    """Schema for overall pipeline funnel summary metrics."""

    total_deals: int = Field(..., ge=0, description="Total deals across all pipeline stages")
    total_pipeline_value: float = Field(..., description="Total aggregate pipeline monetary value")
    total_weighted_value: float = Field(..., description="Total win probability weighted pipeline value")
    overall_average_win_probability: float = Field(..., ge=0.0, le=1.0, description="Overall average win probability")
    overall_average_deal_age_days: float = Field(..., ge=0.0, description="Overall average deal age in days")
    stages: List[StageFunnelMetricsSchema] = Field(default_factory=list, description="Breakdown of metrics per stage")

    model_config = ConfigDict(from_attributes=True)


# --- Milestone & Critical Task Schemas ---


class MilestoneStatusEnum(str, Enum):
    """Enumeration for milestone completion status."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    DELAYED = "DELAYED"


class CriticalTaskCreate(BaseModel):
    """Payload schema for creating a critical task."""

    title: str = Field(..., min_length=1, max_length=255, description="Title of the critical task")
    due_date: datetime = Field(..., description="Target due date for task completion")
    is_critical: bool = Field(default=True, description="Whether task is designated as critical")


class CriticalTaskSchema(BaseModel):
    """Response schema for critical task details."""

    id: str = Field(..., description="Unique task identifier")
    milestone_id: str = Field(..., description="Associated milestone ID")
    title: str = Field(..., description="Title of the critical task")
    due_date: datetime = Field(..., description="Due date for the task")
    is_critical: bool = Field(..., description="Whether task is critical")
    is_overdue: bool = Field(..., description="Whether task is currently overdue")
    escalated_to_supervisor: bool = Field(..., description="Whether task has been escalated to supervisor")
    escalated_at: Optional[datetime] = Field(None, description="Timestamp when task was escalated")
    created_at: Optional[datetime] = Field(None, description="Task creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Task last updated timestamp")

    model_config = ConfigDict(from_attributes=True)


class MilestoneCreate(BaseModel):
    """Payload schema for creating a milestone."""

    title: str = Field(..., min_length=1, max_length=255, description="Title of the milestone")
    target_date: datetime = Field(..., description="Target date for milestone completion")
    status: MilestoneStatusEnum = Field(default=MilestoneStatusEnum.PENDING, description="Milestone status")


class MilestoneUpdate(BaseModel):
    """Payload schema for updating a milestone."""

    title: Optional[str] = Field(None, min_length=1, max_length=255, description="Updated milestone title")
    target_date: Optional[datetime] = Field(None, description="Updated target date")
    completed_date: Optional[datetime] = Field(None, description="Completion timestamp")
    status: Optional[MilestoneStatusEnum] = Field(None, description="Updated status")


class MilestoneSchema(BaseModel):
    """Response schema for milestone details."""

    id: str = Field(..., description="Unique milestone identifier")
    deal_id: str = Field(..., description="Associated deal ID")
    title: str = Field(..., description="Title of the milestone")
    target_date: datetime = Field(..., description="Target completion date")
    completed_date: Optional[datetime] = Field(None, description="Timestamp when completed")
    status: MilestoneStatusEnum = Field(..., description="Milestone completion status")
    color_code: Optional[str] = Field(None, description="UI visual color code indicator")
    critical_tasks: List[CriticalTaskSchema] = Field(default_factory=list, description="Associated critical tasks")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last updated timestamp")

    model_config = ConfigDict(from_attributes=True)


# --- Stakeholder Network Schemas ---


class StakeholderCreate(BaseModel):
    """Payload schema for creating a stakeholder."""

    name: str = Field(..., min_length=1, max_length=255, description="Full name of stakeholder")
    org_role: str = Field(..., min_length=1, max_length=255, description="Organizational role or title")
    contact_email: str = Field(..., min_length=3, max_length=255, description="Contact email address")
    parent_stakeholder_id: Optional[str] = Field(None, description="Optional parent manager stakeholder ID")


class StakeholderNodeSchema(BaseModel):
    """Node schema for stakeholder in network graph."""

    id: str = Field(..., description="Stakeholder unique identifier")
    name: str = Field(..., description="Stakeholder name")
    org_role: str = Field(..., description="Organizational role")
    contact_email: str = Field(..., description="Contact email address")
    email: Optional[str] = Field(None, description="Email alias for frontend graph rendering")
    deal_id: str = Field(..., description="Associated deal ID")
    parent_stakeholder_id: Optional[str] = Field(None, description="Parent stakeholder ID if hierarchical")
    subordinate_ids: List[str] = Field(default_factory=list, description="List of direct report stakeholder IDs")

    model_config = ConfigDict(from_attributes=True)


class StakeholderEdgeSchema(BaseModel):
    """Edge schema for relationship between stakeholders in graph."""

    source_id: str = Field(..., description="Source stakeholder ID")
    target_id: str = Field(..., description="Target stakeholder ID")
    relationship_type: str = Field(default="reports_to", description="Type of relationship")
    is_cycle_edge: bool = Field(default=False, description="Whether edge forms a circular reference")

    model_config = ConfigDict(from_attributes=True)


class StakeholderGraphResponse(BaseModel):
    """Response schema for stakeholder network graph."""

    deal_id: str = Field(..., description="Target deal ID")
    nodes: List[StakeholderNodeSchema] = Field(default_factory=list, description="Graph stakeholder nodes")
    edges: List[StakeholderEdgeSchema] = Field(default_factory=list, description="All graph edges including cycles")
    dag_edges: List[StakeholderEdgeSchema] = Field(default_factory=list, description="Directed Acyclic Graph edges with cycles removed")
    has_cycles: bool = Field(..., description="Flag indicating if circular reporting dependencies exist")
    detected_cycles: List[List[str]] = Field(default_factory=list, description="List of stakeholder ID paths forming cycles")
    root_stakeholder_ids: List[str] = Field(default_factory=list, description="Stakeholder IDs at top of hierarchy")

    model_config = ConfigDict(from_attributes=True)


# --- Timeline Forecast Schemas ---


class ForecastRequestPayload(BaseModel):
    """Request payload for timeline forecast calculation."""

    deal_id: Optional[str] = Field(None, description="Optional deal ID override in body")
    include_milestones: bool = Field(default=True, description="Whether to include milestone timeline estimates")


class MilestoneForecastEstimateSchema(BaseModel):
    """Estimated completion date for an individual milestone in forecast."""

    milestone_id: str = Field(..., description="Milestone identifier")
    milestone_title: Optional[str] = Field(None, description="Title of the milestone")
    estimated_completion_date: datetime = Field(..., description="Predicted completion datetime")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Model confidence score for milestone estimate")

    model_config = ConfigDict(from_attributes=True)


class DealForecastResponse(BaseModel):
    """Response schema for deal timeline forecast."""

    deal_id: str = Field(..., description="Target deal ID")
    predicted_close_date: datetime = Field(..., description="Predicted close date and time")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Overall forecast confidence score")
    milestone_estimates: List[MilestoneForecastEstimateSchema] = Field(
        default_factory=list, description="Timeline predictions for deal milestones"
    )
    is_fallback: bool = Field(default=False, description="Whether prediction was generated via historical fallback logic")
    model_version: Optional[str] = Field(None, description="Version tag of the forecasting model used")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional model output details or fallback metadata")

    model_config = ConfigDict(from_attributes=True)


# --- Deal Management Schemas ---


class DealCreate(BaseModel):
    """Payload schema for creating a new deal."""

    title: str = Field(..., min_length=1, max_length=255, description="Title of the deal")
    monetary_value: Decimal = Field(default=Decimal("0.00"), ge=0, description="Monetary value of the deal")
    stage_id: str = Field(..., description="Deal stage ID")
    assigned_team_member_id: Optional[str] = Field(None, description="ID of assigned team member")
    win_probability: float = Field(default=0.0, ge=0.0, le=1.0, description="Estimated win probability between 0.0 and 1.0")


class DealUpdate(BaseModel):
    """Payload schema for updating a deal."""

    title: Optional[str] = Field(None, min_length=1, max_length=255, description="Updated title")
    monetary_value: Optional[Decimal] = Field(None, ge=0, description="Updated monetary value")
    stage_id: Optional[str] = Field(None, description="Updated stage ID")
    assigned_team_member_id: Optional[str] = Field(None, description="Updated team member ID")
    win_probability: Optional[float] = Field(None, ge=0.0, le=1.0, description="Updated win probability")


class DealSchema(BaseModel):
    """Response schema for deal entity details."""

    id: str = Field(..., description="Deal unique identifier")
    title: str = Field(..., description="Deal title")
    monetary_value: float = Field(..., description="Monetary value")
    stage_id: str = Field(..., description="Associated deal stage ID")
    assigned_team_member_id: Optional[str] = Field(None, description="Assigned team member ID")
    win_probability: float = Field(..., description="Win probability")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last updated timestamp")

    model_config = ConfigDict(from_attributes=True)


# --- Helpers & Route Endpoints ---


def determine_milestone_color_code(status_val: Any) -> str:
    """Determine visual color indicator for a milestone status."""
    status_str = str(status_val.value if hasattr(status_val, "value") else status_val).upper()
    if status_str == "COMPLETED":
        return "green"
    elif status_str == "DELAYED":
        return "red"
    elif status_str == "IN_PROGRESS":
        return "blue"
    else:
        return "gray"


@router.get(
    "/funnel",
    response_model=PipelineFunnelSummarySchema,
    status_code=status.HTTP_200_OK,
    summary="Retrieve pipeline funnel aggregation metrics",
)
def get_pipeline_funnel(
    start_date: Optional[datetime] = Query(None, description="Start date filter threshold"),
    end_date: Optional[datetime] = Query(None, description="End date filter threshold"),
    min_value: Optional[float] = Query(None, ge=0.0, description="Minimum monetary value filter"),
    max_value: Optional[float] = Query(None, ge=0.0, description="Maximum monetary value filter"),
    stage_id: Optional[str] = Query(None, description="Specific deal stage ID filter"),
    team_member_id: Optional[str] = Query(None, description="Assigned team member ID filter"),
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> PipelineFunnelSummarySchema:
    """Retrieve aggregated pipeline funnel metrics and stage conversion rates."""
    summary = get_pipeline_funnel_metrics(
        db,
        start_date_filter=start_date,
        end_date_filter=end_date,
        min_value=min_value,
        max_value=max_value,
        stage_id=stage_id,
        assigned_team_member_id=team_member_id,
    )
    return PipelineFunnelSummarySchema.model_validate(summary.to_dict())


@router.get(
    "",
    response_model=List[DealSchema],
    status_code=status.HTTP_200_OK,
    summary="List or filter deals in pipeline",
)
def list_deals(
    start_date: Optional[datetime] = Query(None, description="Start date filter threshold"),
    end_date: Optional[datetime] = Query(None, description="End date filter threshold"),
    min_value: Optional[float] = Query(None, ge=0.0, description="Minimum monetary value filter"),
    max_value: Optional[float] = Query(None, ge=0.0, description="Maximum monetary value filter"),
    stage_id: Optional[str] = Query(None, description="Specific deal stage ID filter"),
    team_member_id: Optional[str] = Query(None, description="Assigned team member ID filter"),
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> List[DealSchema]:
    """Retrieve filtered list of deal entities."""
    deals = filter_deals(
        db,
        start_date_filter=start_date,
        end_date_filter=end_date,
        min_value=min_value,
        max_value=max_value,
        stage_id=stage_id,
        assigned_team_member_id=team_member_id,
    )
    return [DealSchema.model_validate(deal) for deal in deals]


@router.post(
    "",
    response_model=DealSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new deal entity",
)
def create_deal(
    payload: DealCreate,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> DealSchema:
    """Create a new deal record in the pipeline."""
    stage = db.query(DealStage).filter(DealStage.id == payload.stage_id).first()
    if not stage:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_STAGE",
                "message": f"Deal stage with ID '{payload.stage_id}' not found.",
                "details": {},
            },
        )

    deal = Deal(
        title=payload.title,
        monetary_value=payload.monetary_value,
        stage_id=payload.stage_id,
        assigned_team_member_id=payload.assigned_team_member_id,
        win_probability=payload.win_probability,
    )
    db.add(deal)
    db.commit()
    db.refresh(deal)
    return DealSchema.model_validate(deal)


@router.get(
    "/{deal_id}",
    response_model=DealSchema,
    status_code=status.HTTP_200_OK,
    summary="Get deal details by ID",
)
def get_deal(
    deal_id: str,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> DealSchema:
    """Get single deal details by identifier."""
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "DEAL_NOT_FOUND",
                "message": f"Deal with ID '{deal_id}' not found.",
                "details": {},
            },
        )
    return DealSchema.model_validate(deal)


@router.put(
    "/{deal_id}",
    response_model=DealSchema,
    status_code=status.HTTP_200_OK,
    summary="Update deal entity details",
)
def update_deal(
    deal_id: str,
    payload: DealUpdate,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> DealSchema:
    """Update properties of an existing deal entity."""
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "DEAL_NOT_FOUND",
                "message": f"Deal with ID '{deal_id}' not found.",
                "details": {},
            },
        )

    if payload.title is not None:
        deal.title = payload.title
    if payload.monetary_value is not None:
        deal.monetary_value = payload.monetary_value
    if payload.stage_id is not None:
        stage = db.query(DealStage).filter(DealStage.id == payload.stage_id).first()
        if not stage:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_STAGE",
                    "message": f"Deal stage with ID '{payload.stage_id}' not found.",
                    "details": {},
                },
            )
        deal.stage_id = payload.stage_id
    if payload.assigned_team_member_id is not None:
        deal.assigned_team_member_id = payload.assigned_team_member_id
    if payload.win_probability is not None:
        deal.win_probability = payload.win_probability

    db.commit()
    db.refresh(deal)
    return DealSchema.model_validate(deal)


@router.delete(
    "/{deal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete deal entity",
)
def delete_deal(
    deal_id: str,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> Response:
    """Delete a deal resource from the pipeline."""
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "DEAL_NOT_FOUND",
                "message": f"Deal with ID '{deal_id}' not found.",
                "details": {},
            },
        )
    db.delete(deal)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{deal_id}/stage",
    response_model=DealSchema,
    status_code=status.HTTP_200_OK,
    summary="Transition deal to a new stage",
)
def transition_stage(
    deal_id: str,
    stage_id: str = Query(..., description="Target deal stage ID"),
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> DealSchema:
    """Transition a deal to a new stage and emit structured stage transition log event."""
    try:
        deal, _ = transition_deal_stage(
            session=db,
            deal_id=deal_id,
            new_stage_id=stage_id,
            user_id=session.user_id if session else None,
        )
        return DealSchema.model_validate(deal)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_STAGE_TRANSITION",
                "message": str(err),
                "details": {},
            },
        )


@router.get(
    "/{deal_id}/milestones",
    response_model=List[MilestoneSchema],
    status_code=status.HTTP_200_OK,
    summary="Retrieve milestones and task progress for a deal",
)
def get_deal_milestones(
    deal_id: str,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> List[MilestoneSchema]:
    """Retrieve color-coded milestones and critical tasks for a deal."""
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "DEAL_NOT_FOUND",
                "message": f"Deal with ID '{deal_id}' not found.",
                "details": {},
            },
        )

    evaluate_milestone_statuses(db, deal_id=deal_id)

    milestones = (
        db.query(Milestone)
        .filter(Milestone.deal_id == deal_id)
        .order_by(Milestone.target_date.asc())
        .all()
    )

    result = []
    for ms in milestones:
        color = determine_milestone_color_code(ms.status)
        tasks = [CriticalTaskSchema.model_validate(task) for task in ms.critical_tasks]
        ms_schema = MilestoneSchema(
            id=ms.id,
            deal_id=ms.deal_id,
            title=ms.title,
            target_date=ms.target_date,
            completed_date=ms.completed_date,
            status=MilestoneStatusEnum(ms.status.value if hasattr(ms.status, "value") else str(ms.status)),
            color_code=color,
            critical_tasks=tasks,
            created_at=ms.created_at,
            updated_at=ms.updated_at,
        )
        result.append(ms_schema)

    return result


@router.post(
    "/{deal_id}/milestones",
    response_model=MilestoneSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new milestone for a deal",
)
def create_deal_milestone(
    deal_id: str,
    payload: MilestoneCreate,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> MilestoneSchema:
    """Create a new milestone under the specified deal."""
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "DEAL_NOT_FOUND",
                "message": f"Deal with ID '{deal_id}' not found.",
                "details": {},
            },
        )

    milestone = Milestone(
        deal_id=deal_id,
        title=payload.title,
        target_date=payload.target_date,
        status=MilestoneStatus(payload.status.value),
    )
    db.add(milestone)
    db.commit()
    db.refresh(milestone)

    color = determine_milestone_color_code(milestone.status)
    return MilestoneSchema(
        id=milestone.id,
        deal_id=milestone.deal_id,
        title=milestone.title,
        target_date=milestone.target_date,
        completed_date=milestone.completed_date,
        status=MilestoneStatusEnum(milestone.status.value),
        color_code=color,
        critical_tasks=[],
        created_at=milestone.created_at,
        updated_at=milestone.updated_at,
    )


@router.put(
    "/{deal_id}/milestones/{milestone_id}",
    response_model=MilestoneSchema,
    status_code=status.HTTP_200_OK,
    summary="Update a milestone",
)
def update_deal_milestone(
    deal_id: str,
    milestone_id: str,
    payload: MilestoneUpdate,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> MilestoneSchema:
    """Update properties or status of an existing milestone."""
    milestone = (
        db.query(Milestone)
        .filter(Milestone.id == milestone_id, Milestone.deal_id == deal_id)
        .first()
    )
    if not milestone:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "MILESTONE_NOT_FOUND",
                "message": f"Milestone with ID '{milestone_id}' not found for deal '{deal_id}'.",
                "details": {},
            },
        )

    if payload.title is not None:
        milestone.title = payload.title
    if payload.target_date is not None:
        milestone.target_date = payload.target_date
    if payload.completed_date is not None:
        milestone.completed_date = payload.completed_date
    if payload.status is not None:
        milestone.status = MilestoneStatus(payload.status.value)

    db.commit()
    db.refresh(milestone)

    color = determine_milestone_color_code(milestone.status)
    tasks = [CriticalTaskSchema.model_validate(task) for task in milestone.critical_tasks]
    return MilestoneSchema(
        id=milestone.id,
        deal_id=milestone.deal_id,
        title=milestone.title,
        target_date=milestone.target_date,
        completed_date=milestone.completed_date,
        status=MilestoneStatusEnum(milestone.status.value),
        color_code=color,
        critical_tasks=tasks,
        created_at=milestone.created_at,
        updated_at=milestone.updated_at,
    )


@router.post(
    "/{deal_id}/milestones/{milestone_id}/tasks",
    response_model=CriticalTaskSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Create a critical task under a milestone",
)
def create_critical_task(
    deal_id: str,
    milestone_id: str,
    payload: CriticalTaskCreate,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> CriticalTaskSchema:
    """Create a new critical task associated with a milestone."""
    milestone = (
        db.query(Milestone)
        .filter(Milestone.id == milestone_id, Milestone.deal_id == deal_id)
        .first()
    )
    if not milestone:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "MILESTONE_NOT_FOUND",
                "message": f"Milestone with ID '{milestone_id}' not found for deal '{deal_id}'.",
                "details": {},
            },
        )

    now = datetime.now(payload.due_date.tzinfo or None)
    is_overdue = payload.due_date < now

    task = CriticalTask(
        milestone_id=milestone_id,
        title=payload.title,
        due_date=payload.due_date,
        is_critical=payload.is_critical,
        is_overdue=is_overdue,
        escalated_to_supervisor=False,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return CriticalTaskSchema.model_validate(task)


@router.get(
    "/{deal_id}/stakeholders",
    response_model=StakeholderGraphResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve stakeholder network graph for a deal",
)
def get_stakeholders(
    deal_id: str,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> StakeholderGraphResponse:
    """Retrieve stakeholder contact network graph and reporting cycle analysis."""
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "DEAL_NOT_FOUND",
                "message": f"Deal with ID '{deal_id}' not found.",
                "details": {},
            },
        )

    graph = get_deal_stakeholder_graph(db, deal_id)
    return StakeholderGraphResponse.model_validate(graph.to_dict())


@router.post(
    "/{deal_id}/stakeholders",
    response_model=StakeholderNodeSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Create a stakeholder contact for a deal",
)
def create_stakeholder(
    deal_id: str,
    payload: StakeholderCreate,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> StakeholderNodeSchema:
    """Create a new stakeholder entity associated with a deal."""
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "DEAL_NOT_FOUND",
                "message": f"Deal with ID '{deal_id}' not found.",
                "details": {},
            },
        )

    if payload.parent_stakeholder_id:
        parent = (
            db.query(Stakeholder)
            .filter(
                Stakeholder.id == payload.parent_stakeholder_id,
                Stakeholder.deal_id == deal_id,
            )
            .first()
        )
        if not parent:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_PARENT_STAKEHOLDER",
                    "message": f"Parent stakeholder with ID '{payload.parent_stakeholder_id}' not found.",
                    "details": {},
                },
            )

    stakeholder = Stakeholder(
        deal_id=deal_id,
        name=payload.name,
        org_role=payload.org_role,
        contact_email=payload.contact_email,
        parent_stakeholder_id=payload.parent_stakeholder_id,
    )
    db.add(stakeholder)
    db.commit()
    db.refresh(stakeholder)

    subordinates = (
        db.query(Stakeholder)
        .filter(Stakeholder.parent_stakeholder_id == stakeholder.id)
        .all()
    )
    subordinate_ids = [sub.id for sub in subordinates]

    return StakeholderNodeSchema(
        id=stakeholder.id,
        name=stakeholder.name,
        org_role=stakeholder.org_role,
        contact_email=stakeholder.contact_email,
        email=stakeholder.contact_email,
        deal_id=stakeholder.deal_id,
        parent_stakeholder_id=stakeholder.parent_stakeholder_id,
        subordinate_ids=subordinate_ids,
    )


@router.post(
    "/{deal_id}/forecast",
    response_model=DealForecastResponse,
    status_code=status.HTTP_200_OK,
    summary="Predict deal completion timeline forecast",
)
def get_deal_forecast(
    deal_id: str,
    payload: Optional[ForecastRequestPayload] = Body(None),
    include_milestones: Optional[bool] = Query(True),
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> DealForecastResponse:
    """Predict deal close date and milestone timelines with historical fallback."""
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "DEAL_NOT_FOUND",
                "message": f"Deal with ID '{deal_id}' not found.",
                "details": {},
            },
        )

    inc_milestones = True
    if payload is not None and payload.include_milestones is not None:
        inc_milestones = payload.include_milestones
    elif include_milestones is not None:
        inc_milestones = include_milestones

    result = get_deal_timeline_forecast(
        db,
        deal_id=deal_id,
        include_milestones=inc_milestones,
    )
    return DealForecastResponse.model_validate(result.to_dict())

