"""Unit tests for EmailAdapter / EmailGatewayAdapter connector."""

import uuid

import httpx
import pytest
from sqlalchemy.orm import Session

from src.connectors.email_adapter import (
    EmailAdapter,
    EmailGatewayAdapter,
    EmailGatewayDeliveryError,
    EmailGatewayUnavailableError,
    EmailValidationError,
)
from src.models.base import Base, create_db_engine
from src.services.notifications.dispatcher import (
    DispatchStatus,
    dispatch_notification,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_email_adapter_mock_mode_success():
    """Verify email delivery simulation in mock provider mode."""
    adapter = EmailAdapter(provider="mock", sender_address="noreply@example.com")
    assert EmailGatewayAdapter is EmailAdapter

    result = adapter.send_email(
        recipient_email="user@example.com",
        subject="Invoice Notification",
        body="Your invoice #1002 is now available.",
    )

    assert result is True
    assert len(adapter.sent_messages) == 1
    sent = adapter.sent_messages[0]
    assert sent["recipient"] == "user@example.com"
    assert sent["subject"] == "Invoice Notification"
    assert sent["body"] == "Your invoice #1002 is now available."
    assert sent["sender"] == "noreply@example.com"
    assert sent["status"] == "delivered"

    adapter.clear_sent_messages()
    assert len(adapter.sent_messages) == 0


def test_email_adapter_callable_interface():
    """Verify EmailAdapter instance can be invoked directly as a callable."""
    adapter = EmailAdapter(provider="mock")

    result = adapter(
        recipient_email="client@domain.com",
        subject="Quote Approval Request",
        body="Please review quote Q-2026-01.",
    )

    assert result is True
    assert len(adapter.sent_messages) == 1
    assert adapter.sent_messages[0]["recipient"] == "client@domain.com"


def test_email_adapter_validation_failures():
    """Verify validation exceptions raised for invalid recipient or message parameters."""
    adapter = EmailAdapter(provider="mock")

    # Empty email
    with pytest.raises(EmailValidationError):
        adapter.send_email("", "Subject", "Body")

    # Malformed email
    with pytest.raises(EmailValidationError):
        adapter.send_email("not-an-email", "Subject", "Body")

    # Empty subject
    with pytest.raises(EmailValidationError):
        adapter.send_email("valid@example.com", "", "Body")

    # Empty body
    with pytest.raises(EmailValidationError):
        adapter.send_email("valid@example.com", "Subject", "   ")


def test_email_adapter_disabled_mode():
    """Verify delivery returns False when provider is set to disabled."""
    adapter = EmailAdapter(provider="disabled")

    result = adapter.send_email(
        recipient_email="user@example.com",
        subject="Test Subject",
        body="Test Body",
    )

    assert result is False
    assert len(adapter.sent_messages) == 0


def test_email_adapter_http_mode_success():
    """Verify successful outbound transmission via HTTP gateway API."""

    def mock_transport(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == "http://localhost:8003/api/v1/email"
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.headers["X-API-Key"] == "test-key"
        return httpx.Response(200, json={"status": "queued", "id": "msg-123"})

    client = httpx.Client(transport=httpx.MockTransport(mock_transport))
    adapter = EmailAdapter(
        provider="http",
        api_url="http://localhost:8003/api/v1/email",
        api_key="test-key",
        http_client=client,
    )

    result = adapter.send_email(
        recipient_email="recipient@example.com",
        subject="Payment Received",
        body="Thank you for your payment.",
    )

    assert result is True


def test_email_adapter_http_mode_unavailable_503():
    """Verify EmailGatewayUnavailableError raised on gateway 503 response."""

    def mock_transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    client = httpx.Client(transport=httpx.MockTransport(mock_transport))
    adapter = EmailAdapter(
        provider="external",
        api_url="http://localhost:8003/api/v1/email",
        http_client=client,
    )

    with pytest.raises(EmailGatewayUnavailableError) as exc_info:
        adapter.send_email(
            recipient_email="recipient@example.com",
            subject="Notice",
            body="Important notice body.",
        )

    assert "HTTP status 503" in str(exc_info.value)


def test_email_adapter_http_mode_delivery_failure_400():
    """Verify EmailGatewayDeliveryError raised on gateway 400 Bad Request."""

    def mock_transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Invalid recipient domain")

    client = httpx.Client(transport=httpx.MockTransport(mock_transport))
    adapter = EmailAdapter(
        provider="http",
        api_url="http://localhost:8003/api/v1/email",
        http_client=client,
    )

    with pytest.raises(EmailGatewayDeliveryError) as exc_info:
        adapter.send_email(
            recipient_email="bad@domain.com",
            subject="Notice",
            body="Important notice body.",
        )

    assert "status 400" in str(exc_info.value)


def test_email_adapter_http_mode_network_timeout():
    """Verify EmailGatewayUnavailableError raised on request timeout."""

    def mock_transport(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Connection timed out")

    client = httpx.Client(transport=httpx.MockTransport(mock_transport))
    adapter = EmailAdapter(
        provider="http",
        api_url="http://localhost:8003/api/v1/email",
        http_client=client,
    )

    with pytest.raises(EmailGatewayUnavailableError) as exc_info:
        adapter.send_email(
            recipient_email="recipient@example.com",
            subject="Notice",
            body="Important notice body.",
        )

    assert "connection error" in str(exc_info.value).lower()


def test_integration_with_notification_dispatcher(test_session: Session):
    """Verify EmailAdapter works with notification dispatcher."""
    adapter = EmailAdapter(provider="mock")
    user_id = str(uuid.uuid4())

    summary = dispatch_notification(
        db=test_session,
        user_id=user_id,
        category="Financial",
        title="Quote Approved",
        message="Your commercial quote Q-99 has been approved.",
        email="finance.user@example.com",
        email_sender=adapter,
    )

    assert "email" in summary.results
    assert summary.results["email"].status == DispatchStatus.DISPATCHED
    assert len(adapter.sent_messages) == 1
    assert adapter.sent_messages[0]["recipient"] == "finance.user@example.com"
