"""Unit tests for Knowledge Base ORM entity models."""

import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.knowledge_base import (
    KBCategory,
    KBContentType,
    KBResource,
    KBTutorialStep,
    KBBookmark,
    KBUserProgress,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_kb_category_creation_and_hierarchy(test_session: Session):
    """Verify KBCategory creation, UUID default, parent-child relationship, and query."""
    parent = KBCategory(
        title="Onboarding",
        slug="onboarding",
        description="Onboarding documents and guides",
        display_order=1,
    )
    test_session.add(parent)
    test_session.commit()

    child = KBCategory(
        title="Getting Started",
        slug="getting-started",
        description="First steps for new users",
        parent_id=parent.id,
        display_order=1,
    )
    test_session.add(child)
    test_session.commit()

    retrieved_parent = test_session.query(KBCategory).filter_by(slug="onboarding").first()
    assert retrieved_parent is not None
    assert retrieved_parent.id is not None
    assert str(uuid.UUID(retrieved_parent.id)) == retrieved_parent.id
    assert len(retrieved_parent.children) == 1
    assert retrieved_parent.children[0].slug == "getting-started"

    retrieved_child = test_session.query(KBCategory).filter_by(slug="getting-started").first()
    assert retrieved_child is not None
    assert retrieved_child.parent is not None
    assert retrieved_child.parent.id == parent.id


def test_kb_category_unique_slug_constraint(test_session: Session):
    """Verify duplicate category slugs trigger an IntegrityError."""
    cat1 = KBCategory(title="Category 1", slug="common-slug")
    cat2 = KBCategory(title="Category 2", slug="common-slug")
    test_session.add(cat1)
    test_session.commit()

    test_session.add(cat2)
    with pytest.raises(IntegrityError):
        test_session.commit()
    test_session.rollback()


def test_kb_resource_creation_and_relationships(test_session: Session):
    """Verify KBResource creation, JSON tags, content type enum, and relationship to KBCategory."""
    category = KBCategory(title="Playbooks", slug="playbooks")
    test_session.add(category)
    test_session.commit()

    resource = KBResource(
        category_id=category.id,
        title="Sales Playbook",
        slug="sales-playbook",
        content_type=KBContentType.PLAYBOOK,
        body_text="Sales processes and best practices",
        media_url="https://example.com/media/sales-playbook",
        tags=["sales", "onboarding", "playbook"],
        onboarding_phase="Phase 1",
        required_role="Sales Rep",
        min_subscription_tier="Pro",
    )
    test_session.add(resource)
    test_session.commit()

    retrieved = test_session.query(KBResource).filter_by(slug="sales-playbook").first()
    assert retrieved is not None
    assert retrieved.id is not None
    assert retrieved.category_id == category.id
    assert retrieved.content_type == KBContentType.PLAYBOOK
    assert retrieved.tags == ["sales", "onboarding", "playbook"]
    assert retrieved.category.slug == "playbooks"
    assert len(category.resources) == 1


def test_kb_tutorial_step_creation(test_session: Session):
    """Verify KBTutorialStep creation and relationship to KBResource."""
    category = KBCategory(title="Tutorials", slug="tutorials")
    test_session.add(category)
    test_session.commit()

    resource = KBResource(
        category_id=category.id,
        title="Portal Setup Tutorial",
        slug="portal-setup-tutorial",
        content_type=KBContentType.TUTORIAL,
    )
    test_session.add(resource)
    test_session.commit()

    step1 = KBTutorialStep(
        resource_id=resource.id,
        step_number=1,
        title="Step 1: Account Creation",
        content="Fill out account registration form.",
        video_url="https://example.com/videos/step1.mp4",
    )
    step2 = KBTutorialStep(
        resource_id=resource.id,
        step_number=2,
        title="Step 2: Profile Setup",
        content="Update your profile details.",
    )
    test_session.add_all([step1, step2])
    test_session.commit()

    retrieved_resource = test_session.query(KBResource).filter_by(slug="portal-setup-tutorial").first()
    assert retrieved_resource is not None
    assert len(retrieved_resource.tutorial_steps) == 2
    assert retrieved_resource.tutorial_steps[0].step_number == 1
    assert retrieved_resource.tutorial_steps[1].step_number == 2


def test_kb_bookmark_unique_constraint(test_session: Session):
    """Verify KBBookmark creation and unique constraint on (user_id, resource_id)."""
    category = KBCategory(title="FAQs", slug="faqs")
    test_session.add(category)
    test_session.commit()

    resource = KBResource(
        category_id=category.id,
        title="Billing FAQ",
        slug="billing-faq",
        content_type=KBContentType.FAQ,
    )
    test_session.add(resource)
    test_session.commit()

    bm1 = KBBookmark(user_id="user_1001", resource_id=resource.id)
    test_session.add(bm1)
    test_session.commit()

    bm_duplicate = KBBookmark(user_id="user_1001", resource_id=resource.id)
    test_session.add(bm_duplicate)
    with pytest.raises(IntegrityError):
        test_session.commit()
    test_session.rollback()


def test_kb_user_progress_unique_constraint(test_session: Session):
    """Verify KBUserProgress creation and unique constraint on (user_id, resource_id, step_number)."""
    category = KBCategory(title="Videos", slug="videos")
    test_session.add(category)
    test_session.commit()

    resource = KBResource(
        category_id=category.id,
        title="Intro Video",
        slug="intro-video",
        content_type=KBContentType.VIDEO,
    )
    test_session.add(resource)
    test_session.commit()

    prog1 = KBUserProgress(
        user_id="user_2002",
        resource_id=resource.id,
        step_number=1,
        is_completed=True,
    )
    test_session.add(prog1)
    test_session.commit()

    prog_duplicate = KBUserProgress(
        user_id="user_2002",
        resource_id=resource.id,
        step_number=1,
        is_completed=False,
    )
    test_session.add(prog_duplicate)
    with pytest.raises(IntegrityError):
        test_session.commit()
    test_session.rollback()
