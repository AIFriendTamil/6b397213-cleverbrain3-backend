"""Outbound email gateway delivery adapter supporting mock and HTTP external providers."""

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from src.lib.config import settings

logger = logging.getLogger(__name__)

# RFC 5322 compliant basic email validation pattern
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class EmailAdapterError(Exception):
    """Base exception class for email delivery gateway operations."""

    pass


class EmailValidationError(EmailAdapterError):
    """Raised when email recipient address or payload parameters fail validation."""

    pass


class EmailGatewayUnavailableError(EmailAdapterError):
    """Raised when external email gateway HTTP API is unreachable or times out."""

    pass


class EmailGatewayDeliveryError(EmailAdapterError):
    """Raised when email gateway returns a delivery failure status code."""

    pass


class EmailAdapter:
    """Outbound email gateway adapter for delivering notification messages."""

    def __init__(
        self,
        provider: Optional[str] = None,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        sender_address: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        """Initialize email gateway adapter with target configuration.

        Args:
            provider: Provider operational mode ('mock', 'http', 'external', 'disabled').
            api_url: Target REST endpoint URL for external email gateway API.
            api_key: Optional secret authorization key or bearer token.
            sender_address: Default sender email address.
            timeout_seconds: HTTP request timeout duration in seconds.
            http_client: Optional pre-configured httpx.Client instance for testing
                or connection pooling.
        """
        cfg = settings.email_gateway_config
        self.provider = (provider or cfg.provider).lower().strip()
        self.api_url = (api_url or cfg.api_url).rstrip("/")
        self.api_key = api_key or cfg.api_key
        self.sender_address = sender_address or cfg.sender_address
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else float(cfg.timeout_seconds)
        )
        self._custom_client = http_client
        self._sent_messages: List[Dict[str, Any]] = []

    @property
    def sent_messages(self) -> List[Dict[str, Any]]:
        """Retrieve copy of in-memory sent message history (mock mode)."""
        return list(self._sent_messages)

    def clear_sent_messages(self) -> None:
        """Clear in-memory sent message history (mock mode)."""
        self._sent_messages.clear()

    def validate_email_address(self, email: str) -> str:
        """Validate recipient email address format.

        Args:
            email: Candidate email address string.

        Returns:
            Cleaned and normalized email address string.

        Raises:
            EmailValidationError: If email address is empty or improperly formatted.
        """
        if not email or not isinstance(email, str) or not email.strip():
            raise EmailValidationError("Recipient email address cannot be empty")

        clean_email = email.strip()
        if not EMAIL_REGEX.match(clean_email):
            raise EmailValidationError(f"Invalid email address format: '{clean_email}'")

        return clean_email

    def send_email(
        self,
        recipient_email: str,
        subject: str,
        body: str,
        sender_address: Optional[str] = None,
    ) -> bool:
        """Transmit an email notification message to the target recipient.

        Args:
            recipient_email: Target recipient email address string.
            subject: Email message subject line string.
            body: Email message body content string.
            sender_address: Optional sender address override.

        Returns:
            bool: True if email delivery succeeded, False if disabled or failed.

        Raises:
            EmailValidationError: If recipient or payload parameters fail validation.
            EmailGatewayUnavailableError: If external HTTP gateway is unreachable.
            EmailGatewayDeliveryError: If external HTTP gateway returns a failure error.
        """
        clean_recipient = self.validate_email_address(recipient_email)

        if not subject or not isinstance(subject, str) or not subject.strip():
            raise EmailValidationError("Email subject line cannot be empty")

        if not body or not isinstance(body, str) or not body.strip():
            raise EmailValidationError("Email message body cannot be empty")

        clean_subject = subject.strip()
        clean_body = body.strip()
        clean_sender = (sender_address or self.sender_address).strip()

        if self.provider == "disabled":
            logger.info("Email gateway provider is disabled; skipping delivery")
            return False

        if self.provider == "mock":
            message_record = {
                "recipient": clean_recipient,
                "subject": clean_subject,
                "body": clean_body,
                "sender": clean_sender,
                "status": "delivered",
                "provider": "mock",
            }
            self._sent_messages.append(message_record)
            logger.info(
                "Simulated email delivery via mock gateway",
                extra={
                    "recipient_domain": clean_recipient.split("@")[-1],
                    "subject_length": len(clean_subject),
                    "body_length": len(clean_body),
                },
            )
            return True

        # Handle 'http' or 'external' provider modes
        payload = {
            "recipient": clean_recipient,
            "subject": clean_subject,
            "body": clean_body,
            "sender": clean_sender,
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-API-Key"] = self.api_key

        try:
            if self._custom_client is not None:
                response = self._custom_client.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        self.api_url,
                        json=payload,
                        headers=headers,
                    )

            if response.status_code in (502, 503, 504, 408):
                raise EmailGatewayUnavailableError(
                    f"Email gateway returned HTTP status {response.status_code}"
                )

            if response.status_code >= 400:
                raise EmailGatewayDeliveryError(
                    f"Email gateway HTTP request failed with status "
                    f"{response.status_code}: {response.text}"
                )

            logger.info(
                "Email successfully dispatched via HTTP gateway",
                extra={
                    "recipient_domain": clean_recipient.split("@")[-1],
                    "status_code": response.status_code,
                },
            )
            return True

        except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as err:
            raise EmailGatewayUnavailableError(
                f"Email gateway connection error: {str(err)}"
            ) from err
        except (EmailGatewayUnavailableError, EmailGatewayDeliveryError):
            raise
        except Exception as err:
            raise EmailGatewayDeliveryError(
                f"Unexpected email gateway error: {str(err)}"
            ) from err

    def __call__(
        self,
        recipient_email: str,
        subject: str,
        body: str,
    ) -> bool:
        """Enable callable instance usage for direct port adapter invocation.

        Args:
            recipient_email: Target recipient email address string.
            subject: Email subject header string.
            body: Email body text string.

        Returns:
            bool: True if email delivery succeeded, False otherwise.
        """
        return self.send_email(
            recipient_email=recipient_email,
            subject=subject,
            body=body,
        )


# Alias for class name consistency
EmailGatewayAdapter = EmailAdapter
