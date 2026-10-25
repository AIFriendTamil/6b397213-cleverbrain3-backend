"""Outbound SMS gateway delivery adapter supporting mock and HTTP external providers."""

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from src.lib.config import settings

logger = logging.getLogger(__name__)

# E.164 compliant phone number validation pattern (+ followed by 1 to 15 digits)
E164_PHONE_REGEX = re.compile(r"^\+[1-9]\d{1,14}$")


class SMSAdapterError(Exception):
    """Base exception class for SMS delivery gateway operations."""

    pass


class SMSValidationError(SMSAdapterError):
    """Raised when SMS recipient phone number or message parameters fail validation."""

    pass


class SMSGatewayUnavailableError(SMSAdapterError):
    """Raised when external SMS gateway HTTP API is unreachable or times out."""

    pass


class SMSGatewayDeliveryError(SMSAdapterError):
    """Raised when SMS gateway returns a delivery failure status code."""

    pass


class SMSAdapter:
    """Outbound SMS gateway adapter for delivering text notification messages."""

    def __init__(
        self,
        provider: Optional[str] = None,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        sender_number: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        """Initialize SMS gateway adapter with target configuration.

        Args:
            provider: Provider operational mode ('mock', 'http', 'external', 'disabled').
            api_url: Target REST endpoint URL for external SMS gateway API.
            api_key: Optional secret authorization key or bearer token.
            sender_number: Default sender phone number in E.164 format.
            timeout_seconds: HTTP request timeout duration in seconds.
            http_client: Optional pre-configured httpx.Client instance for testing
                or connection pooling.
        """
        cfg = settings.sms_gateway_config
        self.provider = (provider or cfg.provider).lower().strip()
        self.api_url = (api_url or cfg.api_url).rstrip("/")
        self.api_key = api_key or cfg.api_key
        self.sender_number = sender_number or cfg.sender_number
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

    def validate_phone_number(self, phone_number: str) -> str:
        """Validate recipient phone number against international E.164 standards.

        Args:
            phone_number: Candidate phone number string.

        Returns:
            Cleaned and normalized E.164 phone number string.

        Raises:
            SMSValidationError: If phone number is empty or improperly formatted.
        """
        if not phone_number or not isinstance(phone_number, str) or not phone_number.strip():
            raise SMSValidationError("Recipient phone number cannot be empty")

        clean_phone = phone_number.strip()
        if not E164_PHONE_REGEX.match(clean_phone):
            raise SMSValidationError(
                f"Invalid E.164 phone number format: '{clean_phone}'. "
                "Must start with '+' followed by country code and digits (e.g. +15550199)."
            )

        return clean_phone

    def send_sms(
        self,
        recipient_phone: str,
        message: str,
        sender_number: Optional[str] = None,
    ) -> bool:
        """Transmit an SMS text message to the target recipient phone number.

        Args:
            recipient_phone: Target recipient phone number in E.164 format.
            message: Text message payload body content string.
            sender_number: Optional sender phone number override.

        Returns:
            bool: True if SMS delivery succeeded, False if disabled or failed.

        Raises:
            SMSValidationError: If recipient or payload parameters fail validation.
            SMSGatewayUnavailableError: If external HTTP gateway is unreachable.
            SMSGatewayDeliveryError: If external HTTP gateway returns a failure error.
        """
        clean_recipient = self.validate_phone_number(recipient_phone)

        if not message or not isinstance(message, str) or not message.strip():
            raise SMSValidationError("SMS message payload cannot be empty")

        clean_message = message.strip()
        raw_sender = sender_number or self.sender_number
        clean_sender = raw_sender.strip() if raw_sender and raw_sender.strip() else None

        if self.provider == "disabled":
            logger.info("SMS gateway provider is disabled; skipping delivery")
            return False

        if self.provider == "mock":
            message_record = {
                "recipient": clean_recipient,
                "message": clean_message,
                "sender": clean_sender,
                "status": "delivered",
                "provider": "mock",
            }
            self._sent_messages.append(message_record)
            logger.info(
                "Simulated SMS delivery via mock gateway",
                extra={
                    "recipient_suffix": clean_recipient[-4:] if len(clean_recipient) >= 4 else "****",
                    "message_length": len(clean_message),
                },
            )
            return True

        # Handle 'http' or 'external' provider modes
        payload = {
            "recipient": clean_recipient,
            "message": clean_message,
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
                raise SMSGatewayUnavailableError(
                    f"SMS gateway returned HTTP status {response.status_code}"
                )

            if response.status_code >= 400:
                raise SMSGatewayDeliveryError(
                    f"SMS gateway HTTP request failed with status "
                    f"{response.status_code}: {response.text}"
                )

            logger.info(
                "SMS successfully dispatched via HTTP gateway",
                extra={
                    "recipient_suffix": clean_recipient[-4:] if len(clean_recipient) >= 4 else "****",
                    "status_code": response.status_code,
                },
            )
            return True

        except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as err:
            raise SMSGatewayUnavailableError(
                f"SMS gateway connection error: {str(err)}"
            ) from err
        except (SMSGatewayUnavailableError, SMSGatewayDeliveryError):
            raise
        except Exception as err:
            raise SMSGatewayDeliveryError(
                f"Unexpected SMS gateway error: {str(err)}"
            ) from err

    def __call__(
        self,
        recipient_phone: str,
        message: str,
    ) -> bool:
        """Enable callable instance usage for direct port adapter invocation.

        Args:
            recipient_phone: Target recipient phone number string in E.164 format.
            message: SMS text message body content string.

        Returns:
            bool: True if SMS delivery succeeded, False otherwise.
        """
        return self.send_sms(
            recipient_phone=recipient_phone,
            message=message,
        )


# Alias for class name consistency
SMSGatewayAdapter = SMSAdapter
