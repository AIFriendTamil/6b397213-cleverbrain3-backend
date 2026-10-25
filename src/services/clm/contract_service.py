"""Contract Lifecycle Management core contract domain service.

Provides business logic for contract creation, querying, detail loading,
filtering by status/counterparty/renewal timeframe, updating, deletion,
amendment history audit logging with HMAC verification, and RBAC authorization checks.
"""

import hashlib
import hmac
import logging
from datetime import date as date_type, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Union

from sqlalchemy import func, or_
from sqlalchemy.orm import Session as DBSession, joinedload

from src.lib.config import settings
from src.models.contract import (
    AmendmentAuditLog,
    ClauseHighlight,
    ClauseType,
    Contract,
    ContractStatus,
    SLACommitment,
)

logger = logging.getLogger(__name__)


class ContractServiceError(Exception):
    """Base exception class for contract service operations."""

    pass


class ContractNotFoundError(ContractServiceError):
    """Raised when a requested contract is not found in the persistence store."""

    pass


class ContractPermissionDeniedError(ContractServiceError):
    """Raised when user role authorization check fails for a contract operation."""

    pass


class ContractValidationError(ContractServiceError):
    """Raised when contract payload or parameter validation fails."""

    pass


class AmendmentIntegrityError(ContractServiceError):
    """Raised when an amendment audit log record fails HMAC integrity verification."""

    pass


class ContractTamperedError(AmendmentIntegrityError):
    """Raised when record tampering is detected in contract amendment audit logs."""

    pass


# RBAC Role Mapping Permitted Sets
READ_ROLES: Set[str] = {
    "stakeholder",
    "legal_manager",
    "auditor",
    "admin",
    "executive",
    "superadmin",
    "all",
    "*",
}

WRITE_ROLES: Set[str] = {
    "legal_manager",
    "admin",
    "superadmin",
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

AUDIT_ROLES: Set[str] = {
    "auditor",
    "legal_manager",
    "admin",
    "superadmin",
    "all",
    "*",
}


def normalize_roles(roles: Optional[Union[str, Iterable[str]]]) -> Set[str]:
    """Normalize user roles input into a set of clean lowercase role strings.

    Args:
        roles: User role string, comma-separated string, or iterable of role strings.

    Returns:
        Set of normalized lowercase role strings.
    """
    if not roles:
        return set()
    if isinstance(roles, str):
        parts = [r.strip().lower() for r in roles.split(",") if r.strip()]
        return set(parts)

    result: Set[str] = set()
    for role in roles:
        if isinstance(role, str) and role.strip():
            result.add(role.strip().lower())
    return result


def validate_contract_access(
    roles: Optional[Union[str, Iterable[str]]],
    action: str = "read",
) -> Set[str]:
    """Validate whether user roles grant permission for the specified action.

    Args:
        roles: User role string or iterable of role strings.
        action: Requested action string ("read", "write", "delete", "audit").

    Returns:
        Set of normalized user role strings.

    Raises:
        ContractPermissionDeniedError: If roles do not satisfy action requirements.
    """
    norm_roles = normalize_roles(roles)
    if not norm_roles:
        raise ContractPermissionDeniedError(
            "Access denied: missing or empty user role authorization."
        )

    permitted_map = {
        "read": READ_ROLES,
        "write": WRITE_ROLES,
        "delete": DELETE_ROLES,
        "audit": AUDIT_ROLES,
    }
    allowed_roles = permitted_map.get(action.lower(), READ_ROLES)

    if not norm_roles.intersection(allowed_roles):
        logger.warning(
            "RBAC authorization denied for action '%s' with roles: %s",
            action,
            norm_roles,
        )
        raise ContractPermissionDeniedError(
            f"User with roles {sorted(list(norm_roles))} is not authorized to perform '{action}' action."
        )

    return norm_roles


def _parse_date_field(value: Any, field_name: str) -> date_type:
    """Helper to parse string or date into date_type."""
    if isinstance(value, date_type) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date_type.fromisoformat(value.strip())
        except ValueError:
            raise ContractValidationError(
                f"Invalid date format for field '{field_name}'. Expected YYYY-MM-DD."
            )
    raise ContractValidationError(f"Field '{field_name}' must be a valid date or ISO date string.")


def _parse_status_enum(status_val: Any) -> ContractStatus:
    """Helper to parse string or enum into ContractStatus."""
    if isinstance(status_val, ContractStatus):
        return status_val
    if isinstance(status_val, str):
        cleaned = status_val.strip().upper()
        try:
            return ContractStatus(cleaned)
        except ValueError:
            valid = [e.value for e in ContractStatus]
            raise ContractValidationError(
                f"Invalid contract status '{status_val}'. Must be one of: {valid}"
            )
    return ContractStatus.DRAFT


def create_contract(
    db: DBSession,
    data: Dict[str, Any],
    roles: Optional[Union[str, Iterable[str]]] = None,
) -> Contract:
    """Create a new contract record with optional clauses and SLA commitments.

    Args:
        db: Synchronous SQLAlchemy database session.
        data: Contract field values and optional nested clauses / SLA lists.
        roles: User role string or iterable for RBAC verification.

    Returns:
        Created Contract ORM entity.

    Raises:
        ContractPermissionDeniedError: If user lacks write permission.
        ContractValidationError: If required fields are missing or duplicate contract number exists.
    """
    validate_contract_access(roles, action="write")

    contract_number = data.get("contract_number")
    title = data.get("title")
    counterparty = data.get("counterparty")
    effective_on_raw = data.get("effective_on")
    expires_on_raw = data.get("expires_on")

    if not contract_number or not title or not counterparty:
        raise ContractValidationError(
            "Missing required fields: 'contract_number', 'title', and 'counterparty' are mandatory."
        )
    if effective_on_raw is None or expires_on_raw is None:
        raise ContractValidationError(
            "Missing required date fields: 'effective_on' and 'expires_on' are mandatory."
        )

    # Check for duplicate contract number
    existing = (
        db.query(Contract)
        .filter(Contract.contract_number == str(contract_number).strip())
        .first()
    )
    if existing:
        raise ContractValidationError(
            f"Contract with contract number '{contract_number}' already exists."
        )

    effective_on = _parse_date_field(effective_on_raw, "effective_on")
    expires_on = _parse_date_field(expires_on_raw, "expires_on")
    status_enum = _parse_status_enum(data.get("status", ContractStatus.DRAFT))

    contract = Contract(
        contract_number=str(contract_number).strip(),
        title=str(title).strip(),
        counterparty=str(counterparty).strip(),
        status=status_enum,
        effective_on=effective_on,
        expires_on=expires_on,
        auto_renew=bool(data.get("auto_renew", False)),
    )

    # Process nested clause highlights
    clauses_data = data.get("clauses") or []
    for clause_item in clauses_data:
        c_title = clause_item.get("title")
        c_content = clause_item.get("content")
        c_type_raw = clause_item.get("clause_type", ClauseType.SPECIAL_TERM)
        if c_title and c_content:
            clause_type_enum = (
                c_type_raw
                if isinstance(c_type_raw, ClauseType)
                else ClauseType(str(c_type_raw).upper())
            )
            clause_obj = ClauseHighlight(
                title=str(c_title).strip(),
                clause_type=clause_type_enum,
                content=str(c_content).strip(),
            )
            contract.clauses.append(clause_obj)

    # Process nested SLA commitments
    sla_data = data.get("sla_commitments") or []
    for sla_item in sla_data:
        s_metric = sla_item.get("metric_name")
        s_target = sla_item.get("target_value")
        s_penalty = sla_item.get("penalty_terms")
        if s_metric and s_target:
            sla_obj = SLACommitment(
                metric_name=str(s_metric).strip(),
                target_value=str(s_target).strip(),
                penalty_terms=str(s_penalty).strip() if s_penalty else None,
            )
            contract.sla_commitments.append(sla_obj)

    db.add(contract)
    db.commit()
    db.refresh(contract)
    logger.info("Created contract '%s' (ID: %s)", contract.contract_number, contract.id)
    return contract


def get_contract_detail(
    db: DBSession,
    contract_id: str,
    roles: Optional[Union[str, Iterable[str]]] = None,
) -> Contract:
    """Retrieve detailed contract record including clause highlights and SLA terms.

    Args:
        db: Synchronous SQLAlchemy database session.
        contract_id: UUID string of contract.
        roles: User role string or iterable for RBAC verification.

    Returns:
        Contract ORM entity with loaded details.

    Raises:
        ContractPermissionDeniedError: If user lacks read permission.
        ContractNotFoundError: If contract ID does not exist.
    """
    validate_contract_access(roles, action="read")

    contract = (
        db.query(Contract)
        .options(
            joinedload(Contract.clauses),
            joinedload(Contract.sla_commitments),
            joinedload(Contract.document_versions),
        )
        .filter(Contract.id == contract_id)
        .first()
    )

    if not contract:
        raise ContractNotFoundError(f"Contract with ID '{contract_id}' not found.")

    return contract


def list_contracts(
    db: DBSession,
    status: Optional[Union[str, ContractStatus]] = None,
    counterparty: Optional[str] = None,
    keyword: Optional[str] = None,
    renewal_days: Optional[int] = None,
    current_date: Optional[date_type] = None,
    roles: Optional[Union[str, Iterable[str]]] = None,
) -> List[Contract]:
    """Query and filter contract repository records.

    Args:
        db: Synchronous SQLAlchemy database session.
        status: Optional status string or ContractStatus enum filter.
        counterparty: Optional counterparty name substring filter.
        keyword: Optional search keyword matching title, contract_number, or counterparty.
        renewal_days: Optional integer timeframe window for contracts expiring within N days.
        current_date: Optional reference date for renewal window calculations (defaults to UTC today).
        roles: User role string or iterable for RBAC verification.

    Returns:
        List of matching Contract ORM entities.

    Raises:
        ContractPermissionDeniedError: If user lacks read permission.
        ContractValidationError: If search parameters are invalid.
    """
    validate_contract_access(roles, action="read")

    query = db.query(Contract)

    # 1. Filter by status
    if status is not None:
        status_enum = _parse_status_enum(status)
        query = query.filter(Contract.status == status_enum)

    # 2. Filter by counterparty
    if counterparty and str(counterparty).strip():
        cp_term = f"%{str(counterparty).strip()}%"
        query = query.filter(Contract.counterparty.ilike(cp_term))

    # 3. Filter by keyword
    if keyword and str(keyword).strip():
        kw_term = f"%{str(keyword).strip()}%"
        query = query.filter(
            or_(
                Contract.title.ilike(kw_term),
                Contract.contract_number.ilike(kw_term),
                Contract.counterparty.ilike(kw_term),
            )
        )

    # 4. Filter by renewal timeframe
    if renewal_days is not None:
        if renewal_days < 0:
            raise ContractValidationError("Parameter 'renewal_days' must be non-negative.")

        ref_date = current_date or datetime.now(timezone.utc).date()
        end_window = ref_date + timedelta(days=renewal_days)
        query = query.filter(
            Contract.expires_on >= ref_date,
            Contract.expires_on <= end_window,
        )

    contracts = query.order_by(Contract.expires_on.asc(), Contract.contract_number.asc()).all()
    return contracts


def update_contract(
    db: DBSession,
    contract_id: str,
    data: Dict[str, Any],
    roles: Optional[Union[str, Iterable[str]]] = None,
) -> Contract:
    """Update contract fields and attributes.

    Args:
        db: Synchronous SQLAlchemy database session.
        contract_id: UUID string of contract to update.
        data: Dict of fields to update.
        roles: User role string or iterable for RBAC verification.

    Returns:
        Updated Contract ORM entity.

    Raises:
        ContractPermissionDeniedError: If user lacks write permission.
        ContractNotFoundError: If contract ID does not exist.
        ContractValidationError: If date or status values are invalid.
    """
    validate_contract_access(roles, action="write")

    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise ContractNotFoundError(f"Contract with ID '{contract_id}' not found.")

    if "title" in data and data["title"]:
        contract.title = str(data["title"]).strip()
    if "counterparty" in data and data["counterparty"]:
        contract.counterparty = str(data["counterparty"]).strip()
    if "status" in data:
        contract.status = _parse_status_enum(data["status"])
    if "effective_on" in data:
        contract.effective_on = _parse_date_field(data["effective_on"], "effective_on")
    if "expires_on" in data:
        contract.expires_on = _parse_date_field(data["expires_on"], "expires_on")
    if "auto_renew" in data:
        contract.auto_renew = bool(data["auto_renew"])

    db.commit()
    db.refresh(contract)
    logger.info("Updated contract '%s' (ID: %s)", contract.contract_number, contract.id)
    return contract


def delete_contract(
    db: DBSession,
    contract_id: str,
    roles: Optional[Union[str, Iterable[str]]] = None,
) -> None:
    """Delete a contract record from the persistence store.

    Args:
        db: Synchronous SQLAlchemy database session.
        contract_id: UUID string of contract to delete.
        roles: User role string or iterable for RBAC verification.

    Raises:
        ContractPermissionDeniedError: If user lacks delete permission.
        ContractNotFoundError: If contract ID does not exist.
    """
    validate_contract_access(roles, action="delete")

    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise ContractNotFoundError(f"Contract with ID '{contract_id}' not found.")

    db.delete(contract)
    db.commit()
    logger.info("Deleted contract '%s' (ID: %s)", contract.contract_number, contract_id)


def compute_amendment_integrity_hash(
    contract_id: str,
    amendment_number: int,
    summary: str,
    user_id: str,
    timestamp: datetime,
    secret_key: Optional[str] = None,
) -> str:
    """Compute SHA-256 HMAC integrity hash for contract amendment log fields.

    Args:
        contract_id: UUID string of the contract.
        amendment_number: Sequential integer index of the amendment.
        summary: Text summary describing amendment changes.
        user_id: String identifier of the user recording the amendment.
        timestamp: Timezone-aware or naive UTC datetime of amendment entry.
        secret_key: Optional HMAC secret key override.

    Returns:
        Hex-encoded SHA-256 HMAC string digest.
    """
    key = secret_key or getattr(settings, "CLM_HMAC_SECRET_KEY", "dev-clm-hmac-secret-key-change-in-prod")
    if isinstance(timestamp, datetime):
        if timestamp.tzinfo is None:
            ts_dt = timestamp.replace(tzinfo=timezone.utc)
        else:
            ts_dt = timestamp.astimezone(timezone.utc)
        ts_str = ts_dt.isoformat()
    else:
        ts_str = str(timestamp)

    canonical_payload = f"{contract_id}|{amendment_number}|{summary.strip()}|{user_id.strip()}|{ts_str}"
    return hmac.new(
        key.encode("utf-8"),
        canonical_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_amendment_integrity(
    log: AmendmentAuditLog,
    secret_key: Optional[str] = None,
) -> bool:
    """Verify cryptographic SHA-256 HMAC integrity of an amendment audit log record.

    Args:
        log: AmendmentAuditLog ORM model instance.
        secret_key: Optional HMAC secret key override.

    Returns:
        True if record integrity hash matches computed HMAC, False otherwise.
    """
    expected_hash = compute_amendment_integrity_hash(
        contract_id=log.contract_id,
        amendment_number=log.amendment_number,
        summary=log.summary,
        user_id=log.user_id,
        timestamp=log.timestamp,
        secret_key=secret_key,
    )
    return hmac.compare_digest(log.integrity_hash, expected_hash)


def record_amendment(
    db: DBSession,
    contract_id: str,
    summary: str,
    user_id: str,
    timestamp: Optional[datetime] = None,
    roles: Optional[Union[str, Iterable[str]]] = None,
    secret_key: Optional[str] = None,
) -> AmendmentAuditLog:
    """Record a new contract amendment with cryptographic SHA-256 HMAC integrity verification.

    Args:
        db: Synchronous SQLAlchemy database session.
        contract_id: UUID string of the target contract.
        summary: Text summary describing amendment changes.
        user_id: String identifier of the user recording the amendment.
        timestamp: Optional explicit entry datetime (defaults to current UTC time).
        roles: User role string or iterable for RBAC verification.
        secret_key: Optional HMAC secret key override.

    Returns:
        Created AmendmentAuditLog ORM entity.

    Raises:
        ContractPermissionDeniedError: If user lacks write permission.
        ContractNotFoundError: If target contract ID does not exist.
        ContractValidationError: If required fields are missing or empty.
    """
    validate_contract_access(roles, action="write")

    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise ContractNotFoundError(f"Contract with ID '{contract_id}' not found.")

    if not summary or not str(summary).strip():
        raise ContractValidationError("Field 'summary' is mandatory and cannot be empty.")
    if not user_id or not str(user_id).strip():
        raise ContractValidationError("Field 'user_id' is mandatory and cannot be empty.")

    clean_summary = str(summary).strip()
    clean_user_id = str(user_id).strip()

    max_num = (
        db.query(func.max(AmendmentAuditLog.amendment_number))
        .filter(AmendmentAuditLog.contract_id == contract_id)
        .scalar()
    )
    amendment_number = (max_num or 0) + 1

    entry_time = timestamp or datetime.now(timezone.utc)
    if entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=timezone.utc)
    else:
        entry_time = entry_time.astimezone(timezone.utc)

    integrity_hash = compute_amendment_integrity_hash(
        contract_id=contract_id,
        amendment_number=amendment_number,
        summary=clean_summary,
        user_id=clean_user_id,
        timestamp=entry_time,
        secret_key=secret_key,
    )

    amendment_log = AmendmentAuditLog(
        contract_id=contract_id,
        amendment_number=amendment_number,
        summary=clean_summary,
        user_id=clean_user_id,
        timestamp=entry_time,
        integrity_hash=integrity_hash,
    )

    db.add(amendment_log)
    db.commit()
    db.refresh(amendment_log)
    logger.info(
        "Recorded amendment #%d for contract '%s' (user: %s, HMAC: %s...)",
        amendment_number,
        contract_id,
        clean_user_id,
        integrity_hash[:8],
    )
    return amendment_log


def get_amendment_history(
    db: DBSession,
    contract_id: str,
    roles: Optional[Union[str, Iterable[str]]] = None,
    verify_integrity: bool = True,
    secret_key: Optional[str] = None,
) -> List[AmendmentAuditLog]:
    """Retrieve chronological amendment audit history with HMAC integrity verification.

    Args:
        db: Synchronous SQLAlchemy database session.
        contract_id: UUID string of the target contract.
        roles: User role string or iterable for RBAC verification.
        verify_integrity: Whether to verify HMAC signatures of retrieved logs.
        secret_key: Optional HMAC secret key override.

    Returns:
        List of AmendmentAuditLog ORM entities ordered chronologically by amendment_number.

    Raises:
        ContractPermissionDeniedError: If user lacks read/audit permission.
        ContractNotFoundError: If target contract ID does not exist.
        AmendmentIntegrityError: If an audit log record fails HMAC integrity verification.
    """
    validate_contract_access(roles, action="read")

    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise ContractNotFoundError(f"Contract with ID '{contract_id}' not found.")

    logs = (
        db.query(AmendmentAuditLog)
        .filter(AmendmentAuditLog.contract_id == contract_id)
        .order_by(AmendmentAuditLog.amendment_number.asc())
        .all()
    )

    if verify_integrity:
        for log in logs:
            if not verify_amendment_integrity(log, secret_key=secret_key):
                logger.error(
                    "Integrity verification failed for amendment #%d (log ID: %s) on contract %s",
                    log.amendment_number,
                    log.id,
                    contract_id,
                )
                raise AmendmentIntegrityError(
                    f"Audit log integrity verification failed for amendment #{log.amendment_number} "
                    f"(contract ID: {contract_id}). Possible record tampering detected."
                )

    return logs


# Aliases for API routers and external service calls
add_amendment = record_amendment
create_amendment = record_amendment
create_amendment_log = record_amendment
list_amendment_logs = get_amendment_history
get_contract_amendments = get_amendment_history
