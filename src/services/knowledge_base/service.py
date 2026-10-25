"""Domain search, metadata filtering, authorization checks, and resource management service."""

import logging
import re
from datetime import datetime, timezone
from typing import Any, List, Optional, Union

from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from src.models.knowledge_base import (
    KBBookmark,
    KBCategory,
    KBContentType,
    KBResource,
    KBTutorialStep,
    KBUserProgress,
)

logger = logging.getLogger(__name__)

# Role hierarchy levels
ROLE_HIERARCHY: dict[str, int] = {
    "admin": 100,
    "administrator": 100,
    "superadmin": 100,
    "system": 100,
    "director": 80,
    "executive": 80,
    "manager": 50,
    "lead": 50,
    "member": 20,
    "user": 20,
    "agent": 20,
    "guest": 10,
    "viewer": 10,
}

# Subscription tier hierarchy levels
TIER_HIERARCHY: dict[str, int] = {
    "enterprise": 100,
    "premium": 70,
    "pro": 50,
    "professional": 50,
    "basic": 20,
    "standard": 20,
    "free": 10,
}

# Admin bypass role identifiers
ADMIN_ROLES = {"admin", "administrator", "superadmin", "system", "*", "all"}


class KnowledgeBaseError(Exception):
    """Base exception for Knowledge Base service operations."""

    pass


class ResourceNotFoundError(KnowledgeBaseError):
    """Raised when a knowledge base resource cannot be found."""

    pass


class CategoryNotFoundError(KnowledgeBaseError):
    """Raised when a category cannot be found."""

    pass


class ResourceAccessDeniedError(KnowledgeBaseError):
    """Raised when role or subscription tier authorization checks fail."""

    pass


class StepNotFoundError(KnowledgeBaseError):
    """Raised when a tutorial step cannot be found."""

    pass


def _norm(value: Optional[str]) -> str:
    """Normalize string value for case-insensitive comparison."""
    return value.strip().lower() if value else ""


def _check_role_access(required_role: Optional[str], user_role: Optional[str]) -> bool:
    """Evaluate whether user role meets or exceeds required role."""
    req_clean = _norm(required_role)
    if not req_clean or req_clean in {"public", "all", "*"}:
        return True

    user_clean = _norm(user_role)
    if not user_clean:
        return False

    if user_clean in ADMIN_ROLES or user_clean == req_clean:
        return True

    user_level = ROLE_HIERARCHY.get(user_clean, 0)
    req_level = ROLE_HIERARCHY.get(req_clean, 0)
    return user_level >= req_level if req_level > 0 else False


def _check_tier_access(min_tier: Optional[str], user_tier: Optional[str]) -> bool:
    """Evaluate whether user subscription tier meets or exceeds required tier."""
    min_clean = _norm(min_tier)
    if not min_clean or min_clean in {"free", "public", "all", "*"}:
        return True

    user_clean = _norm(user_tier)
    if not user_clean:
        return False

    if user_clean in ADMIN_ROLES or user_clean == min_clean:
        return True

    user_level = TIER_HIERARCHY.get(user_clean, 0)
    min_level = TIER_HIERARCHY.get(min_clean, 0)
    return user_level >= min_level if min_level > 0 else False


def check_resource_access(
    resource: KBResource,
    user_role: Optional[str] = None,
    subscription_tier: Optional[str] = None,
) -> bool:
    """Check if user role and subscription tier grant access to a resource."""
    role_ok = _check_role_access(resource.required_role, user_role)
    tier_ok = _check_tier_access(resource.min_subscription_tier, subscription_tier)
    return role_ok and tier_ok


def authorize_resource_access(
    resource: KBResource,
    user_role: Optional[str] = None,
    subscription_tier: Optional[str] = None,
) -> None:
    """Enforce resource access permissions, raising ResourceAccessDeniedError if denied."""
    if not check_resource_access(resource, user_role, subscription_tier):
        logger.warning(
            "Access denied to resource %s: user_role=%s, required_role=%s, user_tier=%s, min_tier=%s",
            resource.id,
            user_role,
            resource.required_role,
            subscription_tier,
            resource.min_subscription_tier,
        )
        raise ResourceAccessDeniedError(
            f"Access denied for resource '{resource.id}'. Insufficient role or subscription tier."
        )


def _highlight_snippet(text: str, query: str) -> Optional[str]:
    """Generate a highlighted text snippet wrapping query terms in mark tags."""
    if not text or not query:
        return None

    match = re.search(re.escape(query), text, re.IGNORECASE)
    if not match:
        return None

    start_idx, end_idx = match.span()
    snippet_start = max(0, start_idx - 40)
    snippet_end = min(len(text), end_idx + 40)

    prefix = "..." if snippet_start > 0 else ""
    suffix = "..." if snippet_end < len(text) else ""

    matched_text = text[start_idx:end_idx]
    snippet = f"{prefix}{text[snippet_start:start_idx]}<mark>{matched_text}</mark>{text[end_idx:snippet_end]}{suffix}"
    return snippet


def generate_highlights(resource: KBResource, query: str) -> List[str]:
    """Produce highlighted match snippets across resource fields for search queries."""
    if not query or not query.strip():
        return []

    q = query.strip()
    highlights: List[str] = []

    if resource.title and re.search(re.escape(q), resource.title, re.IGNORECASE):
        h = _highlight_snippet(resource.title, q)
        if h:
            highlights.append(f"Title: {h}")

    if resource.body_text and re.search(re.escape(q), resource.body_text, re.IGNORECASE):
        h = _highlight_snippet(resource.body_text, q)
        if h:
            highlights.append(f"Content: {h}")

    if resource.tags and any(q.lower() in str(tag).lower() for tag in resource.tags):
        highlights.append(f"Tag match: {q}")

    if resource.category and resource.category.title and re.search(re.escape(q), resource.category.title, re.IGNORECASE):
        highlights.append(f"Category: {resource.category.title}")

    return highlights


def _serialize_resource_summary(
    resource: KBResource,
    query: Optional[str] = None,
) -> dict[str, Any]:
    """Format KBResource model instance into a summary dictionary response."""
    ct_val = resource.content_type.value if hasattr(resource.content_type, "value") else str(resource.content_type)
    pub_iso = resource.published_on.isoformat() if resource.published_on else None
    cat_title = resource.category.title if resource.category else None

    return {
        "id": resource.id,
        "category_id": resource.category_id,
        "category_title": cat_title,
        "title": resource.title,
        "slug": resource.slug,
        "content_type": ct_val,
        "body_text": resource.body_text,
        "media_url": resource.media_url,
        "tags": resource.tags or [],
        "onboarding_phase": resource.onboarding_phase,
        "required_role": resource.required_role,
        "min_subscription_tier": resource.min_subscription_tier,
        "published_on": pub_iso,
        "highlights": generate_highlights(resource, query) if query else [],
    }


def _apply_content_filters(
    query_obj: Any,
    content_type: Optional[Union[KBContentType, str]] = None,
    category_id: Optional[str] = None,
    onboarding_phase: Optional[str] = None,
    query: Optional[str] = None,
) -> Any:
    """Apply query filters for content type, category, onboarding phase, and search keywords."""
    if content_type:
        ct_enum = KBContentType(content_type) if isinstance(content_type, str) else content_type
        query_obj = query_obj.filter(KBResource.content_type == ct_enum)

    if category_id:
        query_obj = query_obj.filter(KBResource.category_id == category_id)

    if onboarding_phase and onboarding_phase.strip():
        query_obj = query_obj.filter(KBResource.onboarding_phase.ilike(f"%{onboarding_phase.strip()}%"))

    if query and query.strip():
        q = query.strip()
        search_filter = or_(
            KBResource.title.ilike(f"%{q}%"),
            KBResource.body_text.ilike(f"%{q}%"),
            KBResource.onboarding_phase.ilike(f"%{q}%"),
            KBCategory.title.ilike(f"%{q}%"),
            cast(KBResource.tags, String).ilike(f"%{q}%"),
        )
        query_obj = query_obj.filter(search_filter)

    return query_obj


def search_resources(
    db: Session,
    query: Optional[str] = None,
    content_type: Optional[Union[KBContentType, str]] = None,
    category_id: Optional[str] = None,
    onboarding_phase: Optional[str] = None,
    user_role: Optional[str] = None,
    subscription_tier: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    """Execute domain search across KB resources with metadata filtering and RBAC checks."""
    safe_page = max(1, page)
    safe_limit = max(1, min(100, limit))

    query_obj = db.query(KBResource).join(KBCategory, KBResource.category_id == KBCategory.id)
    query_obj = _apply_content_filters(query_obj, content_type, category_id, onboarding_phase, query)

    candidate_resources = query_obj.all()
    accessible_resources = [
        r for r in candidate_resources if check_resource_access(r, user_role, subscription_tier)
    ]

    total_count = len(accessible_resources)
    offset = (safe_page - 1) * safe_limit
    paginated = accessible_resources[offset : offset + safe_limit]

    items = [_serialize_resource_summary(r, query) for r in paginated]

    return {
        "items": items,
        "total_count": total_count,
        "page": safe_page,
        "limit": safe_limit,
    }


def get_resource_by_id(
    db: Session,
    resource_id: str,
    user_role: Optional[str] = None,
    subscription_tier: Optional[str] = None,
) -> KBResource:
    """Fetch single resource by ID and authorize user role/tier access."""
    resource = db.query(KBResource).filter(KBResource.id == resource_id).first()
    if not resource:
        raise ResourceNotFoundError(f"Knowledge base resource '{resource_id}' was not found.")

    authorize_resource_access(resource, user_role, subscription_tier)
    return resource


def _build_category_tree_node(cat: KBCategory) -> dict[str, Any]:
    """Convert KBCategory ORM model node into a nested dictionary structure."""
    children_nodes = [_build_category_tree_node(child) for child in cat.children]
    children_nodes.sort(key=lambda x: (x.get("display_order", 0), x.get("title", "")))

    return {
        "id": cat.id,
        "title": cat.title,
        "slug": cat.slug,
        "description": cat.description,
        "parent_id": cat.parent_id,
        "display_order": cat.display_order,
        "created_on": cat.created_on.isoformat() if cat.created_on else None,
        "children": children_nodes,
    }


def get_category_tree(db: Session) -> List[dict[str, Any]]:
    """Retrieve top-level category hierarchy tree."""
    root_categories = (
        db.query(KBCategory)
        .filter(KBCategory.parent_id.is_(None))
        .order_by(KBCategory.display_order.asc(), KBCategory.title.asc())
        .all()
    )
    return [_build_category_tree_node(cat) for cat in root_categories]


def get_category_breadcrumbs(db: Session, category_id: str) -> List[dict[str, Any]]:
    """Generate ordered list of category breadcrumb nodes from root down to category_id."""
    breadcrumbs: List[dict[str, Any]] = []
    current_id: Optional[str] = category_id

    while current_id:
        cat = db.query(KBCategory).filter(KBCategory.id == current_id).first()
        if not cat:
            if not breadcrumbs:
                raise CategoryNotFoundError(f"Category '{category_id}' was not found.")
            break

        breadcrumbs.insert(
            0,
            {
                "id": cat.id,
                "title": cat.title,
                "slug": cat.slug,
            },
        )
        current_id = cat.parent_id

    return breadcrumbs


def add_bookmark(
    db: Session,
    user_id: str,
    resource_id: str,
    user_role: Optional[str] = None,
    subscription_tier: Optional[str] = None,
) -> KBBookmark:
    """Bookmark a resource for a user after verifying resource existence and access."""
    resource = get_resource_by_id(db, resource_id, user_role, subscription_tier)

    existing = (
        db.query(KBBookmark)
        .filter(KBBookmark.user_id == user_id, KBBookmark.resource_id == resource.id)
        .first()
    )
    if existing:
        return existing

    bookmark = KBBookmark(user_id=user_id, resource_id=resource.id)
    db.add(bookmark)
    db.commit()
    db.refresh(bookmark)
    return bookmark


def remove_bookmark(db: Session, user_id: str, resource_id: str) -> bool:
    """Remove a user's bookmark for a given resource."""
    bookmark = (
        db.query(KBBookmark)
        .filter(KBBookmark.user_id == user_id, KBBookmark.resource_id == resource_id)
        .first()
    )
    if not bookmark:
        return False

    db.delete(bookmark)
    db.commit()
    return True


def is_resource_bookmarked(
    db: Session,
    user_id: str,
    resource_id: str,
) -> bool:
    """Check if a specific resource is bookmarked by a user."""
    bm = (
        db.query(KBBookmark)
        .filter(KBBookmark.user_id == user_id, KBBookmark.resource_id == resource_id)
        .first()
    )
    return bm is not None


def get_user_bookmarks(
    db: Session,
    user_id: str,
    user_role: Optional[str] = None,
    subscription_tier: Optional[str] = None,
) -> List[dict[str, Any]]:
    """Retrieve all accessible bookmarked resources for a given user."""
    bookmarks = (
        db.query(KBBookmark)
        .filter(KBBookmark.user_id == user_id)
        .order_by(KBBookmark.created_on.desc())
        .all()
    )
    results: List[dict[str, Any]] = []
    for bm in bookmarks:
        if bm.resource and check_resource_access(bm.resource, user_role, subscription_tier):
            summary = _serialize_resource_summary(bm.resource)
            summary["bookmarked_on"] = bm.created_on.isoformat() if bm.created_on else None
            results.append(summary)
    return results


def _evaluate_overall_tutorial_completion(
    db: Session,
    user_id: str,
    resource_id: str,
) -> None:
    """Check if all tutorial steps are completed, marking overall resource progress completed if so."""
    all_steps = (
        db.query(KBTutorialStep.step_number)
        .filter(KBTutorialStep.resource_id == resource_id)
        .all()
    )
    if not all_steps:
        return

    total_step_count = len(all_steps)
    step_numbers = [s[0] for s in all_steps]

    completed_count = (
        db.query(KBUserProgress)
        .filter(
            KBUserProgress.user_id == user_id,
            KBUserProgress.resource_id == resource_id,
            KBUserProgress.step_number.in_(step_numbers),
            KBUserProgress.is_completed.is_(True),
        )
        .count()
    )

    if completed_count == total_step_count:
        overall_prog = (
            db.query(KBUserProgress)
            .filter(
                KBUserProgress.user_id == user_id,
                KBUserProgress.resource_id == resource_id,
                KBUserProgress.step_number.is_(None),
            )
            .first()
        )
        now = datetime.now(timezone.utc)
        if overall_prog:
            overall_prog.is_completed = True
            overall_prog.last_accessed_on = now
        else:
            overall_prog = KBUserProgress(
                user_id=user_id,
                resource_id=resource_id,
                step_number=None,
                is_completed=True,
                last_accessed_on=now,
            )
            db.add(overall_prog)
        db.commit()


def update_user_progress(
    db: Session,
    user_id: str,
    resource_id: str,
    step_number: Optional[int] = None,
    is_completed: bool = True,
    user_role: Optional[str] = None,
    subscription_tier: Optional[str] = None,
) -> KBUserProgress:
    """Record or update user completion progress for a resource or tutorial step."""
    resource = get_resource_by_id(db, resource_id, user_role, subscription_tier)

    progress = (
        db.query(KBUserProgress)
        .filter(
            KBUserProgress.user_id == user_id,
            KBUserProgress.resource_id == resource.id,
            KBUserProgress.step_number == step_number,
        )
        .first()
    )

    now = datetime.now(timezone.utc)
    if progress:
        progress.is_completed = is_completed
        progress.last_accessed_on = now
    else:
        progress = KBUserProgress(
            user_id=user_id,
            resource_id=resource.id,
            step_number=step_number,
            is_completed=is_completed,
            last_accessed_on=now,
        )
        db.add(progress)

    db.commit()

    if step_number is not None and is_completed:
        _evaluate_overall_tutorial_completion(db, user_id, resource.id)

    db.refresh(progress)
    return progress


def get_user_progress(
    db: Session,
    user_id: str,
    resource_id: str,
    user_role: Optional[str] = None,
    subscription_tier: Optional[str] = None,
) -> dict[str, Any]:
    """Retrieve completion progress status for a user on a given resource."""
    resource = get_resource_by_id(db, resource_id, user_role, subscription_tier)

    progress_records = (
        db.query(KBUserProgress)
        .filter(
            KBUserProgress.user_id == user_id,
            KBUserProgress.resource_id == resource.id,
        )
        .all()
    )

    overall_record = next((p for p in progress_records if p.step_number is None), None)
    is_completed = overall_record.is_completed if overall_record else False

    completed_step_numbers = [
        p.step_number
        for p in progress_records
        if p.step_number is not None and p.is_completed
    ]

    all_steps = (
        db.query(KBTutorialStep)
        .filter(KBTutorialStep.resource_id == resource.id)
        .all()
    )
    total_steps = len(all_steps)

    percentage = 0.0
    if total_steps > 0:
        percentage = min(100.0, (len(completed_step_numbers) / total_steps) * 100.0)
    elif is_completed:
        percentage = 100.0

    return {
        "resource_id": resource.id,
        "user_id": user_id,
        "is_completed": is_completed,
        "completed_step_numbers": sorted(completed_step_numbers),
        "total_steps": total_steps,
        "completion_percentage": round(percentage, 2),
        "last_accessed_on": (
            overall_record.last_accessed_on.isoformat()
            if overall_record and overall_record.last_accessed_on
            else None
        ),
    }


def get_tutorial_steps(
    db: Session,
    resource_id: str,
    user_role: Optional[str] = None,
    subscription_tier: Optional[str] = None,
) -> List[dict[str, Any]]:
    """Retrieve ordered list of tutorial steps for a given resource after verifying access."""
    resource = get_resource_by_id(db, resource_id, user_role, subscription_tier)
    steps = (
        db.query(KBTutorialStep)
        .filter(KBTutorialStep.resource_id == resource.id)
        .order_by(KBTutorialStep.step_number.asc())
        .all()
    )
    return [
        {
            "id": s.id,
            "resource_id": s.resource_id,
            "step_number": s.step_number,
            "title": s.title,
            "content": s.content,
            "video_url": s.video_url,
        }
        for s in steps
    ]


def get_tutorial_step_navigation(
    db: Session,
    resource_id: str,
    step_number: int,
    user_role: Optional[str] = None,
    subscription_tier: Optional[str] = None,
) -> dict[str, Any]:
    """Retrieve step details and sequential navigation controls (prev/next) for a tutorial step."""
    resource = get_resource_by_id(db, resource_id, user_role, subscription_tier)
    all_steps = (
        db.query(KBTutorialStep)
        .filter(KBTutorialStep.resource_id == resource.id)
        .order_by(KBTutorialStep.step_number.asc())
        .all()
    )
    if not all_steps:
        raise StepNotFoundError(f"Resource '{resource_id}' has no tutorial steps.")

    target_step = next((s for s in all_steps if s.step_number == step_number), None)
    if not target_step:
        raise StepNotFoundError(
            f"Step {step_number} was not found for resource '{resource_id}'."
        )

    prev_steps = [s.step_number for s in all_steps if s.step_number < step_number]
    prev_step_number = max(prev_steps) if prev_steps else None

    next_steps = [s.step_number for s in all_steps if s.step_number > step_number]
    next_step_number = min(next_steps) if next_steps else None

    return {
        "step": {
            "id": target_step.id,
            "resource_id": target_step.resource_id,
            "step_number": target_step.step_number,
            "title": target_step.title,
            "content": target_step.content,
            "video_url": target_step.video_url,
        },
        "current_step_number": step_number,
        "total_steps": len(all_steps),
        "has_previous": prev_step_number is not None,
        "has_next": next_step_number is not None,
        "prev_step_number": prev_step_number,
        "next_step_number": next_step_number,
        "is_last_step": next_step_number is None,
    }


def get_resource_detail(
    db: Session,
    resource_id: str,
    user_id: Optional[str] = None,
    user_role: Optional[str] = None,
    subscription_tier: Optional[str] = None,
) -> dict[str, Any]:
    """Retrieve full resource details including breadcrumbs, tutorial steps, bookmark state, and progress."""
    resource = get_resource_by_id(db, resource_id, user_role, subscription_tier)
    summary = _serialize_resource_summary(resource)

    summary["breadcrumbs"] = get_category_breadcrumbs(db, resource.category_id)

    if resource.content_type == KBContentType.TUTORIAL:
        summary["tutorial_steps"] = get_tutorial_steps(db, resource.id, user_role, subscription_tier)
    else:
        summary["tutorial_steps"] = []

    if user_id:
        summary["is_bookmarked"] = is_resource_bookmarked(db, user_id, resource.id)
        summary["progress"] = get_user_progress(db, user_id, resource.id, user_role, subscription_tier)
    else:
        summary["is_bookmarked"] = False
        summary["progress"] = None

    return summary

