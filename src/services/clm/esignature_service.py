"""E-Signature Execution Tracking Hub and Webhook Service.

Provides domain business logic for creating e-signature requests, tracking execution states,
listing active signature requests for the tracking hub, processing incoming provider webhooks
with out-of-order timestamp validation, voiding/canceling signature requests, and enforcing RBAC.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Union

from sqlalchemy.orm import Session as DBSession

from src.lib.config import settings
from src.lib.logging import get_trace_id
from src.models.contract import (
    Contract,
    ESignatureExecutionStatus,
    ESignatureRequest,
)
from src.services.clm.ports import (
    ESignatureError,
    ESignaturePort,
    ESignatureRequestNotFoundError,
    ESignatureWebhookVerificationError,
)

logger = logging.getLogger(__name__)


class ESignatureServiceError(Exception):
    """Base exception class for e-signature service operations."""

    pass


class ESignatureNotFoundError(ESignatureServiceError):
    """Raised when an e-signature request or contract is not found."""

    pass


class ESignaturePermissionDeniedError(ESignatureServiceError):
    """Raised when user role authorization check fails for an e-signature operation."""

    pass


class ESignatureValidationError(ESignatureServiceError):
    """Raised when e-signature payload, recipient, or request parameter validation fails."""

    pass


class ESignatureWebhookError(ESignatureServiceError):
    """Raised when webhook signature verification or payload parsing encounters an error."""

    pass


# RBAC Permitted Role Sets
READ_ROLES: Set[str] = {
    "stakeholder",
    "legal_manager",
    "auditor",
    "admin",
    "executive",
    "superadmin",
    "account_manager",
    "all",
    "*",
}

WRITE_ROLES: Set[str] = {
    "legal_manager",
    "admin",
    "superadmin",
    "account_manager",
    "all",
    "*",
}

DELETE_ROLES: Set[str] = {
    "legal_manager",
    "admin",
    "superadmin",
    "all",
    "*",
}

EXECUTE_ROLES: Set[str] = {
    "legal_manager",
    "admin",
    "superadmin",
    "system",
    "worker",
    "all",
    "*",
}


def normalize_roles(roles: Optional[Union[str, Iterable[str]]]) -> Set[str]:
    """Normalize input role names to a clean set of lower-case strings."""
    if not roles:
        return set()
    if isinstance(roles, str):
        role_items = roles.replace(";", ",").split(",")
    else:
        role_items = list(roles)

    normalized = set()
    for item in role_items:
        clean = item.strip().lower()
        if clean:
            normalized.add(clean)
    return normalized


def validate_esignature_access(
    roles: Optional[Union[str, Iterable[str]]],
    action: str = "read",
) -> Set[str]:
    """Verify if user roles permit the requested e-signature operation action."""
    user_roles = normalize_roles(roles)

    if not user_roles:
        logger.warning(
            f"E-Signature access denied: missing user roles for action='{action}'",
            extra={"action": action},
        )
        raise ESignaturePermissionDeniedError("Authentication required: missing user roles.")

    allowed_map = {
        "read": READ_ROLES,
        "write": WRITE_ROLES,
        "delete": DELETE_ROLES,
        "execute": EXECUTE_ROLES,
    }

    permitted_roles = allowed_map.get(action.lower())
    if not permitted_roles:
        raise ValueError(f"Unsupported action: '{action}'")

    if not user_roles.intersection(permitted_roles):
        logger.warning(
            f"Role access denied: roles={user_roles} lack permission for action='{action}'",
            extra={"action": action, "user_roles": list(user_roles)},
        )
        raise ESignaturePermissionDeniedError(
            f"User role lacks authorization for '{action}' e-signature operation."
        )

    return user_roles


def parse_datetime_utc(val: Union[datetime, str, int, float, None]) -> datetime:
    """Parse various timestamp representations into a timezone-aware UTC datetime."""
    if val is None:
        return datetime.now(timezone.utc)

    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)

    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=timezone.utc)

    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return datetime.now(timezone.utc)
        if val_str.endswith("Z") or val_str.endswith("z"):
            val_str = val_str[:-1] + "+00:00"
        try:
            parsed_dt = datetime.fromisoformat(val_str)
            if parsed_dt.tzinfo is None:
                return parsed_dt.replace(tzinfo=timezone.utc)
            return parsed_dt.astimezone(timezone.utc)
        except ValueError:
            pass

    return datetime.now(timezone.utc)


def _get_default_esign_provider() -> ESignaturePort:
    """Lazy import and instantiate default MockESignProvider connector."""
    from src.connectors.esignature.mock_esign_provider import MockESignProvider

    return MockESignProvider(webhook_secret=settings.CLM_ESIGN_WEBHOOK_SECRET)


def create_signature_request(
    db: DBSession,
    contract_id: str,
    recipient_email: str,
    recipient_name: str,
    document_content: Optional[bytes] = None,
    filename: Optional[str] = None,
    subject: Optional[str] = None,
    esign_port: Optional[ESignaturePort] = None,
    roles: Optional[Union[str, Iterable[str]]] = None,
    correlation_id: Optional[str] = None,
) -> ESignatureRequest:
    """Create and dispatch an e-signature request for a contract.

    Args:
        db: Synchronous SQLAlchemy database session.
        contract_id: Associated contract UUID.
        recipient_email: Email address of recipient signer.
        recipient_name: Full name of recipient signer.
        document_content: Optional raw document bytes to send to provider.
        filename: Optional filename for the document.
        subject: Optional email notification subject line.
        esign_port: Optional ESignaturePort provider instance.
        roles: User roles for RBAC enforcement.
        correlation_id: Optional correlation/trace identifier for logging.

    Returns:
        Persisted ESignatureRequest ORM instance.

    Raises:
        ESignaturePermissionDeniedError: If roles fail authorization.
        ESignatureValidationError: If recipient or contract parameters are invalid.
        ESignatureNotFoundError: If target contract is not found in database.
    """
    validate_esignature_access(roles, action="write")
    trace_id = correlation_id or get_trace_id() or ""

    if not contract_id or not contract_id.strip():
        raise ESignatureValidationError("Contract ID must be a non-empty string.")

    if not recipient_email or "@" not in recipient_email:
        raise ESignatureValidationError("Valid recipient email address is required.")

    if not recipient_name or not recipient_name.strip():
        raise ESignatureValidationError("Recipient name must be a non-empty string.")

    target_contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not target_contract:
        raise ESignatureNotFoundError(f"Contract with ID '{contract_id}' not found.")

    provider = esign_port or _get_default_esign_provider()
    doc_bytes = document_content or f"Contract {contract_id} document content".encode("utf-8")
    doc_filename = filename or f"contract_{contract_id}.pdf"

    try:
        provider_resp = provider.create_signature_request(
            contract_id=contract_id,
            document_content=doc_bytes,
            filename=doc_filename,
            recipient_email=recipient_email,
            recipient_name=recipient_name,
            subject=subject or f"Signature Request for {target_contract.title}",
        )
    except Exception as exc:
        logger.error(
            f"Failed to create signature request with provider: {exc}",
            extra={"trace_id": trace_id, "contract_id": contract_id},
        )
        raise ESignatureValidationError(f"E-signature provider creation failed: {exc}") from exc

    provider_ref_id = provider_resp.get("provider_ref_id")
    raw_status = str(provider_resp.get("execution_status", "PENDING")).upper()

    try:
        execution_status_enum = ESignatureExecutionStatus(raw_status)
    except ValueError:
        execution_status_enum = ESignatureExecutionStatus.PENDING

    status_updated_at = parse_datetime_utc(provider_resp.get("status_updated_at"))

    esig_request = ESignatureRequest(
        id=str(uuid.uuid4()),
        contract_id=contract_id,
        provider_ref_id=provider_ref_id,
        execution_status=execution_status_enum,
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        status_updated_at=status_updated_at,
        created_at=datetime.now(timezone.utc),
    )

    db.add(esig_request)
    db.commit()
    db.refresh(esig_request)

    logger.info(
        "Successfully created e-signature request record",
        extra={
            "trace_id": trace_id,
            "request_id": esig_request.id,
            "contract_id": contract_id,
            "provider_ref_id": provider_ref_id,
            "recipient_email": recipient_email,
            "execution_status": execution_status_enum.value,
        },
    )

    return esig_request


def list_signature_requests(
    db: DBSession,
    execution_status: Optional[Union[str, ESignatureExecutionStatus]] = None,
    contract_id: Optional[str] = None,
    recipient_email: Optional[str] = None,
    roles: Optional[Union[str, Iterable[str]]] = None,
    correlation_id: Optional[str] = None,
) -> List[ESignatureRequest]:
    """Retrieve signature request records for the E-Signature Tracking Hub.

    Args:
        db: Synchronous SQLAlchemy database session.
        execution_status: Optional execution status filter.
        contract_id: Optional contract ID filter.
        recipient_email: Optional recipient email filter.
        roles: User roles for RBAC enforcement.
        correlation_id: Optional correlation/trace identifier.

    Returns:
        List of matching ESignatureRequest ORM records ordered by status_updated_at desc.
    """
    validate_esignature_access(roles, action="read")
    trace_id = correlation_id or get_trace_id() or ""

    query = db.query(ESignatureRequest)

    if execution_status:
        if isinstance(execution_status, str):
            status_clean = execution_status.strip().upper()
            try:
                status_enum = ESignatureExecutionStatus(status_clean)
                query = query.filter(ESignatureRequest.execution_status == status_enum)
            except ValueError:
                logger.warning(
                    f"Invalid execution_status filter ignored: '{execution_status}'",
                    extra={"trace_id": trace_id},
                )
        elif isinstance(execution_status, ESignatureExecutionStatus):
            query = query.filter(ESignatureRequest.execution_status == execution_status)

    if contract_id:
        query = query.filter(ESignatureRequest.contract_id == contract_id)

    if recipient_email:
        clean_email = recipient_email.strip().lower()
        query = query.filter(ESignatureRequest.recipient_email.ilike(f"%{clean_email}%"))

    results = query.order_by(ESignatureRequest.status_updated_at.desc()).all()

    logger.info(
        f"Fetched {len(results)} e-signature tracking hub request(s)",
        extra={
            "trace_id": trace_id,
            "result_count": len(results),
            "status_filter": str(execution_status) if execution_status else None,
            "contract_id_filter": contract_id,
        },
    )

    return results


def get_signature_request(
    db: DBSession,
    request_id: str,
    roles: Optional[Union[str, Iterable[str]]] = None,
    correlation_id: Optional[str] = None,
) -> ESignatureRequest:
    """Retrieve a single e-signature request record by ID or provider_ref_id.

    Args:
        db: Synchronous SQLAlchemy database session.
        request_id: Signature request UUID or provider_ref_id.
        roles: User roles for RBAC enforcement.
        correlation_id: Optional trace identifier.

    Returns:
        ESignatureRequest ORM record.

    Raises:
        ESignatureNotFoundError: If record is not found.
    """
    validate_esignature_access(roles, action="read")

    esig_req = (
        db.query(ESignatureRequest)
        .filter(
            (ESignatureRequest.id == request_id)
            | (ESignatureRequest.provider_ref_id == request_id)
        )
        .first()
    )

    if not esig_req:
        raise ESignatureNotFoundError(f"E-signature request '{request_id}' not found.")

    return esig_req


def process_webhook_event(
    db: DBSession,
    provider_name: str,
    payload: Union[bytes, str, Dict[str, Any]],
    signature_header: Optional[str] = None,
    esign_port: Optional[ESignaturePort] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Process an incoming e-signature webhook execution event with out-of-order timestamp handling.

    Args:
        db: Synchronous SQLAlchemy database session.
        provider_name: Vendor or provider name (e.g. 'mock', 'docusign').
        payload: Raw body bytes, JSON string, or parsed dict payload.
        signature_header: Optional HTTP signature header for HMAC verification.
        esign_port: Optional ESignaturePort provider implementation.
        correlation_id: Optional correlation/trace identifier for logging.

    Returns:
        Dict detailing outcome ('updated' or 'ignored' with status and timestamps).

    Raises:
        ESignatureWebhookError: If signature verification fails.
        ESignatureValidationError: If payload is missing provider_ref_id.
        ESignatureNotFoundError: If signature request record is not found in DB.
    """
    trace_id = correlation_id or get_trace_id() or ""
    provider = esign_port or _get_default_esign_provider()

    payload_bytes: bytes = b""
    if isinstance(payload, bytes):
        payload_bytes = payload
    elif isinstance(payload, str):
        payload_bytes = payload.encode("utf-8")
    elif isinstance(payload, dict):
        payload_bytes = json.dumps(payload).encode("utf-8")

    if signature_header and payload_bytes:
        is_valid = provider.verify_webhook_signature(payload_bytes, signature_header)
        if not is_valid:
            logger.warning(
                "Webhook HMAC signature verification failed",
                extra={"trace_id": trace_id, "provider": provider_name},
            )
            raise ESignatureWebhookError("Webhook signature verification failed.")

    if hasattr(provider, "parse_webhook_event"):
        event_info = provider.parse_webhook_event(payload)
    else:
        if isinstance(payload, dict):
            p_dict = payload
        else:
            try:
                p_dict = json.loads(payload_bytes.decode("utf-8"))
            except Exception:
                p_dict = {}

        provider_ref_id = p_dict.get("provider_ref_id") or p_dict.get("signature_request_id")
        raw_status = str(p_dict.get("execution_status") or p_dict.get("status") or "").upper()
        event_ts = p_dict.get("event_timestamp") or p_dict.get("status_updated_at")

        event_info = {
            "provider_ref_id": provider_ref_id,
            "execution_status": raw_status or "PENDING",
            "event_timestamp": event_ts,
        }

    provider_ref_id = event_info.get("provider_ref_id")
    if not provider_ref_id:
        logger.warning(
            "Webhook payload missing provider_ref_id",
            extra={"trace_id": trace_id, "event_info": event_info},
        )
        raise ESignatureValidationError("Webhook payload is missing 'provider_ref_id'.")

    raw_status = str(event_info.get("execution_status", "PENDING")).upper()
    if raw_status in ("SIGNED", "COMPLETE", "COMPLETED"):
        new_status_enum = ESignatureExecutionStatus.SIGNED
    elif raw_status in ("DECLINED", "REJECTED"):
        new_status_enum = ESignatureExecutionStatus.DECLINED
    elif raw_status in ("VOIDED", "CANCELED", "CANCELLED"):
        new_status_enum = ESignatureExecutionStatus.VOIDED
    else:
        new_status_enum = ESignatureExecutionStatus.PENDING

    incoming_dt = parse_datetime_utc(event_info.get("event_timestamp"))

    esig_req = (
        db.query(ESignatureRequest)
        .filter(ESignatureRequest.provider_ref_id == provider_ref_id)
        .first()
    )

    if not esig_req:
        logger.warning(
            f"Signature request record not found for provider_ref_id='{provider_ref_id}'",
            extra={"trace_id": trace_id, "provider_ref_id": provider_ref_id},
        )
        raise ESignatureNotFoundError(
            f"Signature request with provider_ref_id '{provider_ref_id}' not found."
        )

    existing_dt = parse_datetime_utc(esig_req.status_updated_at)

    if incoming_dt <= existing_dt:
        logger.warning(
            "Ignored out-of-order or stale webhook event timestamp",
            extra={
                "trace_id": trace_id,
                "provider_ref_id": provider_ref_id,
                "existing_status_updated_at": existing_dt.isoformat(),
                "incoming_event_timestamp": incoming_dt.isoformat(),
                "current_status": esig_req.execution_status.value,
                "incoming_status": new_status_enum.value,
            },
        )
        return {
            "status": "ignored",
            "reason": "stale_timestamp",
            "provider_ref_id": provider_ref_id,
            "execution_status": esig_req.execution_status.value,
            "status_updated_at": existing_dt.isoformat(),
            "incoming_timestamp": incoming_dt.isoformat(),
        }

    esig_req.execution_status = new_status_enum
    esig_req.status_updated_at = incoming_dt

    db.add(esig_req)
    db.commit()
    db.refresh(esig_req)

    logger.info(
        "Successfully updated e-signature request status from webhook",
        extra={
            "trace_id": trace_id,
            "provider_ref_id": provider_ref_id,
            "execution_status": new_status_enum.value,
            "status_updated_at": incoming_dt.isoformat(),
        },
    )

    return {
        "status": "updated",
        "provider_ref_id": provider_ref_id,
        "execution_status": new_status_enum.value,
        "status_updated_at": incoming_dt.isoformat(),
    }


def cancel_signature_request(
    db: DBSession,
    request_id: str,
    roles: Optional[Union[str, Iterable[str]]] = None,
    esign_port: Optional[ESignaturePort] = None,
    correlation_id: Optional[str] = None,
) -> ESignatureRequest:
    """Cancel / void an active signature request.

    Args:
        db: Synchronous SQLAlchemy database session.
        request_id: Signature request UUID or provider_ref_id.
        roles: User roles for RBAC enforcement.
        esign_port: Optional ESignaturePort provider instance.
        correlation_id: Optional correlation/trace identifier.

    Returns:
        Updated ESignatureRequest record with execution_status=VOIDED.

    Raises:
        ESignatureNotFoundError: If record is not found.
        ESignaturePermissionDeniedError: If roles fail authorization.
    """
    validate_esignature_access(roles, action="write")
    trace_id = correlation_id or get_trace_id() or ""

    esig_req = get_signature_request(db, request_id, roles=roles, correlation_id=trace_id)

    provider = esign_port or _get_default_esign_provider()
    if esig_req.provider_ref_id:
        try:
            provider.cancel_signature_request(esig_req.provider_ref_id)
        except Exception as exc:
            logger.warning(
                f"Provider cancellation encountered exception: {exc}",
                extra={"trace_id": trace_id, "provider_ref_id": esig_req.provider_ref_id},
            )

    now_utc = datetime.now(timezone.utc)
    esig_req.execution_status = ESignatureExecutionStatus.VOIDED
    esig_req.status_updated_at = now_utc

    db.add(esig_req)
    db.commit()
    db.refresh(esig_req)

    logger.info(
        "Canceled e-signature request",
        extra={
            "trace_id": trace_id,
            "request_id": esig_req.id,
            "provider_ref_id": esig_req.provider_ref_id,
            "status_updated_at": now_utc.isoformat(),
        },
    )

    return esig_req


def sync_signature_status(
    db: DBSession,
    request_id: str,
    roles: Optional[Union[str, Iterable[str]]] = None,
    esign_port: Optional[ESignaturePort] = None,
    correlation_id: Optional[str] = None,
) -> ESignatureRequest:
    """Query provider status and synchronize DB record if provider timestamp is newer.

    Args:
        db: Synchronous SQLAlchemy database session.
        request_id: Signature request UUID or provider_ref_id.
        roles: User roles for RBAC enforcement.
        esign_port: Optional ESignaturePort implementation.
        correlation_id: Optional correlation trace ID.

    Returns:
        Updated or current ESignatureRequest ORM record.
    """
    validate_esignature_access(roles, action="read")
    trace_id = correlation_id or get_trace_id() or ""

    esig_req = get_signature_request(db, request_id, roles=roles, correlation_id=trace_id)

    if not esig_req.provider_ref_id:
        return esig_req

    provider = esign_port or _get_default_esign_provider()
    try:
        status_resp = provider.get_signature_status(esig_req.provider_ref_id)
    except ESignatureRequestNotFoundError:
        logger.warning(
            f"Provider signature request not found for sync: {esig_req.provider_ref_id}",
            extra={"trace_id": trace_id},
        )
        return esig_req

    raw_status = str(status_resp.get("execution_status", "PENDING")).upper()
    try:
        new_status_enum = ESignatureExecutionStatus(raw_status)
    except ValueError:
        new_status_enum = ESignatureExecutionStatus.PENDING

    provider_dt = parse_datetime_utc(status_resp.get("status_updated_at"))
    existing_dt = parse_datetime_utc(esig_req.status_updated_at)

    if provider_dt > existing_dt:
        esig_req.execution_status = new_status_enum
        esig_req.status_updated_at = provider_dt
        db.add(esig_req)
        db.commit()
        db.refresh(esig_req)

        logger.info(
            "Synchronized signature request status from provider",
            extra={
                "trace_id": trace_id,
                "provider_ref_id": esig_req.provider_ref_id,
                "execution_status": new_status_enum.value,
            },
        )

    return esig_req


class ESignatureService:
    """Class wrapper providing static access to E-Signature domain service operations."""

    create_signature_request = staticmethod(create_signature_request)
    list_signature_requests = staticmethod(list_signature_requests)
    get_signature_request = staticmethod(get_signature_request)
    process_webhook_event = staticmethod(process_webhook_event)
    cancel_signature_request = staticmethod(cancel_signature_request)
    sync_signature_status = staticmethod(sync_signature_status)
    validate_access = staticmethod(validate_esignature_access)
