"""Unit tests for knowledge base domain search, filtering, and RBAC authorization service."""

import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.knowledge_base import (
    KBCategory,
    KBContentType,
    KBResource,
    KBTutorialStep,
)
from src.services.knowledge_base.service import (
    CategoryNotFoundError,
    ResourceAccessDeniedError,
    ResourceNotFoundError,
    StepNotFoundError,
    add_bookmark,
    authorize_resource_access,
    check_resource_access,
    get_category_breadcrumbs,
    get_category_tree,
    get_resource_by_id,
    get_resource_detail,
    get_tutorial_step_navigation,
    get_tutorial_steps,
    get_user_bookmarks,
    get_user_progress,
    is_resource_bookmarked,
    remove_bookmark,
    search_resources,
    update_user_progress,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def seed_kb_data(test_session: Session):
    """Seed test category and resource records with various roles, tiers, and content types."""
    cat_onboarding = KBCategory(
        title="Onboarding",
        slug="onboarding",
        description="Onboarding documents",
        display_order=1,
    )
    cat_getting_started = KBCategory(
        title="Getting Started",
        slug="getting-started",
        description="First steps for new users",
        parent_id=None,
        display_order=2,
    )
    test_session.add_all([cat_onboarding, cat_getting_started])
    test_session.commit()

    cat_sub = KBCategory(
        title="Setup Guides",
        slug="setup-guides",
        parent_id=cat_onboarding.id,
        display_order=1,
    )
    test_session.add(cat_sub)
    test_session.commit()

    res_public_faq = KBResource(
        category_id=cat_onboarding.id,
        title="General FAQ",
        slug="general-faq",
        content_type=KBContentType.FAQ,
        body_text="Frequently asked questions about onboarding and account setup.",
        tags=["faq", "general"],
        onboarding_phase="Phase 1",
        required_role=None,
        min_subscription_tier="Free",
    )

    res_sales_playbook = KBResource(
        category_id=cat_onboarding.id,
        title="Sales Strategy Playbook",
        slug="sales-strategy-playbook",
        content_type=KBContentType.PLAYBOOK,
        body_text="Comprehensive strategy playbook for enterprise sales reps.",
        tags=["sales", "strategy", "playbook"],
        onboarding_phase="Phase 2",
        required_role="Manager",
        min_subscription_tier="Pro",
    )

    res_admin_video = KBResource(
        category_id=cat_getting_started.id,
        title="Admin Security Configuration Video",
        slug="admin-security-video",
        content_type=KBContentType.VIDEO,
        body_text="Video tutorial on administrative security policies and role controls.",
        media_url="https://example.com/stream/admin-security",
        tags=["admin", "security", "video"],
        onboarding_phase="Phase 3",
        required_role="Admin",
        min_subscription_tier="Enterprise",
    )

    test_session.add_all([res_public_faq, res_sales_playbook, res_admin_video])
    test_session.commit()

    return {
        "cat_onboarding": cat_onboarding,
        "cat_getting_started": cat_getting_started,
        "cat_sub": cat_sub,
        "res_public_faq": res_public_faq,
        "res_sales_playbook": res_sales_playbook,
        "res_admin_video": res_admin_video,
    }


def test_check_resource_access_role_and_tier(seed_kb_data):
    """Verify check_resource_access logic for public, role-restricted, and tier-restricted resources."""
    public_res = seed_kb_data["res_public_faq"]
    sales_res = seed_kb_data["res_sales_playbook"]
    admin_res = seed_kb_data["res_admin_video"]

    # Public resource accessible by anyone
    assert check_resource_access(public_res, user_role=None, subscription_tier=None) is True
    assert check_resource_access(public_res, user_role="user", subscription_tier="Free") is True

    # Sales playbook requires Manager role and Pro tier
    assert check_resource_access(sales_res, user_role="user", subscription_tier="Pro") is False
    assert check_resource_access(sales_res, user_role="Manager", subscription_tier="Free") is False
    assert check_resource_access(sales_res, user_role="Manager", subscription_tier="Pro") is True
    assert check_resource_access(sales_res, user_role="Director", subscription_tier="Enterprise") is True

    # Admin video requires Admin role and Enterprise tier
    assert check_resource_access(admin_res, user_role="Manager", subscription_tier="Enterprise") is False
    assert check_resource_access(admin_res, user_role="Admin", subscription_tier="Enterprise") is True
    assert check_resource_access(admin_res, user_role="superadmin", subscription_tier="Pro") is True


def test_authorize_resource_access_raises_denied(seed_kb_data):
    """Verify authorize_resource_access raises ResourceAccessDeniedError when access checks fail."""
    admin_res = seed_kb_data["res_admin_video"]

    with pytest.raises(ResourceAccessDeniedError):
        authorize_resource_access(admin_res, user_role="User", subscription_tier="Free")

    # Should not raise for admin
    authorize_resource_access(admin_res, user_role="Admin", subscription_tier="Enterprise")


def test_search_resources_keyword_and_highlights(test_session: Session, seed_kb_data):
    """Verify domain search returns matching resources with highlighted match snippets."""
    result = search_resources(test_session, query="sales", user_role="Manager", subscription_tier="Pro")

    assert result["total_count"] == 1
    assert len(result["items"]) == 1

    item = result["items"][0]
    assert item["slug"] == "sales-strategy-playbook"
    assert len(item["highlights"]) > 0
    assert any("sales" in h.lower() for h in item["highlights"])


def test_search_resources_content_type_and_metadata_filtering(test_session: Session, seed_kb_data):
    """Verify filtering by content_type, category_id, and onboarding_phase."""
    cat_onboarding = seed_kb_data["cat_onboarding"]

    # Filter by content_type
    faq_result = search_resources(test_session, content_type=KBContentType.FAQ, user_role="Admin", subscription_tier="Enterprise")
    assert faq_result["total_count"] == 1
    assert faq_result["items"][0]["slug"] == "general-faq"

    # Filter by category_id
    cat_result = search_resources(test_session, category_id=cat_onboarding.id, user_role="Admin", subscription_tier="Enterprise")
    assert cat_result["total_count"] == 2

    # Filter by onboarding_phase
    phase_result = search_resources(test_session, onboarding_phase="Phase 2", user_role="Manager", subscription_tier="Pro")
    assert phase_result["total_count"] == 1
    assert phase_result["items"][0]["slug"] == "sales-strategy-playbook"


def test_search_resources_rbac_filtering(test_session: Session, seed_kb_data):
    """Verify RBAC scope filtering omits restricted resources from search results for unauthorized callers."""
    # User role without admin or manager permissions should only see public FAQ
    user_result = search_resources(test_session, user_role="User", subscription_tier="Free")
    assert user_result["total_count"] == 1
    assert user_result["items"][0]["slug"] == "general-faq"

    # Admin role sees all 3 resources
    admin_result = search_resources(test_session, user_role="Admin", subscription_tier="Enterprise")
    assert admin_result["total_count"] == 3


def test_get_resource_by_id_success_and_failures(test_session: Session, seed_kb_data):
    """Verify get_resource_by_id returns resource or raises appropriate exception."""
    public_res = seed_kb_data["res_public_faq"]
    admin_res = seed_kb_data["res_admin_video"]

    # Valid retrieval
    retrieved = get_resource_by_id(test_session, public_res.id, user_role="User", subscription_tier="Free")
    assert retrieved.id == public_res.id

    # Access denied
    with pytest.raises(ResourceAccessDeniedError):
        get_resource_by_id(test_session, admin_res.id, user_role="User", subscription_tier="Free")

    # Not found
    with pytest.raises(ResourceNotFoundError):
        get_resource_by_id(test_session, "non-existent-id", user_role="Admin", subscription_tier="Enterprise")


def test_get_category_tree_and_breadcrumbs(test_session: Session, seed_kb_data):
    """Verify hierarchical category tree and breadcrumb generation."""
    tree = get_category_tree(test_session)
    assert len(tree) == 2  # Onboarding and Getting Started are root categories

    onboarding_node = next(node for node in tree if node["slug"] == "onboarding")
    assert len(onboarding_node["children"]) == 1
    assert onboarding_node["children"][0]["slug"] == "setup-guides"

    sub_cat = seed_kb_data["cat_sub"]
    breadcrumbs = get_category_breadcrumbs(test_session, sub_cat.id)
    assert len(breadcrumbs) == 2
    assert breadcrumbs[0]["slug"] == "onboarding"
    assert breadcrumbs[1]["slug"] == "setup-guides"

    with pytest.raises(CategoryNotFoundError):
        get_category_breadcrumbs(test_session, "missing-category-id")


def test_bookmark_and_progress_operations(test_session: Session, seed_kb_data):
    """Verify bookmark management and tutorial step progress tracking logic."""
    res = seed_kb_data["res_public_faq"]
    user_id = "usr_test_123"

    # Add bookmark
    bm = add_bookmark(test_session, user_id=user_id, resource_id=res.id, user_role="User", subscription_tier="Free")
    assert bm.id is not None
    assert bm.user_id == user_id
    assert bm.resource_id == res.id

    # Remove bookmark
    removed = remove_bookmark(test_session, user_id=user_id, resource_id=res.id)
    assert removed is True

    # Remove non-existent bookmark
    assert remove_bookmark(test_session, user_id=user_id, resource_id=res.id) is False

    # Update user progress
    prog = update_user_progress(test_session, user_id=user_id, resource_id=res.id, step_number=1, is_completed=True)
    assert prog.id is not None
    assert prog.is_completed is True

    # Update existing progress
    prog_updated = update_user_progress(test_session, user_id=user_id, resource_id=res.id, step_number=1, is_completed=False)
    assert prog_updated.id == prog.id
    assert prog_updated.is_completed is False


def test_tutorial_steps_and_navigation(test_session: Session, seed_kb_data):
    """Verify retrieval of tutorial steps and step-by-step sequential navigation."""
    cat = seed_kb_data["cat_onboarding"]

    tutorial_res = KBResource(
        category_id=cat.id,
        title="Workflow Tutorial",
        slug="workflow-tutorial",
        content_type=KBContentType.TUTORIAL,
        body_text="Step-by-step workflow guide.",
    )
    test_session.add(tutorial_res)
    test_session.commit()

    step1 = KBTutorialStep(
        resource_id=tutorial_res.id,
        step_number=1,
        title="Step 1: Introduction",
        content="Welcome to the workflow tutorial.",
    )
    step2 = KBTutorialStep(
        resource_id=tutorial_res.id,
        step_number=2,
        title="Step 2: Configuration",
        content="Configure your preferences.",
    )
    step3 = KBTutorialStep(
        resource_id=tutorial_res.id,
        step_number=3,
        title="Step 3: Verification",
        content="Verify setup complete.",
    )
    test_session.add_all([step1, step2, step3])
    test_session.commit()

    steps = get_tutorial_steps(test_session, tutorial_res.id, user_role="User", subscription_tier="Free")
    assert len(steps) == 3
    assert steps[0]["step_number"] == 1
    assert steps[2]["step_number"] == 3

    # Test step navigation for step 2
    nav = get_tutorial_step_navigation(test_session, tutorial_res.id, step_number=2)
    assert nav["current_step_number"] == 2
    assert nav["total_steps"] == 3
    assert nav["has_previous"] is True
    assert nav["prev_step_number"] == 1
    assert nav["has_next"] is True
    assert nav["next_step_number"] == 3
    assert nav["is_last_step"] is False

    # Test step navigation for last step (step 3)
    nav_last = get_tutorial_step_navigation(test_session, tutorial_res.id, step_number=3)
    assert nav_last["has_previous"] is True
    assert nav_last["prev_step_number"] == 2
    assert nav_last["has_next"] is False
    assert nav_last["next_step_number"] is None
    assert nav_last["is_last_step"] is True

    # Test invalid step number raises StepNotFoundError
    with pytest.raises(StepNotFoundError):
        get_tutorial_step_navigation(test_session, tutorial_res.id, step_number=99)


def test_get_user_bookmarks_and_is_bookmarked(test_session: Session, seed_kb_data):
    """Verify listing user bookmarks and checking bookmarked state."""
    res_public = seed_kb_data["res_public_faq"]
    res_admin = seed_kb_data["res_admin_video"]
    user_id = "usr_bookmark_test"

    assert is_resource_bookmarked(test_session, user_id, res_public.id) is False

    add_bookmark(test_session, user_id=user_id, resource_id=res_public.id)
    add_bookmark(test_session, user_id=user_id, resource_id=res_admin.id, user_role="Admin", subscription_tier="Enterprise")

    assert is_resource_bookmarked(test_session, user_id, res_public.id) is True

    # User with regular role should only see accessible bookmarked resources
    user_bms = get_user_bookmarks(test_session, user_id=user_id, user_role="User", subscription_tier="Free")
    assert len(user_bms) == 1
    assert user_bms[0]["id"] == res_public.id

    # Admin should see both bookmarked resources
    admin_bms = get_user_bookmarks(test_session, user_id=user_id, user_role="Admin", subscription_tier="Enterprise")
    assert len(admin_bms) == 2


def test_get_user_progress_and_automatic_tutorial_completion(test_session: Session, seed_kb_data):
    """Verify tracking user progress and automatic overall tutorial completion upon finishing all steps."""
    cat = seed_kb_data["cat_onboarding"]
    tutorial_res = KBResource(
        category_id=cat.id,
        title="Multi-Step Tutorial",
        slug="multi-step-tutorial",
        content_type=KBContentType.TUTORIAL,
    )
    test_session.add(tutorial_res)
    test_session.commit()

    step1 = KBTutorialStep(resource_id=tutorial_res.id, step_number=1, title="S1", content="C1")
    step2 = KBTutorialStep(resource_id=tutorial_res.id, step_number=2, title="S2", content="C2")
    test_session.add_all([step1, step2])
    test_session.commit()

    user_id = "usr_prog_test"

    prog_initial = get_user_progress(test_session, user_id, tutorial_res.id)
    assert prog_initial["is_completed"] is False
    assert prog_initial["completion_percentage"] == 0.0

    # Complete step 1
    update_user_progress(test_session, user_id, tutorial_res.id, step_number=1, is_completed=True)
    prog_step1 = get_user_progress(test_session, user_id, tutorial_res.id)
    assert prog_step1["is_completed"] is False
    assert prog_step1["completed_step_numbers"] == [1]
    assert prog_step1["completion_percentage"] == 50.0

    # Complete step 2 -> should automatically complete overall tutorial
    update_user_progress(test_session, user_id, tutorial_res.id, step_number=2, is_completed=True)
    prog_step2 = get_user_progress(test_session, user_id, tutorial_res.id)
    assert prog_step2["is_completed"] is True
    assert prog_step2["completed_step_numbers"] == [1, 2]
    assert prog_step2["completion_percentage"] == 100.0


def test_get_resource_detail_aggregated_payload(test_session: Session, seed_kb_data):
    """Verify get_resource_detail returns aggregated resource summary with breadcrumbs, steps, and user state."""
    res_public = seed_kb_data["res_public_faq"]
    user_id = "usr_detail_test"

    add_bookmark(test_session, user_id=user_id, resource_id=res_public.id)
    update_user_progress(test_session, user_id=user_id, resource_id=res_public.id, is_completed=True)

    detail = get_resource_detail(test_session, res_public.id, user_id=user_id, user_role="User", subscription_tier="Free")
    assert detail["id"] == res_public.id
    assert len(detail["breadcrumbs"]) > 0
    assert detail["is_bookmarked"] is True
    assert detail["progress"] is not None
    assert detail["progress"]["is_completed"] is True
