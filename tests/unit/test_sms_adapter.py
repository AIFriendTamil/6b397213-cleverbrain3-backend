"""Unit tests for SMSAdapter / SMSGatewayAdapter connector."""

import pytest
import httpx
from sqlalchemy.orm import Session

from src.connectors.sms_adapter import (
    SMSAdapter,
    SMSGatewayAdapter,
    SMSGatewayDeliveryError,
    SMSGatewayUnavailableError,
    SMSValidationError,
)
from src.models.base import Base, create_db_engine
from src.models.notification import NotificationCategory
from src.services.notifications.dispatcher import (
    DispatchStatus,
    dispatch_notification,
)
from src.services.notifications.preference import (
    update_user_notification_preference,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_sms_adapter_mock_mode_success():
    """Verify SMS delivery simulation in mock provider mode."""
    adapter = SMSAdapter(provider="mock", sender_number="+15550199")
    assert SMSGatewayAdapter is SMSAdapter

    result = adapter.send_sms(
        recipient_phone="+15550123",
        message="Your invoice payment is due tomorrow.",
    )

    assert result is True
    assert len(adapter.sent_messages) == 1
    sent = adapter.sent_messages[0]
    assert sent["recipient"] == "+15550123"
    assert sent["message"] == "Your invoice payment is due tomorrow."
    assert sent["sender"] == "+15550199"
    assert sent["status"] == "delivered"

    adapter.clear_sent_messages()
    assert len(adapter.sent_messages) == 0


def test_sms_adapter_callable_interface():
    """Verify SMSAdapter instance can be invoked directly as a callable."""
    adapter = SMSAdapter(provider="mock")

    result = adapter(
        recipient_phone="+15550188",
        message="Quote Q-101 requires your approval.",
    )

    assert result is True
    assert len(adapter.sent_messages) == 1
    assert adapter.sent_messages[0]["recipient"] == "+15550188"


def test_sms_adapter_validation_failures():
    """Verify validation exceptions raised for invalid recipient phone or message parameters."""
    adapter = SMSAdapter(provider="mock")

    # Empty phone
    with pytest.raises(SMSValidationError):
        adapter.send_sms("", "Message text")

    # Invalid E.164 phone format
    with pytest.raises(SMSValidationError):
        adapter.send_sms("15550123", "Message text")  # Missing leading '+'

    with pytest.raises(SMSValidationError):
        adapter.send_sms("+0123456", "Message text")  # Country code starting with 0

    with pytest.raises(SMSValidationError):
        adapter.send_sms("invalid-phone", "Message text")

    # Empty message
    with pytest.raises(SMSValidationError):
        adapter.send_sms("+15550123", "")

    with pytest.raises(SMSValidationError):
        adapter.send_sms("+15550123", "   ")


def test_sms_adapter_disabled_mode():
    """Verify delivery returns False when provider is set to disabled."""
    adapter = SMSAdapter(provider="disabled")

    result = adapter.send_sms(
        recipient_phone="+15550123",
        message="Test alert message",
    )

    assert result is False
    assert len(adapter.sent_messages) == 0


def test_sms_adapter_http_mode_success():
    """Verify HTTP provider mode handles successful 200 response."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://gateway.test/api/v1/sms"
        return httpx.Response(200, json={"status": "sent", "id": "sms_123"})

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    adapter = SMSAdapter(
        provider="http",
        api_url="http://gateway.test/api/v1/sms",
        api_key="secret-sms-key",
        http_client=client,
    )

    result = adapter.send_sms(
        recipient_phone="+15550199",
        message="HTTP gateway test message.",
    )

    assert result is True


def test_sms_adapter_http_mode_gateway_unavailable():
    """Verify HTTP provider mode handles 503 gateway unavailable error."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    adapter = SMSAdapter(
        provider="http",
        api_url="http://gateway.test/api/v1/sms",
        http_client=client,
    )

    with pytest.raises(SMSGatewayUnavailableError):
        adapter.send_sms(
            recipient_phone="+15550199",
            message="Test message",
        )


def test_sms_adapter_http_mode_delivery_error():
    """Verify HTTP provider mode handles 400 client delivery error."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Bad Request: Invalid phone number")

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    adapter = SMSAdapter(
        provider="http",
        api_url="http://gateway.test/api/v1/sms",
        http_client=client,
    )

    with pytest.raises(SMSGatewayDeliveryError):
        adapter.send_sms(
            recipient_phone="+15550199",
            message="Test message",
        )


def test_sms_adapter_http_mode_network_timeout():
    """Verify HTTP provider mode handles network timeout exceptions."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Connection timed out")

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    adapter = SMSAdapter(
        provider="http",
        api_url="http://gateway.test/api/v1/sms",
        http_client=client,
    )

    with pytest.raises(SMSGatewayUnavailableError):
        adapter.send_sms(
            recipient_phone="+15550199",
            message="Test message",
        )


def test_dispatcher_integration_with_sms_adapter(test_session: Session):
    """Verify dispatcher service integration with SMSAdapter instance."""
    user_id = "usr_test_sms_101"
    sms_adapter = SMSAdapter(provider="mock")

    # Enable SMS for Financial category with valid E.164 phone
    update_user_notification_preference(
        db=test_session,
        user_id=user_id,
        category=NotificationCategory.FINANCIAL,
        channel_sms=True,
        phone_number="+15550999",
    )

    summary = dispatch_notification(
        db=test_session,
        user_id=user_id,
        category=NotificationCategory.FINANCIAL,
        title="Payment Reminder",
        message="Invoice #1040 is overdue.",
        sms_sender=sms_adapter,
    )

    assert "sms" in summary.results
    sms_res = summary.results["sms"]
    assert sms_res.status == DispatchStatus.DISPATCHED
    assert len(sms_adapter.sent_messages) == 1
    assert sms_adapter.sent_messages[0]["recipient"] == "+15550999"
    assert sms_adapter.sent_messages[0]["message"] == "Invoice #1040 is overdue."


def test_dispatcher_integration_sms_opted_out(test_session: Session):
    """Verify dispatcher respects SMS channel opt-out preference."""
    user_id = "usr_test_sms_optout"
    sms_adapter = SMSAdapter(provider="mock")

    # Opt out of SMS for Financial category
    update_user_notification_preference(
        db=test_session,
        user_id=user_id,
        category=NotificationCategory.FINANCIAL,
        channel_sms=False,
        phone_number="+15550999",
    )

    summary = dispatch_notification(
        db=test_session,
        user_id=user_id,
        category=NotificationCategory.FINANCIAL,
        title="Payment Reminder",
        message="Invoice #1040 is overdue.",
        sms_sender=sms_adapter,
    )

    assert "sms" in summary.results
    sms_res = summary.results["sms"]
    assert sms_res.status == DispatchStatus.OPTED_OUT
    assert len(sms_adapter.sent_messages) == 0
