"""Contract Lifecycle Management renewal tracking domain service.

Provides business logic for calculating contract renewal warning stages (90, 60, 30 days),
checking expiration dates, executing daily idempotent renewal warning dispatches,
preventing duplicate notifications within a 24-hour cycle or per warning stage,
and filtering expiring contracts for repository queries.
"""

import logging
import uuid
from datetime import date as date_type, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Union

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from src.lib.config import settings
from src.models.contract import (
    Contract,
    ContractStatus,
    RenewalNotificationLog,
    RenewalWarningStage,
)

logger = logging.getLogger(__name__)


class RenewalServiceError(Exception):
    """Base exception class for contract renewal service operations."""

    pass


class RenewalNotFoundError(RenewalServiceError):
    """Raised when a requested contract or renewal log record is not found."""

    pass


class RenewalPermissionDeniedError(RenewalServiceError):
    """Raised when user role authorization check fails for renewal operations."""

    pass


class RenewalValidationError(RenewalServiceError):
    """Raised when input parameters or calculation arguments are invalid."""

    pass


# RBAC Role Mapping Permitted Sets
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


def validate_renewal_access(
    roles: Optional[Union[str, Iterable[str]]],
    action: str = "read",
) -> Set[str]:
    """Validate user role authorization against permitted roles for renewal operations."""
    normalized = normalize_roles(roles)
    if action == "read":
        permitted = READ_ROLES
    elif action == "write":
        permitted = WRITE_ROLES
    elif action == "execute":
        permitted = EXECUTE_ROLES
    else:
        raise RenewalValidationError(f"Invalid renewal action specified: '{action}'")

    if not normalized.intersection(permitted):
        raise RenewalPermissionDeniedError(
            f"Role(s) '{sorted(list(normalized))}' not authorized to perform '{action}' on renewal service."
        )
    return normalized


def calculate_days_until_expiration(
    expires_on: date_type,
    as_of_date: Optional[date_type] = None,
) -> int:
    """Calculate the exact integer number of calendar days remaining until contract expiration.

    Handles month length differences and leap years seamlessly via Python date arithmetic.

    Args:
        expires_on: The contract expiration date.
        as_of_date: Evaluation target date (defaults to current UTC date).

    Returns:
        Integer number of days remaining until expiration. Negative if past expiration.
    """
    if as_of_date is None:
        as_of_date = datetime.now(timezone.utc).date()
    return (expires_on - as_of_date).days


def determine_warning_stage(
    expires_on: date_type,
    as_of_date: Optional[date_type] = None,
    exact_day_only: bool = False,
) -> Optional[RenewalWarningStage]:
    """Determine the applicable renewal warning stage (DAYS_90, DAYS_60, DAYS_30).

    Args:
        expires_on: Contract expiration date.
        as_of_date: Evaluation target date (defaults to current UTC date).
        exact_day_only: If True, matches exact milestone days (90, 60, 30).
            If False (default), matches threshold windows:
            - 0 <= days <= 30 -> DAYS_30
            - 30 < days <= 60 -> DAYS_60
            - 60 < days <= 90 -> DAYS_90

    Returns:
        RenewalWarningStage enum or None if outside warning windows or expired.
    """
    days_remaining = calculate_days_until_expiration(expires_on, as_of_date)

    if days_remaining < 0:
        # Contract has already expired
        return None

    if exact_day_only:
        if days_remaining == 90:
            return RenewalWarningStage.DAYS_90
        elif days_remaining == 60:
            return RenewalWarningStage.DAYS_60
        elif days_remaining == 30:
            return RenewalWarningStage.DAYS_30
        return None

    if days_remaining <= 30:
        return RenewalWarningStage.DAYS_30
    elif days_remaining <= 60:
        return RenewalWarningStage.DAYS_60
    elif days_remaining <= 90:
        return RenewalWarningStage.DAYS_90

    return None


def is_notification_dispatched_today(
    db_session: DBSession,
    contract_id: str,
    warning_stage: RenewalWarningStage,
    as_of_date: Optional[date_type] = None,
) -> bool:
    """Check if a notification log exists for the contract, stage, and target date.

    Enforces NFR-4 daily idempotency check.
    """
    target_date = as_of_date or datetime.now(timezone.utc).date()
    existing = (
        db_session.query(RenewalNotificationLog)
        .filter(
            RenewalNotificationLog.contract_id == contract_id,
            RenewalNotificationLog.warning_stage == warning_stage,
            RenewalNotificationLog.dispatched_at_date == target_date,
        )
        .first()
    )
    return existing is not None


def is_notification_dispatched_for_stage(
    db_session: DBSession,
    contract_id: str,
    warning_stage: RenewalWarningStage,
    contract_updated_at: Optional[datetime] = None,
) -> bool:
    """Check if a notification has already been dispatched for this contract and stage.

    If contract_updated_at is provided, ignores notification logs recorded prior to
    the contract's last update timestamp (handling contract extension/revision edge cases).
    """
    query = db_session.query(RenewalNotificationLog).filter(
        RenewalNotificationLog.contract_id == contract_id,
        RenewalNotificationLog.warning_stage == warning_stage,
    )

    if contract_updated_at:
        query = query.filter(RenewalNotificationLog.dispatched_at >= contract_updated_at)

    existing = query.first()
    return existing is not None


def get_notification_log_for_stage_and_date(
    db_session: DBSession,
    contract_id: str,
    warning_stage: RenewalWarningStage,
    as_of_date: Optional[date_type] = None,
) -> Optional[RenewalNotificationLog]:
    """Retrieve an existing notification log record for a contract, stage, and date."""
    target_date = as_of_date or datetime.now(timezone.utc).date()
    return (
        db_session.query(RenewalNotificationLog)
        .filter(
            RenewalNotificationLog.contract_id == contract_id,
            RenewalNotificationLog.warning_stage == warning_stage,
            RenewalNotificationLog.dispatched_at_date == target_date,
        )
        .first()
    )


def dispatch_renewal_notification(
    db_session: DBSession,
    contract: Contract,
    warning_stage: RenewalWarningStage,
    recipient_target: Optional[str] = None,
    as_of_date: Optional[date_type] = None,
    force_redispatch: bool = False,
    roles: Optional[Union[str, Iterable[str]]] = None,
) -> Optional[RenewalNotificationLog]:
    """Dispatch a renewal warning notification and record an idempotent notification log.

    Args:
        db_session: Synchronous SQLAlchemy session.
        contract: Target Contract ORM entity.
        warning_stage: RenewalWarningStage enum (DAYS_90, DAYS_60, DAYS_30).
        recipient_target: Target recipient string (email/counterparty).
        as_of_date: Date context for notification dispatch (defaults to current UTC date).
        force_redispatch: If True, bypasses application-level idempotency checks.
        roles: Optional user roles for RBAC authorization check.

    Returns:
        RenewalNotificationLog instance if dispatched/recorded, or existing log if skipped/duplicate.
    """
    if roles is not None:
        validate_renewal_access(roles, action="write")

    target_date = as_of_date or datetime.now(timezone.utc).date()
    target_recipient = (
        recipient_target
        or contract.counterparty
        or "legal-alerts@company.com"
    )

    # Application-level idempotency checks
    if not force_redispatch:
        # 1. Daily idempotency check
        if is_notification_dispatched_today(db_session, contract.id, warning_stage, target_date):
            logger.info(
                "Renewal notification for contract %s stage %s already dispatched on %s. Skipping.",
                contract.id,
                warning_stage.value,
                target_date,
            )
            return get_notification_log_for_stage_and_date(db_session, contract.id, warning_stage, target_date)

        # 2. Stage-level idempotency check (accounting for contract revisions)
        if is_notification_dispatched_for_stage(
            db_session,
            contract.id,
            warning_stage,
            contract_updated_at=contract.updated_at,
        ):
            logger.info(
                "Renewal notification for contract %s stage %s was previously dispatched for current revision. Skipping.",
                contract.id,
                warning_stage.value,
            )
            return (
                db_session.query(RenewalNotificationLog)
                .filter(
                    RenewalNotificationLog.contract_id == contract.id,
                    RenewalNotificationLog.warning_stage == warning_stage,
                )
                .order_by(RenewalNotificationLog.dispatched_at.desc())
                .first()
            )

    dispatched_dt = datetime.now(timezone.utc)
    if as_of_date and as_of_date != dispatched_dt.date():
        dispatched_dt = datetime.combine(as_of_date, datetime.now(timezone.utc).time()).replace(tzinfo=timezone.utc)

    log_entry = RenewalNotificationLog(
        id=str(uuid.uuid4()),
        contract_id=contract.id,
        warning_stage=warning_stage,
        dispatched_at=dispatched_dt,
        dispatched_at_date=target_date,
        recipient_target=target_recipient,
    )

    try:
        db_session.add(log_entry)
        db_session.flush()
        db_session.commit()
        logger.info(
            "Dispatched renewal warning for contract %s stage %s to %s",
            contract.id,
            warning_stage.value,
            target_recipient,
        )
        return log_entry
    except IntegrityError as err:
        db_session.rollback()
        logger.warning(
            "Suppressed duplicate renewal notification for contract %s, stage %s, date %s due to constraint violation: %s",
            contract.id,
            warning_stage.value,
            target_date,
            err,
        )
        return get_notification_log_for_stage_and_date(db_session, contract.id, warning_stage, target_date)


def process_contract_renewals(
    db_session: DBSession,
    as_of_date: Optional[date_type] = None,
    force_redispatch: bool = False,
    exact_day_only: bool = False,
    roles: Optional[Union[str, Iterable[str]]] = None,
) -> Dict[str, Any]:
    """Execute batch daily contract renewal calculations and dispatch warning notifications.

    Acts as core domain service logic for background workers (FR-3, FR-4, NFR-4).

    Args:
        db_session: Synchronous SQLAlchemy session.
        as_of_date: Evaluation target date (defaults to current UTC date).
        force_redispatch: If True, bypasses idempotency checks.
        exact_day_only: If True, evaluates only exact 90, 60, 30 day milestones.
        roles: Optional user roles for RBAC authorization check.

    Returns:
        Dict summarizing processing outcomes and dispatched log records.
    """
    if roles is not None:
        validate_renewal_access(roles, action="execute")

    target_date = as_of_date or datetime.now(timezone.utc).date()

    # Query active contracts
    active_contracts = (
        db_session.query(Contract)
        .filter(Contract.status == ContractStatus.ACTIVE)
        .all()
    )

    dispatched_logs: List[RenewalNotificationLog] = []
    skipped_count = 0

    for contract in active_contracts:
        stage = determine_warning_stage(
            expires_on=contract.expires_on,
            as_of_date=target_date,
            exact_day_only=exact_day_only,
        )

        if stage is None:
            continue

        log_entry = dispatch_renewal_notification(
            db_session=db_session,
            contract=contract,
            warning_stage=stage,
            as_of_date=target_date,
            force_redispatch=force_redispatch,
        )

        if log_entry is not None and log_entry.dispatched_at_date == target_date:
            dispatched_logs.append(log_entry)
        else:
            skipped_count += 1

    return {
        "as_of_date": target_date.isoformat(),
        "evaluated_contracts_count": len(active_contracts),
        "dispatched_count": len(dispatched_logs),
        "skipped_count": skipped_count,
        "dispatched_logs": [
            {
                "id": log.id,
                "contract_id": log.contract_id,
                "warning_stage": log.warning_stage.value,
                "dispatched_at": log.dispatched_at.isoformat(),
                "dispatched_at_date": log.dispatched_at_date.isoformat(),
                "recipient_target": log.recipient_target,
            }
            for log in dispatched_logs
        ],
    }


def list_renewal_notification_logs(
    db_session: DBSession,
    contract_id: Optional[str] = None,
    warning_stage: Optional[Union[RenewalWarningStage, str]] = None,
    roles: Optional[Union[str, Iterable[str]]] = None,
) -> List[RenewalNotificationLog]:
    """Retrieve historical renewal notification logs with optional contract and stage filters."""
    if roles is not None:
        validate_renewal_access(roles, action="read")

    query = db_session.query(RenewalNotificationLog)

    if contract_id:
        query = query.filter(RenewalNotificationLog.contract_id == contract_id)

    if warning_stage:
        if isinstance(warning_stage, str):
            warning_stage = RenewalWarningStage(warning_stage.upper())
        query = query.filter(RenewalNotificationLog.warning_stage == warning_stage)

    return query.order_by(RenewalNotificationLog.dispatched_at.desc()).all()


def get_expiring_contracts(
    db_session: DBSession,
    renewal_days: Optional[int] = None,
    as_of_date: Optional[date_type] = None,
    roles: Optional[Union[str, Iterable[str]]] = None,
) -> List[Contract]:
    """Retrieve active contracts expiring within a specified number of days (e.g. 90, 60, 30 days).

    Supports FR-7 API filtering by renewal timeframe.
    """
    if roles is not None:
        validate_renewal_access(roles, action="read")

    target_date = as_of_date or datetime.now(timezone.utc).date()
    query = db_session.query(Contract).filter(Contract.status == ContractStatus.ACTIVE)

    if renewal_days is not None and renewal_days > 0:
        cutoff_date = target_date + timedelta(days=renewal_days)
        query = query.filter(
            Contract.expires_on >= target_date,
            Contract.expires_on <= cutoff_date,
        )

    return query.order_by(Contract.expires_on.asc()).all()
