"""Unit tests for user notification preference matrix management and E.164 phone validation."""

import uuid
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.notification import (
    NotificationCategory,
    NotificationPreference,
)
from src.services.notifications.preference import (
    PreferenceValidationError,
    get_user_notification_preferences,
    get_user_preference_by_category,
    is_channel_enabled_for_user,
    is_valid_e164_phone,
    parse_notification_category,
    update_user_notification_preference,
    update_user_notification_preference_matrix,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_is_valid_e164_phone():
    """Verify E.164 phone format validation function against valid and invalid cases."""
    # Valid E.164 phone numbers
    assert is_valid_e164_phone("+14155552671") is True
    assert is_valid_e164_phone("+442071838750") is True
    assert is_valid_e164_phone("+33123456789") is True

    # Invalid phone numbers
    assert is_valid_e164_phone("14155552671") is False  # Missing +
    assert is_valid_e164_phone("+012345678") is False  # Country code starting with 0
    assert is_valid_e164_phone("+1 415 555 2671") is False  # Contains spaces
    assert is_valid_e164_phone("+1-415-555-2671") is False  # Contains hyphens
    assert is_valid_e164_phone("+1234567890123456") is False  # More than 15 digits
    assert is_valid_e164_phone("+") is False  # Only plus
    assert is_valid_e164_phone("") is False
    assert is_valid_e164_phone(None) is False


def test_parse_notification_category():
    """Verify category parsing from string or enum inputs."""
    assert parse_notification_category(NotificationCategory.FINANCIAL) == NotificationCategory.FINANCIAL
    assert parse_notification_category("Financial") == NotificationCategory.FINANCIAL
    assert parse_notification_category("financial") == NotificationCategory.FINANCIAL
    assert parse_notification_category("MILESTONE") == NotificationCategory.MILESTONE
    assert parse_notification_category("SLA") == NotificationCategory.SLA

    with pytest.raises(PreferenceValidationError):
        parse_notification_category("UNKNOWN")

    with pytest.raises(PreferenceValidationError):
        parse_notification_category("")


def test_get_user_notification_preferences_initialization(test_session: Session):
    """Verify get_user_notification_preferences auto-creates default preference matrix if missing."""
    user_id = str(uuid.uuid4())

    prefs = get_user_notification_preferences(test_session, user_id)
    assert len(prefs) == 3

    categories = {p.category for p in prefs}
    assert categories == {
        NotificationCategory.FINANCIAL,
        NotificationCategory.MILESTONE,
        NotificationCategory.SLA,
    }

    for p in prefs:
        assert p.channel_email is True
        assert p.channel_sms is False
        assert p.channel_in_app is True
        assert p.phone_number is None


def test_get_user_notification_preferences_empty_user_id(test_session: Session):
    """Verify error raised when user_id is empty."""
    with pytest.raises(PreferenceValidationError):
        get_user_notification_preferences(test_session, "")


def test_update_user_notification_preference_toggles(test_session: Session):
    """Verify toggling channel settings updates user preferences immediately."""
    user_id = str(uuid.uuid4())

    updated = update_user_notification_preference(
        db=test_session,
        user_id=user_id,
        category="Financial",
        channel_email=False,
        channel_in_app=True,
    )
    assert updated.channel_email is False
    assert updated.channel_in_app is True
    assert updated.channel_sms is False

    # Verify database persistence
    fetched = get_user_preference_by_category(test_session, user_id, "Financial")
    assert fetched.channel_email is False


def test_update_user_notification_preference_sms_e164_validation(test_session: Session):
    """Verify SMS channel enablement mandates valid E.164 phone numbers."""
    user_id = str(uuid.uuid4())

    # Enabling SMS without phone_number should raise error
    with pytest.raises(PreferenceValidationError, match="required when SMS channel is enabled"):
        update_user_notification_preference(
            db=test_session,
            user_id=user_id,
            category="Milestone",
            channel_sms=True,
        )

    # Enabling SMS with invalid E.164 phone string should raise error
    with pytest.raises(PreferenceValidationError, match="Invalid E.164 phone number format"):
        update_user_notification_preference(
            db=test_session,
            user_id=user_id,
            category="Milestone",
            channel_sms=True,
            phone_number="1234567890",
        )

    # Enabling SMS with valid E.164 phone should succeed
    updated = update_user_notification_preference(
        db=test_session,
        user_id=user_id,
        category="Milestone",
        channel_sms=True,
        phone_number="+14155552671",
    )
    assert updated.channel_sms is True
    assert updated.phone_number == "+14155552671"


def test_update_user_notification_preference_matrix(test_session: Session):
    """Verify matrix update function processes multiple category preferences at once."""
    user_id = str(uuid.uuid4())

    matrix_data = [
        {
            "category": "Financial",
            "channel_email": False,
            "channel_sms": False,
            "channel_in_app": True,
        },
        {
            "category": "SLA",
            "channel_email": True,
            "channel_sms": True,
            "channel_in_app": True,
            "phone_number": "+442071838750",
        },
    ]

    result = update_user_notification_preference_matrix(
        db=test_session,
        user_id=user_id,
        preferences_data=matrix_data,
    )

    assert len(result) == 3
    fin_pref = next(p for p in result if p.category == NotificationCategory.FINANCIAL)
    sla_pref = next(p for p in result if p.category == NotificationCategory.SLA)

    assert fin_pref.channel_email is False
    assert sla_pref.channel_sms is True
    assert sla_pref.phone_number == "+442071838750"


def test_is_channel_enabled_for_user(test_session: Session):
    """Verify is_channel_enabled_for_user correctly evaluates active channels."""
    user_id = str(uuid.uuid4())

    # Default settings: email=True, sms=False, in_app=True
    assert is_channel_enabled_for_user(test_session, user_id, "Financial", "email") is True
    assert is_channel_enabled_for_user(test_session, user_id, "Financial", "sms") is False
    assert is_channel_enabled_for_user(test_session, user_id, "Financial", "in_app") is True

    # Enable SMS with valid phone
    update_user_notification_preference(
        db=test_session,
        user_id=user_id,
        category="Financial",
        channel_sms=True,
        phone_number="+14155552671",
    )
    assert is_channel_enabled_for_user(test_session, user_id, "Financial", "sms") is True

    # Test unknown channel
    with pytest.raises(PreferenceValidationError):
        is_channel_enabled_for_user(test_session, user_id, "Financial", "pigeon_post")
