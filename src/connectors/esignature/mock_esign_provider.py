"""Vendor-agnostic mock e-signature provider connector satisfying ESignaturePort."""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from src.lib.config import settings
from src.services.clm.ports import (
    ESignaturePort,
    ESignatureRequestNotFoundError,
)

logger = logging.getLogger(__name__)


class MockESignProvider(ESignaturePort):
    """Mock e-signature provider implementation for vendor-agnostic signature workflows."""

    def __init__(self, webhook_secret: Optional[str] = None) -> None:
        self.webhook_secret = webhook_secret or settings.CLM_ESIGN_WEBHOOK_SECRET
        self._requests: Dict[str, Dict[str, Any]] = {}

    def create_signature_request(
        self,
        contract_id: str,
        document_content: bytes,
        filename: str,
        recipient_email: str,
        recipient_name: str,
        subject: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create and dispatch a mock e-signature request.

        Args:
            contract_id: Associated contract unique ID.
            document_content: Raw bytes of the document to sign.
            filename: Original file name.
            recipient_email: Email address of the signer.
            recipient_name: Full name of the signer.
            subject: Optional custom subject for the notification email.

        Returns:
            Dict containing signature request details and generated provider_ref_id.
        """
        provider_ref_id = f"mock_esig_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        request_record = {
            "provider_ref_id": provider_ref_id,
            "contract_id": contract_id,
            "execution_status": "PENDING",
            "recipient_email": recipient_email,
            "recipient_name": recipient_name,
            "filename": filename,
            "document_hash": hashlib.sha256(document_content).hexdigest(),
            "subject": subject or f"Signature Request for Contract {contract_id}",
            "created_at": now,
            "status_updated_at": now,
        }

        self._requests[provider_ref_id] = request_record
        logger.info(
            "Created mock signature request",
            extra={
                "provider_ref_id": provider_ref_id,
                "contract_id": contract_id,
                "recipient_email": recipient_email,
            },
        )

        return request_record.copy()

    def get_signature_status(self, provider_ref_id: str) -> Dict[str, Any]:
        """Retrieve current status of a signature request by provider reference ID.

        Args:
            provider_ref_id: Mock provider reference ID.

        Returns:
            Dict containing signature request status metadata.

        Raises:
            ESignatureRequestNotFoundError: If provider_ref_id is unknown.
        """
        if provider_ref_id not in self._requests:
            raise ESignatureRequestNotFoundError(
                f"E-signature request with provider_ref_id '{provider_ref_id}' not found."
            )
        return self._requests[provider_ref_id].copy()

    def cancel_signature_request(self, provider_ref_id: str) -> Dict[str, Any]:
        """Cancel or void an active signature request.

        Args:
            provider_ref_id: Mock provider reference ID.

        Returns:
            Dict containing updated status metadata with execution_status='VOIDED'.

        Raises:
            ESignatureRequestNotFoundError: If provider_ref_id is unknown.
        """
        if provider_ref_id not in self._requests:
            raise ESignatureRequestNotFoundError(
                f"E-signature request with provider_ref_id '{provider_ref_id}' not found."
            )

        now = datetime.now(timezone.utc).isoformat()
        record = self._requests[provider_ref_id]
        record["execution_status"] = "VOIDED"
        record["status_updated_at"] = now

        logger.info(
            "Canceled mock signature request",
            extra={
                "provider_ref_id": provider_ref_id,
                "execution_status": "VOIDED",
            },
        )

        return record.copy()

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature_header: str,
        secret: Optional[str] = None,
    ) -> bool:
        """Verify HMAC-SHA256 signature of incoming webhook payload.

        Args:
            payload: Raw bytes of the webhook body.
            signature_header: Header string containing signature.
            secret: Optional secret key override.

        Returns:
            True if signature is valid, False otherwise.
        """
        if not signature_header:
            return False

        secret_key = (secret or self.webhook_secret).encode("utf-8")
        if isinstance(payload, str):
            payload_bytes = payload.encode("utf-8")
        else:
            payload_bytes = bytes(payload)

        clean_sig = signature_header.strip()
        if "=" in clean_sig:
            clean_sig = clean_sig.split("=", 1)[1].strip()

        expected_sig = hmac.new(secret_key, payload_bytes, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected_sig.lower(), clean_sig.lower())

    def parse_webhook_event(
        self,
        payload: Any,
    ) -> Dict[str, Any]:
        """Parse raw webhook payload into standardized structure.

        Args:
            payload: Raw bytes, JSON string, or dict payload.

        Returns:
            Dict containing normalized fields:
                - provider_ref_id
                - execution_status
                - event_timestamp
                - recipient_email
                - event_type
                - raw_payload
        """
        if isinstance(payload, (bytes, bytearray)):
            try:
                payload_dict = json.loads(payload.decode("utf-8"))
            except json.JSONDecodeError:
                payload_dict = {}
        elif isinstance(payload, str):
            try:
                payload_dict = json.loads(payload)
            except json.JSONDecodeError:
                payload_dict = {}
        elif isinstance(payload, dict):
            payload_dict = payload
        else:
            payload_dict = {}

        provider_ref_id = (
            payload_dict.get("provider_ref_id")
            or payload_dict.get("signature_request_id")
            or payload_dict.get("data", {}).get("provider_ref_id")
        )

        raw_status = str(
            payload_dict.get("execution_status")
            or payload_dict.get("status")
            or payload_dict.get("event_type")
            or payload_dict.get("event")
            or ""
        ).upper()

        if "SIGN" in raw_status or "COMPLETE" in raw_status:
            normalized_status = "SIGNED"
        elif "DECLIN" in raw_status or "REJECT" in raw_status:
            normalized_status = "DECLINED"
        elif "VOID" in raw_status or "CANCEL" in raw_status:
            normalized_status = "VOIDED"
        else:
            normalized_status = "PENDING"

        event_timestamp = (
            payload_dict.get("event_timestamp")
            or payload_dict.get("timestamp")
            or payload_dict.get("status_updated_at")
            or datetime.now(timezone.utc).isoformat()
        )

        recipient_email = payload_dict.get("recipient_email") or payload_dict.get(
            "data", {}
        ).get("recipient_email")
        event_type = (
            payload_dict.get("event_type")
            or payload_dict.get("event")
            or f"signature.{normalized_status.lower()}"
        )

        return {
            "provider_ref_id": provider_ref_id,
            "execution_status": normalized_status,
            "event_timestamp": event_timestamp,
            "recipient_email": recipient_email,
            "event_type": event_type,
            "raw_payload": payload_dict,
        }

    def simulate_signer_action(
        self,
        provider_ref_id: str,
        action: str,
        event_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Simulate a signer performing an action (e.g. SIGNED, DECLINED, VOIDED).

        Args:
            provider_ref_id: Provider reference ID.
            action: Action/status state ('SIGNED', 'DECLINED', 'VOIDED').
            event_time: Optional explicit timestamp of action.

        Returns:
            Dict containing updated signature request metadata.

        Raises:
            ESignatureRequestNotFoundError: If provider_ref_id is unknown.
        """
        if provider_ref_id not in self._requests:
            raise ESignatureRequestNotFoundError(
                f"E-signature request with provider_ref_id '{provider_ref_id}' not found."
            )

        action_upper = action.upper()
        if action_upper not in ("PENDING", "SIGNED", "DECLINED", "VOIDED"):
            action_upper = "PENDING"

        now_str = (event_time or datetime.now(timezone.utc)).isoformat()
        record = self._requests[provider_ref_id]
        record["execution_status"] = action_upper
        record["status_updated_at"] = now_str

        return record.copy()

    def generate_webhook_payload(
        self,
        provider_ref_id: str,
        execution_status: str,
        event_type: Optional[str] = None,
        event_time: Optional[datetime] = None,
        secret: Optional[str] = None,
    ) -> Tuple[bytes, str]:
        """Generate a valid signed mock webhook payload bytes and signature header for testing.

        Args:
            provider_ref_id: Signature request identifier.
            execution_status: Status string ('SIGNED', 'DECLINED', etc.).
            event_type: Optional custom event type name.
            event_time: Optional event timestamp.
            secret: Optional HMAC secret override.

        Returns:
            Tuple of (payload_bytes, signature_header_str).
        """
        secret_key = secret or self.webhook_secret
        ts_str = (event_time or datetime.now(timezone.utc)).isoformat()
        status_norm = execution_status.upper()
        evt = event_type or f"signature.{status_norm.lower()}"

        body_dict = {
            "event_type": evt,
            "provider_ref_id": provider_ref_id,
            "execution_status": status_norm,
            "event_timestamp": ts_str,
        }

        payload_bytes = json.dumps(body_dict).encode("utf-8")
        sig = hmac.new(
            secret_key.encode("utf-8"), payload_bytes, hashlib.sha256
        ).hexdigest()

        return payload_bytes, f"sha256={sig}"


# Alias class name for flexible imports
MockESignatureProvider = MockESignProvider
