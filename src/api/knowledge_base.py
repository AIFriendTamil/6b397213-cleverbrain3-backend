"""FastAPI router for Knowledge Base search, content filtering, category tree, and resource details."""

import logging
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from src.api.deps import get_current_session
from src.connectors.video_provider import VideoProviderAdapter
from src.lib.config import settings
from src.models.base import get_db
from src.models.knowledge_base import KBContentType
from src.models.session import Session as SessionModel
from src.services.knowledge_base.service import (
    KnowledgeBaseError,
    ResourceAccessDeniedError,
    ResourceNotFoundError,
    add_bookmark,
    get_category_tree,
    get_resource_detail,
    get_user_progress,
    remove_bookmark,
    search_resources,
    update_user_progress,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/kb", tags=["knowledge-base"])


# --- Schemas ---


class KBCategoryNodeSchema(BaseModel):
    """Pydantic response schema for nested category hierarchy tree nodes."""

    id: str = Field(..., description="Category UUID primary key")
    title: str = Field(..., description="Category title")
    slug: str = Field(..., description="Category URL slug")
    description: Optional[str] = Field(None, description="Category detailed description")
    parent_id: Optional[str] = Field(None, description="Parent category UUID if nested")
    display_order: int = Field(0, description="Display order index")
    created_on: Optional[str] = Field(None, description="ISO timestamp when category was created")
    children: List["KBCategoryNodeSchema"] = Field(default_factory=list, description="Nested child categories")

    model_config = ConfigDict(from_attributes=True)


KBCategoryNodeSchema.model_rebuild()


class KBBreadcrumbSchema(BaseModel):
    """Pydantic response schema for category breadcrumb navigation node."""

    id: str = Field(..., description="Category UUID")
    title: str = Field(..., description="Category title")
    slug: str = Field(..., description="Category URL slug")

    model_config = ConfigDict(from_attributes=True)


class KBTutorialStepSchema(BaseModel):
    """Pydantic response schema for tutorial step item."""

    id: str = Field(..., description="Step UUID primary key")
    resource_id: str = Field(..., description="Associated parent resource UUID")
    step_number: int = Field(..., description="1-indexed step number")
    title: str = Field(..., description="Step title")
    content: Optional[str] = Field(None, description="Step content text")
    video_url: Optional[str] = Field(None, description="Optional step video URL")

    model_config = ConfigDict(from_attributes=True)


class KBUserProgressSchema(BaseModel):
    """Pydantic response schema for user progress status."""

    resource_id: str = Field(..., description="Resource UUID")
    user_id: str = Field(..., description="User ID")
    is_completed: bool = Field(False, description="Whether resource or step is completed")
    completed_step_numbers: List[int] = Field(default_factory=list, description="List of completed step numbers")
    total_steps: int = Field(0, description="Total tutorial steps count")
    completion_percentage: float = Field(0.0, description="Completion progress percentage")
    last_accessed_on: Optional[str] = Field(None, description="ISO timestamp of last activity")

    model_config = ConfigDict(from_attributes=True)


class KBVideoEmbedSchema(BaseModel):
    """Pydantic response schema for media video player embed configuration."""

    original_input: str = Field(..., description="Sanitized original media input URL or ID")
    provider: str = Field(..., description="Media provider type (youtube, vimeo, direct, default)")
    video_id: str = Field(..., description="Extracted provider video ID")
    embed_url: str = Field(..., description="Sanitized embed player URL")
    has_controls: bool = Field(True, description="Whether media controls are enabled")
    allow_fullscreen: bool = Field(True, description="Whether full screen playback is allowed")
    aspect_ratio: str = Field("16:9", description="Video player aspect ratio")
    sanitized_iframe: str = Field(..., description="HTML iframe embed markup")
    title: str = Field(..., description="Video accessibility title")

    model_config = ConfigDict(from_attributes=True)


class KBResourceSummarySchema(BaseModel):
    """Pydantic response schema for knowledge base resource summary items."""

    id: str = Field(..., description="Resource UUID primary key")
    category_id: str = Field(..., description="Associated category UUID")
    category_title: Optional[str] = Field(None, description="Associated category title")
    title: str = Field(..., description="Resource title")
    slug: str = Field(..., description="Resource URL slug")
    content_type: str = Field(..., description="Content type identifier (FAQ, PLAYBOOK, PROCESS_DOC, TUTORIAL, VIDEO)")
    body_text: Optional[str] = Field(None, description="Resource body text content")
    media_url: Optional[str] = Field(None, description="Media or streaming video URL")
    tags: List[str] = Field(default_factory=list, description="Associated tag strings")
    onboarding_phase: Optional[str] = Field(None, description="Onboarding phase identifier")
    required_role: Optional[str] = Field(None, description="Required role for access control")
    min_subscription_tier: Optional[str] = Field(None, description="Minimum subscription tier for access control")
    published_on: Optional[str] = Field(None, description="ISO timestamp when resource was published")
    highlights: List[str] = Field(default_factory=list, description="Matched keyword text snippets with highlights")

    model_config = ConfigDict(from_attributes=True)


class KBResourceDetailSchema(KBResourceSummarySchema):
    """Pydantic response schema for full knowledge base resource details."""

    breadcrumbs: List[KBBreadcrumbSchema] = Field(default_factory=list, description="Category hierarchy breadcrumb trail")
    tutorial_steps: List[KBTutorialStepSchema] = Field(default_factory=list, description="Ordered tutorial steps if applicable")
    is_bookmarked: bool = Field(False, description="Whether resource is bookmarked by user")
    progress: Optional[KBUserProgressSchema] = Field(None, description="User progress state if authenticated")
    media_embed: Optional[KBVideoEmbedSchema] = Field(None, description="Sanitized video embed configuration if media_url is present")

    model_config = ConfigDict(from_attributes=True)


class PaginatedKBResourcesResponseSchema(BaseModel):
    """Pydantic response schema for paginated knowledge base search results."""

    items: List[KBResourceSummarySchema] = Field(default_factory=list, description="List of matching resource summaries")
    total_count: int = Field(..., description="Total matching accessible resources count")
    page: int = Field(..., description="Current page number")
    limit: int = Field(..., description="Maximum items per page")

    model_config = ConfigDict(from_attributes=True)


class KBBookmarkCreateSchema(BaseModel):
    """Pydantic request payload schema for adding a resource bookmark."""

    resource_id: str = Field(..., description="Knowledge base resource UUID primary key to bookmark")


class KBBookmarkSchema(BaseModel):
    """Pydantic response schema for created bookmark item."""

    id: str = Field(..., description="Bookmark UUID primary key")
    user_id: str = Field(..., description="User ID associated with bookmark")
    resource_id: str = Field(..., description="Bookmarked resource UUID primary key")
    created_on: Optional[str] = Field(None, description="ISO timestamp when bookmark was created")

    model_config = ConfigDict(from_attributes=True)


class KBProgressUpdateSchema(BaseModel):
    """Pydantic request payload schema for tracking user completion progress."""

    resource_id: str = Field(..., description="Knowledge base resource UUID primary key")
    step_number: Optional[int] = Field(None, description="Optional 1-indexed tutorial step number")
    is_completed: bool = Field(True, description="Whether resource or step is completed")


# --- Endpoint Handlers ---


@router.get(
    "/categories",
    response_model=List[KBCategoryNodeSchema],
    status_code=status.HTTP_200_OK,
    summary="Get hierarchical category tree",
    description="Retrieve the complete hierarchical knowledge base category tree with nested children nodes.",
    responses={
        200: {
            "description": "Successfully retrieved category tree",
            "model": List[KBCategoryNodeSchema],
        },
        401: {
            "description": "Authentication required or session invalid",
        },
    },
)
def get_kb_category_tree(
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> List[dict[str, Any]]:
    """Retrieve top-level category hierarchy tree with nested children nodes.

    Args:
        session: Active user session dependency.
        db: Synchronous SQLAlchemy database session.

    Returns:
        List of root category dictionary nodes containing nested children lists.
    """
    logger.info("KB get category tree request from user %s", session.user_id)
    try:
        categories = get_category_tree(db)
        return categories
    except KnowledgeBaseError as err:
        logger.warning("KB category tree error: %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )
    except Exception as err:
        logger.error("Unexpected error during KB category tree retrieval: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred during category tree retrieval.",
                "details": {},
            },
        )


@router.get(
    "/resources",
    response_model=PaginatedKBResourcesResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Search and filter knowledge base resources",
    description="Search knowledge base resources by query keyword, content type, category, and onboarding phase with RBAC enforcement.",
    responses={
        200: {
            "description": "Successfully retrieved knowledge base resources",
            "model": PaginatedKBResourcesResponseSchema,
        },
        401: {
            "description": "Authentication required or session invalid",
        },
        403: {
            "description": "Access denied due to insufficient role or subscription tier",
        },
    },
)
def search_kb_resources(
    query: Optional[str] = Query(None, description="Search keyword query string"),
    content_type: Optional[KBContentType] = Query(None, description="Filter by content type (FAQ, PLAYBOOK, PROCESS_DOC, TUTORIAL, VIDEO)"),
    category_id: Optional[str] = Query(None, description="Filter by category UUID"),
    onboarding_phase: Optional[str] = Query(None, description="Filter by onboarding phase string"),
    user_role: Optional[str] = Query(None, description="Explicit user role override"),
    subscription_tier: Optional[str] = Query(None, description="Explicit subscription tier override"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role", description="User role passed in header"),
    x_subscription_tier: Optional[str] = Header(None, alias="X-Subscription-Tier", description="Subscription tier passed in header"),
    page: int = Query(settings.KB_SEARCH_DEFAULT_PAGE, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(settings.KB_SEARCH_DEFAULT_LIMIT, ge=1, le=settings.KB_SEARCH_MAX_LIMIT, description="Number of items per page"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    """Search and filter knowledge base resources with RBAC scope authorization.

    Args:
        query: Search keyword string matching title, body_text, tags, phase, or category.
        content_type: Optional filter for content type enum.
        category_id: Optional filter for parent or specific category UUID.
        onboarding_phase: Optional filter for onboarding phase.
        user_role: Query parameter user role override.
        subscription_tier: Query parameter subscription tier override.
        x_user_role: Header containing user role.
        x_subscription_tier: Header containing subscription tier.
        page: Page number (default: settings.KB_SEARCH_DEFAULT_PAGE).
        limit: Page size limit (default: settings.KB_SEARCH_DEFAULT_LIMIT).
        session: Active user session dependency.
        db: Synchronous SQLAlchemy database session.

    Returns:
        Paginated summary object containing items list, total_count, page, and limit.
    """
    effective_user_role = user_role or x_user_role
    effective_subscription_tier = subscription_tier or x_subscription_tier

    logger.info(
        "KB search request: query=%r, content_type=%s, category_id=%s, onboarding_phase=%s, page=%d, limit=%d, role=%s, tier=%s",
        query,
        content_type,
        category_id,
        onboarding_phase,
        page,
        limit,
        effective_user_role,
        effective_subscription_tier,
    )

    try:
        results = search_resources(
            db=db,
            query=query,
            content_type=content_type,
            category_id=category_id,
            onboarding_phase=onboarding_phase,
            user_role=effective_user_role,
            subscription_tier=effective_subscription_tier,
            page=page,
            limit=limit,
        )
        return results
    except ResourceAccessDeniedError as err:
        logger.warning("KB search access denied: %s", err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except KnowledgeBaseError as err:
        logger.warning("KB search error: %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )
    except Exception as err:
        logger.error("Unexpected error during KB search: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred during knowledge base search.",
                "details": {},
            },
        )


@router.get(
    "/resources/{id}",
    response_model=KBResourceDetailSchema,
    status_code=status.HTTP_200_OK,
    summary="Get single resource details",
    description="Retrieve single knowledge base resource details including category breadcrumbs, tutorial steps, media embed config, bookmark state, and user progress.",
    responses={
        200: {
            "description": "Successfully retrieved resource details",
            "model": KBResourceDetailSchema,
        },
        401: {
            "description": "Authentication required or session invalid",
        },
        403: {
            "description": "Access denied due to insufficient role or subscription tier",
        },
        404: {
            "description": "Resource not found or deprecated",
        },
    },
)
def get_kb_resource_detail(
    id: str = Path(..., description="Knowledge base resource UUID primary key"),
    user_role: Optional[str] = Query(None, description="Explicit user role override"),
    subscription_tier: Optional[str] = Query(None, description="Explicit subscription tier override"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role", description="User role passed in header"),
    x_subscription_tier: Optional[str] = Header(None, alias="X-Subscription-Tier", description="Subscription tier passed in header"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    """Retrieve full details for a single knowledge base resource.

    Args:
        id: Resource UUID primary key.
        user_role: Query parameter user role override.
        subscription_tier: Query parameter subscription tier override.
        x_user_role: Header containing user role.
        x_subscription_tier: Header containing subscription tier.
        session: Active user session dependency.
        db: Synchronous SQLAlchemy database session.

    Returns:
        Full resource detail dictionary containing breadcrumbs, tutorial steps, media embed, bookmark state, and user progress.
    """
    effective_user_role = user_role or x_user_role
    effective_subscription_tier = subscription_tier or x_subscription_tier

    logger.info(
        "KB resource detail request: resource_id=%s, user_id=%s, role=%s, tier=%s",
        id,
        session.user_id,
        effective_user_role,
        effective_subscription_tier,
    )

    try:
        detail = get_resource_detail(
            db=db,
            resource_id=id,
            user_id=session.user_id,
            user_role=effective_user_role,
            subscription_tier=effective_subscription_tier,
        )

        # Enrich with media embed configuration if media_url is present
        media_url = detail.get("media_url")
        if media_url:
            try:
                video_adapter = VideoProviderAdapter()
                detail["media_embed"] = video_adapter.get_embed_config(
                    media_url, title=detail.get("title")
                )
            except Exception as err:
                logger.warning("Failed to resolve video embed for media_url %s: %s", media_url, err)
                detail["media_embed"] = None
        else:
            detail["media_embed"] = None

        return detail
    except ResourceNotFoundError as err:
        logger.warning("KB resource not found: %s", err)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )
    except ResourceAccessDeniedError as err:
        logger.warning("KB resource access denied: %s", err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except KnowledgeBaseError as err:
        logger.warning("KB resource detail error: %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )
    except Exception as err:
        logger.error("Unexpected error during KB resource detail retrieval: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred during resource detail retrieval.",
                "details": {},
            },
        )


@router.post(
    "/bookmarks",
    response_model=KBBookmarkSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Add a resource bookmark",
    description="Bookmark a knowledge base resource for the authenticated user.",
    responses={
        201: {
            "description": "Bookmark successfully created",
            "model": KBBookmarkSchema,
        },
        401: {
            "description": "Authentication required or session invalid",
        },
        403: {
            "description": "Access denied to resource",
        },
        404: {
            "description": "Resource not found",
        },
    },
)
def create_kb_bookmark(
    payload: KBBookmarkCreateSchema,
    user_role: Optional[str] = Query(None, description="Explicit user role override"),
    subscription_tier: Optional[str] = Query(None, description="Explicit subscription tier override"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role", description="User role passed in header"),
    x_subscription_tier: Optional[str] = Header(None, alias="X-Subscription-Tier", description="Subscription tier passed in header"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a bookmark for a knowledge base resource.

    Args:
        payload: Bookmark creation request schema.
        user_role: Query parameter user role override.
        subscription_tier: Query parameter subscription tier override.
        x_user_role: Header containing user role.
        x_subscription_tier: Header containing subscription tier.
        session: Active user session dependency.
        db: Synchronous SQLAlchemy database session.

    Returns:
        Created bookmark dictionary payload.
    """
    effective_user_role = user_role or x_user_role
    effective_subscription_tier = subscription_tier or x_subscription_tier

    logger.info(
        "KB create bookmark request: resource_id=%s, user_id=%s",
        payload.resource_id,
        session.user_id,
    )

    try:
        bookmark = add_bookmark(
            db=db,
            user_id=session.user_id,
            resource_id=payload.resource_id,
            user_role=effective_user_role,
            subscription_tier=effective_subscription_tier,
        )
        return {
            "id": bookmark.id,
            "user_id": bookmark.user_id,
            "resource_id": bookmark.resource_id,
            "created_on": bookmark.created_on.isoformat() if bookmark.created_on else None,
        }
    except ResourceNotFoundError as err:
        logger.warning("KB bookmark resource not found: %s", err)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )
    except ResourceAccessDeniedError as err:
        logger.warning("KB bookmark resource access denied: %s", err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except KnowledgeBaseError as err:
        logger.warning("KB bookmark error: %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )
    except Exception as err:
        logger.error("Unexpected error during KB bookmark creation: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred while creating bookmark.",
                "details": {},
            },
        )


@router.delete(
    "/bookmarks/{resource_id}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a resource bookmark",
    description="Remove a bookmarked resource for the authenticated user.",
    responses={
        204: {
            "description": "Bookmark successfully removed or did not exist",
        },
        401: {
            "description": "Authentication required or session invalid",
        },
    },
)
def delete_kb_bookmark(
    resource_id: str = Path(..., description="Knowledge base resource UUID primary key"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> Response:
    """Remove a bookmark for the authenticated user.

    Args:
        resource_id: Resource UUID primary key.
        session: Active user session dependency.
        db: Synchronous SQLAlchemy database session.

    Returns:
        Empty FastAPI Response with HTTP 204 No Content.
    """
    logger.info(
        "KB delete bookmark request: resource_id=%s, user_id=%s",
        resource_id,
        session.user_id,
    )

    try:
        remove_bookmark(db=db, user_id=session.user_id, resource_id=resource_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except KnowledgeBaseError as err:
        logger.warning("KB delete bookmark error: %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )
    except Exception as err:
        logger.error("Unexpected error during KB delete bookmark: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred while removing bookmark.",
                "details": {},
            },
        )


@router.post(
    "/progress",
    response_model=KBUserProgressSchema,
    status_code=status.HTTP_200_OK,
    summary="Update user completion progress",
    description="Update completion progress for step-by-step tutorials or video modules.",
    responses={
        200: {
            "description": "Successfully updated progress state",
            "model": KBUserProgressSchema,
        },
        401: {
            "description": "Authentication required or session invalid",
        },
        403: {
            "description": "Access denied to resource",
        },
        404: {
            "description": "Resource not found",
        },
    },
)
def track_kb_user_progress(
    payload: KBProgressUpdateSchema,
    user_role: Optional[str] = Query(None, description="Explicit user role override"),
    subscription_tier: Optional[str] = Query(None, description="Explicit subscription tier override"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role", description="User role passed in header"),
    x_subscription_tier: Optional[str] = Header(None, alias="X-Subscription-Tier", description="Subscription tier passed in header"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    """Track and update user progress for a resource or tutorial step.

    Args:
        payload: Progress tracking request schema.
        user_role: Query parameter user role override.
        subscription_tier: Query parameter subscription tier override.
        x_user_role: Header containing user role.
        x_subscription_tier: Header containing subscription tier.
        session: Active user session dependency.
        db: Synchronous SQLAlchemy database session.

    Returns:
        Updated user progress dictionary payload.
    """
    effective_user_role = user_role or x_user_role
    effective_subscription_tier = subscription_tier or x_subscription_tier

    logger.info(
        "KB update progress request: resource_id=%s, step_number=%s, is_completed=%s, user_id=%s",
        payload.resource_id,
        payload.step_number,
        payload.is_completed,
        session.user_id,
    )

    try:
        update_user_progress(
            db=db,
            user_id=session.user_id,
            resource_id=payload.resource_id,
            step_number=payload.step_number,
            is_completed=payload.is_completed,
            user_role=effective_user_role,
            subscription_tier=effective_subscription_tier,
        )
        progress_data = get_user_progress(
            db=db,
            user_id=session.user_id,
            resource_id=payload.resource_id,
            user_role=effective_user_role,
            subscription_tier=effective_subscription_tier,
        )
        return progress_data
    except ResourceNotFoundError as err:
        logger.warning("KB progress tracking resource not found: %s", err)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )
    except ResourceAccessDeniedError as err:
        logger.warning("KB progress tracking access denied: %s", err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except KnowledgeBaseError as err:
        logger.warning("KB progress tracking error: %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )
    except Exception as err:
        logger.error("Unexpected error during KB progress tracking: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred while updating user progress.",
                "details": {},
            },
        )

