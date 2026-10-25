"""Integration tests for Knowledge Base API endpoints in backend/src/api/knowledge_base.py."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.deps import get_current_session
from src.api.knowledge_base import router as kb_router
from src.models.base import Base, create_db_engine, get_db
from src.models.knowledge_base import KBCategory, KBContentType, KBResource, KBTutorialStep
from src.models.session import Session as SessionModel
from src.models.tenant import Tenant as TenantModel
from src.models.user import User as UserModel


@pytest.fixture
def test_db():
    """Fixture providing an isolated in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with DBSession(engine) as session:
        yield session


@pytest.fixture
def test_session_data(test_db: DBSession):
    """Fixture creating tenant, user, and active session records."""
    tenant = TenantModel(name="KB Test Tenant")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="kb_user@example.com",
        password_hash="hashed_pw",
        full_name="KB Test User",
        is_active=True,
    )
    test_db.add(user)
    test_db.flush()

    session_record = SessionModel(
        user_id=user.id,
        tenant_id=tenant.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    test_db.add(session_record)
    test_db.commit()
    test_db.refresh(session_record)
    return session_record


@pytest.fixture
def client(test_db: DBSession):
    """Fixture providing FastAPI test client with DB override."""
    app = FastAPI()
    app.include_router(kb_router)

    def _get_db_override():
        yield test_db

    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


@pytest.fixture
def seed_kb_resources(test_db: DBSession):
    """Seed categories and resources for integration testing."""
    cat_onboarding = KBCategory(
        title="Client Onboarding",
        slug="client-onboarding",
        description="Onboarding documents and guides",
        display_order=1,
    )
    cat_tech = KBCategory(
        title="Technical Guides",
        slug="technical-guides",
        description="Technical documentation",
        display_order=2,
    )
    test_db.add_all([cat_onboarding, cat_tech])
    test_db.commit()

    res1 = KBResource(
        category_id=cat_onboarding.id,
        title="Welcome FAQ",
        slug="welcome-faq",
        content_type=KBContentType.FAQ,
        body_text="Welcome to client onboarding. Here are the initial steps.",
        tags=["welcome", "faq", "onboarding"],
        onboarding_phase="Phase 1",
        required_role=None,
        min_subscription_tier="Free",
    )

    res2 = KBResource(
        category_id=cat_onboarding.id,
        title="Sales Process Playbook",
        slug="sales-process-playbook",
        content_type=KBContentType.PLAYBOOK,
        body_text="Detailed sales strategy playbook for managers.",
        tags=["sales", "playbook"],
        onboarding_phase="Phase 2",
        required_role="Manager",
        min_subscription_tier="Pro",
    )

    res3 = KBResource(
        category_id=cat_tech.id,
        title="Admin Security Setup Video",
        slug="admin-security-setup-video",
        content_type=KBContentType.VIDEO,
        body_text="Video walkthrough of security policies for administrators.",
        media_url="https://example.com/stream/admin-sec",
        tags=["admin", "security", "video"],
        onboarding_phase="Phase 3",
        required_role="Admin",
        min_subscription_tier="Enterprise",
    )

    res_tutorial = KBResource(
        category_id=cat_onboarding.id,
        title="Interactive Setup Tutorial",
        slug="interactive-setup-tutorial",
        content_type=KBContentType.TUTORIAL,
        body_text="Step-by-step interactive onboarding tutorial.",
        tags=["tutorial", "setup"],
        onboarding_phase="Phase 1",
        required_role=None,
        min_subscription_tier="Free",
    )

    test_db.add_all([res1, res2, res3, res_tutorial])
    test_db.commit()

    step1 = KBTutorialStep(
        resource_id=res_tutorial.id,
        step_number=1,
        title="Step 1: Account Creation",
        content="Fill out the initial profile setup details.",
        video_url="https://example.com/tutorial-step1.mp4",
    )
    step2 = KBTutorialStep(
        resource_id=res_tutorial.id,
        step_number=2,
        title="Step 2: Team Invitation",
        content="Invite members to your workspace.",
    )
    test_db.add_all([step1, step2])
    test_db.commit()

    return {
        "cat_onboarding": cat_onboarding,
        "cat_tech": cat_tech,
        "res1": res1,
        "res2": res2,
        "res3": res3,
        "res_tutorial": res_tutorial,
        "step1": step1,
        "step2": step2,
    }


def test_search_resources_unauthenticated(client):
    """Verify GET /api/v1/kb/resources returns 401 when unauthenticated."""
    response = client.get("/api/v1/kb/resources")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert data["detail"]["code"] == "UNAUTHORIZED"


def test_search_resources_authenticated_default(client, test_session_data, seed_kb_resources):
    """Verify GET /api/v1/kb/resources returns public resources for authenticated user."""
    headers = {"Authorization": f"Bearer {test_session_data.id}"}
    response = client.get("/api/v1/kb/resources", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert "items" in data
    assert "total_count" in data
    assert data["page"] == 1
    assert data["limit"] == 10

    # Without role/tier headers, default user can only access public resources
    items = data["items"]
    assert len(items) == 2
    titles = [item["title"] for item in items]
    assert "Welcome FAQ" in titles
    assert "Interactive Setup Tutorial" in titles


def test_search_resources_query_highlighting(client, test_session_data, seed_kb_resources):
    """Verify search query keyword matching and snippet highlighting."""
    headers = {"Authorization": f"Bearer {test_session_data.id}"}
    response = client.get("/api/v1/kb/resources?query=onboarding", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert len(data["items"]) == 1
    res_item = data["items"][0]
    assert "highlights" in res_item
    assert len(res_item["highlights"]) > 0
    assert any("<mark>" in h for h in res_item["highlights"])


def test_search_resources_content_type_filter(client, test_session_data, seed_kb_resources):
    """Verify filtering by content_type enum query parameter."""
    headers = {
        "Authorization": f"Bearer {test_session_data.id}",
        "X-User-Role": "Manager",
        "X-Subscription-Tier": "Pro",
    }
    response = client.get("/api/v1/kb/resources?content_type=PLAYBOOK", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["title"] == "Sales Process Playbook"


def test_search_resources_category_id_filter(client, test_session_data, seed_kb_resources):
    """Verify filtering by category_id query parameter."""
    headers = {
        "Authorization": f"Bearer {test_session_data.id}",
        "X-User-Role": "Admin",
        "X-Subscription-Tier": "Enterprise",
    }
    cat_tech_id = seed_kb_resources["cat_tech"].id
    response = client.get(f"/api/v1/kb/resources?category_id={cat_tech_id}", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["title"] == "Admin Security Setup Video"


def test_search_resources_rbac_role_and_tier_headers(client, test_session_data, seed_kb_resources):
    """Verify role and tier header enforcement filters out unauthorized resources."""
    headers_manager = {
        "Authorization": f"Bearer {test_session_data.id}",
        "X-User-Role": "Manager",
        "X-Subscription-Tier": "Pro",
    }
    res = client.get("/api/v1/kb/resources", headers=headers_manager)
    assert res.status_code == status.HTTP_200_OK
    titles = [item["title"] for item in res.json()["items"]]
    assert "Welcome FAQ" in titles
    assert "Sales Process Playbook" in titles
    assert "Admin Security Setup Video" not in titles

    headers_admin = {
        "Authorization": f"Bearer {test_session_data.id}",
        "X-User-Role": "Admin",
        "X-Subscription-Tier": "Enterprise",
    }
    res_admin = client.get("/api/v1/kb/resources", headers=headers_admin)
    assert res_admin.status_code == status.HTTP_200_OK
    admin_titles = [item["title"] for item in res_admin.json()["items"]]
    assert len(admin_titles) == 4


def test_search_resources_onboarding_phase_filter(client, test_session_data, seed_kb_resources):
    """Verify filtering resources by onboarding_phase query parameter."""
    headers = {
        "Authorization": f"Bearer {test_session_data.id}",
        "X-User-Role": "Admin",
        "X-Subscription-Tier": "Enterprise",
    }
    response = client.get("/api/v1/kb/resources?onboarding_phase=Phase 1", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    titles = [item["title"] for item in data["items"]]
    assert "Welcome FAQ" in titles
    assert "Interactive Setup Tutorial" in titles
    assert "Sales Process Playbook" not in titles


def test_search_resources_query_params_role_and_tier(client, test_session_data, seed_kb_resources):
    """Verify role and tier query parameter overrides authorize resource access."""
    headers = {"Authorization": f"Bearer {test_session_data.id}"}
    response = client.get(
        "/api/v1/kb/resources?user_role=Admin&subscription_tier=Enterprise",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["total_count"] == 4


def test_get_kb_category_tree_unauthenticated(client):
    """Verify GET /api/v1/kb/categories returns 401 when unauthenticated."""
    response = client.get("/api/v1/kb/categories")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert data["detail"]["code"] == "UNAUTHORIZED"


def test_get_kb_category_tree_authenticated(client, test_session_data, seed_kb_resources):
    """Verify GET /api/v1/kb/categories returns hierarchical category tree."""
    headers = {"Authorization": f"Bearer {test_session_data.id}"}
    response = client.get("/api/v1/kb/categories", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    categories = response.json()
    assert isinstance(categories, list)
    assert len(categories) == 2
    titles = [cat["title"] for cat in categories]
    assert "Client Onboarding" in titles
    assert "Technical Guides" in titles


def test_get_kb_resource_detail_success(client, test_session_data, seed_kb_resources):
    """Verify GET /api/v1/kb/resources/{id} retrieves resource details and breadcrumbs."""
    headers = {"Authorization": f"Bearer {test_session_data.id}"}
    res1_id = seed_kb_resources["res1"].id

    response = client.get(f"/api/v1/kb/resources/{res1_id}", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["id"] == res1_id
    assert data["title"] == "Welcome FAQ"
    assert data["content_type"] == "FAQ"
    assert "breadcrumbs" in data
    assert len(data["breadcrumbs"]) == 1
    assert data["breadcrumbs"][0]["title"] == "Client Onboarding"
    assert data["is_bookmarked"] is False


def test_get_kb_resource_detail_video_media_embed(client, test_session_data, seed_kb_resources):
    """Verify GET /api/v1/kb/resources/{id} enriches video resources with media player embed configuration."""
    headers = {
        "Authorization": f"Bearer {test_session_data.id}",
        "X-User-Role": "Admin",
        "X-Subscription-Tier": "Enterprise",
    }
    res3_id = seed_kb_resources["res3"].id

    response = client.get(f"/api/v1/kb/resources/{res3_id}", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["id"] == res3_id
    assert data["title"] == "Admin Security Setup Video"
    assert data["content_type"] == "VIDEO"
    assert data["media_embed"] is not None
    assert "embed_url" in data["media_embed"]
    assert "sanitized_iframe" in data["media_embed"]


def test_get_kb_resource_detail_not_found(client, test_session_data):
    """Verify GET /api/v1/kb/resources/{id} returns 404 when resource does not exist."""
    headers = {"Authorization": f"Bearer {test_session_data.id}"}
    non_existent_id = "00000000-0000-0000-0000-000000000000"

    response = client.get(f"/api/v1/kb/resources/{non_existent_id}", headers=headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["detail"]["code"] == "NOT_FOUND"


def test_get_kb_resource_detail_forbidden(client, test_session_data, seed_kb_resources):
    """Verify GET /api/v1/kb/resources/{id} returns 403 when user lacks required role/tier."""
    headers = {"Authorization": f"Bearer {test_session_data.id}"}
    res2_id = seed_kb_resources["res2"].id  # requires Manager / Pro

    response = client.get(f"/api/v1/kb/resources/{res2_id}", headers=headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    data = response.json()
    assert data["detail"]["code"] == "FORBIDDEN"


def test_create_and_delete_kb_bookmark(client, test_session_data, seed_kb_resources):
    """Verify POST and DELETE endpoints for bookmarks (returning 204 No Content for DELETE)."""
    headers = {"Authorization": f"Bearer {test_session_data.id}"}
    res1_id = seed_kb_resources["res1"].id

    # Create bookmark
    post_res = client.post(
        "/api/v1/kb/bookmarks",
        headers=headers,
        json={"resource_id": res1_id},
    )
    assert post_res.status_code == status.HTTP_201_CREATED
    bookmark_data = post_res.json()
    assert bookmark_data["resource_id"] == res1_id
    assert bookmark_data["user_id"] == test_session_data.user_id
    assert "id" in bookmark_data

    # Verify resource detail reflects is_bookmarked = True
    detail_res = client.get(f"/api/v1/kb/resources/{res1_id}", headers=headers)
    assert detail_res.status_code == status.HTTP_200_OK
    assert detail_res.json()["is_bookmarked"] is True

    # Delete bookmark (returns 204 No Content with empty body)
    del_res = client.delete(f"/api/v1/kb/bookmarks/{res1_id}", headers=headers)
    assert del_res.status_code == status.HTTP_204_NO_CONTENT
    assert del_res.content == b""

    # Verify resource detail reflects is_bookmarked = False
    detail_res_after = client.get(f"/api/v1/kb/resources/{res1_id}", headers=headers)
    assert detail_res_after.status_code == status.HTTP_200_OK
    assert detail_res_after.json()["is_bookmarked"] is False


def test_track_kb_user_progress(client, test_session_data, seed_kb_resources):
    """Verify POST /api/v1/kb/progress endpoint tracks user progress."""
    headers = {"Authorization": f"Bearer {test_session_data.id}"}
    res1_id = seed_kb_resources["res1"].id

    post_res = client.post(
        "/api/v1/kb/progress",
        headers=headers,
        json={
            "resource_id": res1_id,
            "step_number": None,
            "is_completed": True,
        },
    )
    assert post_res.status_code == status.HTTP_200_OK
    prog_data = post_res.json()
    assert prog_data["resource_id"] == res1_id
    assert prog_data["user_id"] == test_session_data.user_id
    assert prog_data["is_completed"] is True
    assert prog_data["completion_percentage"] == 100.0


def test_bookmark_and_progress_unauthenticated(client, seed_kb_resources):
    """Verify bookmark and progress endpoints return 401 when unauthenticated."""
    res1_id = seed_kb_resources["res1"].id

    # Create bookmark unauthenticated
    res1 = client.post("/api/v1/kb/bookmarks", json={"resource_id": res1_id})
    assert res1.status_code == status.HTTP_401_UNAUTHORIZED

    # Delete bookmark unauthenticated
    res2 = client.delete(f"/api/v1/kb/bookmarks/{res1_id}")
    assert res2.status_code == status.HTTP_401_UNAUTHORIZED

    # Progress unauthenticated
    res3 = client.post(
        "/api/v1/kb/progress",
        json={"resource_id": res1_id, "is_completed": True},
    )
    assert res3.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_kb_resource_detail_tutorial(client, test_session_data, seed_kb_resources):
    """Verify tutorial detail includes ordered tutorial_steps."""
    headers = {"Authorization": f"Bearer {test_session_data.id}"}
    tut_id = seed_kb_resources["res_tutorial"].id

    response = client.get(f"/api/v1/kb/resources/{tut_id}", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["content_type"] == "TUTORIAL"
    steps = data["tutorial_steps"]
    assert len(steps) == 2
    assert steps[0]["step_number"] == 1
    assert steps[0]["title"] == "Step 1: Account Creation"
    assert steps[1]["step_number"] == 2
    assert steps[1]["title"] == "Step 2: Team Invitation"


def test_track_kb_user_progress_tutorial_steps_flow(client, test_session_data, seed_kb_resources):
    """Verify step-by-step tutorial progress tracking and overall completion calculation."""
    headers = {"Authorization": f"Bearer {test_session_data.id}"}
    tut_id = seed_kb_resources["res_tutorial"].id

    # Complete Step 1
    res1 = client.post(
        "/api/v1/kb/progress",
        headers=headers,
        json={"resource_id": tut_id, "step_number": 1, "is_completed": True},
    )
    assert res1.status_code == status.HTTP_200_OK
    data1 = res1.json()
    assert data1["completed_step_numbers"] == [1]
    assert data1["total_steps"] == 2
    assert data1["completion_percentage"] == 50.0
    assert data1["is_completed"] is False

    # Complete Step 2 -> triggers overall completion
    res2 = client.post(
        "/api/v1/kb/progress",
        headers=headers,
        json={"resource_id": tut_id, "step_number": 2, "is_completed": True},
    )
    assert res2.status_code == status.HTTP_200_OK
    data2 = res2.json()
    assert data2["completed_step_numbers"] == [1, 2]
    assert data2["total_steps"] == 2
    assert data2["completion_percentage"] == 100.0
    assert data2["is_completed"] is True


def test_bookmark_error_cases(client, test_session_data, seed_kb_resources):
    """Verify bookmarking forbidden, non-existent, and duplicate resources."""
    headers = {"Authorization": f"Bearer {test_session_data.id}"}
    res2_id = seed_kb_resources["res2"].id  # Requires Manager/Pro

    # Forbidden resource bookmark
    forbidden_res = client.post(
        "/api/v1/kb/bookmarks",
        headers=headers,
        json={"resource_id": res2_id},
    )
    assert forbidden_res.status_code == status.HTTP_403_FORBIDDEN

    # Non-existent resource bookmark
    non_existent_id = "00000000-0000-0000-0000-000000000000"
    not_found_res = client.post(
        "/api/v1/kb/bookmarks",
        headers=headers,
        json={"resource_id": non_existent_id},
    )
    assert not_found_res.status_code == status.HTTP_404_NOT_FOUND

    # Duplicate bookmarking (idempotent)
    res1_id = seed_kb_resources["res1"].id
    bm1 = client.post("/api/v1/kb/bookmarks", headers=headers, json={"resource_id": res1_id})
    assert bm1.status_code == status.HTTP_201_CREATED
    bm2 = client.post("/api/v1/kb/bookmarks", headers=headers, json={"resource_id": res1_id})
    assert bm2.status_code == status.HTTP_201_CREATED
    assert bm1.json()["id"] == bm2.json()["id"]


def test_progress_error_cases(client, test_session_data, seed_kb_resources):
    """Verify progress tracking forbidden and non-existent error cases."""
    headers = {"Authorization": f"Bearer {test_session_data.id}"}
    res2_id = seed_kb_resources["res2"].id  # Requires Manager/Pro

    # Forbidden resource progress
    forbidden_res = client.post(
        "/api/v1/kb/progress",
        headers=headers,
        json={"resource_id": res2_id, "is_completed": True},
    )
    assert forbidden_res.status_code == status.HTTP_403_FORBIDDEN

    # Non-existent resource progress
    non_existent_id = "00000000-0000-0000-0000-000000000000"
    not_found_res = client.post(
        "/api/v1/kb/progress",
        headers=headers,
        json={"resource_id": non_existent_id, "is_completed": True},
    )
    assert not_found_res.status_code == status.HTTP_404_NOT_FOUND


