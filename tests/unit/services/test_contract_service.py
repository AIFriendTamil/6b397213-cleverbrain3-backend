"""Unit tests for Contract Lifecycle Management core domain service (contract_service.py)."""

from datetime import date as date_type, datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.contract import AmendmentAuditLog, ClauseType, Contract, ContractStatus
from src.services.clm.contract_service import (
    AmendmentIntegrityError,
    ContractNotFoundError,
    ContractPermissionDeniedError,
    ContractValidationError,
    compute_amendment_integrity_hash,
    create_contract,
    delete_contract,
    get_amendment_history,
    get_contract_detail,
    list_contracts,
    normalize_roles,
    record_amendment,
    update_contract,
    validate_contract_access,
    verify_amendment_integrity,
)


@pytest.fixture
def test_db_session():
    """Fixture providing an in-memory SQLite database session for unit tests."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_normalize_roles_and_validate_access():
    """Test role normalization and RBAC action permissions."""
    assert normalize_roles("LEGAL_MANAGER, stakeholder") == {"legal_manager", "stakeholder"}
    assert normalize_roles(["Admin ", "Executive"]) == {"admin", "executive"}
    assert normalize_roles(None) == set()

    # Valid read authorization
    assert validate_contract_access("stakeholder", "read") == {"stakeholder"}
    assert validate_contract_access("legal_manager", "write") == {"legal_manager"}
    assert validate_contract_access("admin", "delete") == {"admin"}
    assert validate_contract_access("auditor", "audit") == {"auditor"}

    # Unauthorized access checks
    with pytest.raises(ContractPermissionDeniedError):
        validate_contract_access(None, "read")

    with pytest.raises(ContractPermissionDeniedError):
        validate_contract_access("stakeholder", "write")

    with pytest.raises(ContractPermissionDeniedError):
        validate_contract_access("stakeholder", "delete")

    with pytest.raises(ContractPermissionDeniedError):
        validate_contract_access("unknown_role", "read")


def test_create_contract_success(test_db_session: Session):
    """Test successful creation of a contract with nested clauses and SLA terms."""
    today = datetime.now(timezone.utc).date()
    expiry = today + timedelta(days=180)

    payload = {
        "contract_number": "CNT-2026-TEST1",
        "title": "Cloud Hosting Agreement",
        "counterparty": "CloudCorp LLC",
        "status": "ACTIVE",
        "effective_on": today.isoformat(),
        "expires_on": expiry,
        "auto_renew": True,
        "clauses": [
            {
                "title": "Data Protection Clause",
                "clause_type": "CONFIDENTIALITY",
                "content": "All customer data must be encrypted at rest and in transit.",
            }
        ],
        "sla_commitments": [
            {
                "metric_name": "Service Availability",
                "target_value": "99.95%",
                "penalty_terms": "5% credit per 0.1% downtime",
            }
        ],
    }

    contract = create_contract(test_db_session, payload, roles="legal_manager")

    assert contract.id is not None
    assert contract.contract_number == "CNT-2026-TEST1"
    assert contract.title == "Cloud Hosting Agreement"
    assert contract.status == ContractStatus.ACTIVE
    assert contract.auto_renew is True
    assert len(contract.clauses) == 1
    assert contract.clauses[0].title == "Data Protection Clause"
    assert contract.clauses[0].clause_type == ClauseType.CONFIDENTIALITY
    assert len(contract.sla_commitments) == 1
    assert contract.sla_commitments[0].metric_name == "Service Availability"


def test_create_contract_validation_failures(test_db_session: Session):
    """Test validation errors for missing fields, duplicate contract numbers, and invalid dates."""
    today = datetime.now(timezone.utc).date()

    # Missing mandatory fields
    with pytest.raises(ContractValidationError):
        create_contract(test_db_session, {"title": "Test"}, roles="legal_manager")

    # Missing dates
    with pytest.raises(ContractValidationError):
        create_contract(
            test_db_session,
            {
                "contract_number": "C1",
                "title": "Title",
                "counterparty": "Party",
            },
            roles="legal_manager",
        )

    # Valid contract creation
    valid_payload = {
        "contract_number": "CNT-DUP-01",
        "title": "Primary Agreement",
        "counterparty": "Vendor A",
        "effective_on": today,
        "expires_on": today + timedelta(days=30),
    }
    create_contract(test_db_session, valid_payload, roles="legal_manager")

    # Duplicate contract number error
    with pytest.raises(ContractValidationError) as exc_info:
        create_contract(test_db_session, valid_payload, roles="legal_manager")
    assert "already exists" in str(exc_info.value)

    # Invalid status enum string
    invalid_status_payload = dict(valid_payload)
    invalid_status_payload["contract_number"] = "CNT-DUP-02"
    invalid_status_payload["status"] = "INVALID_STATUS"
    with pytest.raises(ContractValidationError):
        create_contract(test_db_session, invalid_status_payload, roles="legal_manager")


def test_get_contract_detail(test_db_session: Session):
    """Test detail loading for a created contract."""
    today = datetime.now(timezone.utc).date()
    created = create_contract(
        test_db_session,
        {
            "contract_number": "CNT-DETAIL-01",
            "title": "Vendor License Agreement",
            "counterparty": "Vendor Solutions",
            "effective_on": today,
            "expires_on": today + timedelta(days=60),
            "clauses": [
                {
                    "title": "Governing Law",
                    "clause_type": "GOVERNING_LAW",
                    "content": "Laws of Delaware",
                }
            ],
        },
        roles="admin",
    )

    detail = get_contract_detail(test_db_session, created.id, roles="stakeholder")
    assert detail.id == created.id
    assert detail.contract_number == "CNT-DETAIL-01"
    assert len(detail.clauses) == 1
    assert detail.clauses[0].title == "Governing Law"

    # Non-existent ID
    with pytest.raises(ContractNotFoundError):
        get_contract_detail(test_db_session, "non-existent-uuid", roles="stakeholder")


def test_list_contracts_filtering(test_db_session: Session):
    """Test querying and filtering contracts by status, counterparty, keyword, and timeframe."""
    ref_date = date_type(2026, 11, 1)

    c1 = create_contract(
        test_db_session,
        {
            "contract_number": "CNT-101",
            "title": "Alpha Master Service Agreement",
            "counterparty": "Acme Dynamics",
            "status": "ACTIVE",
            "effective_on": ref_date,
            "expires_on": ref_date + timedelta(days=20),  # Expires in 20 days
        },
        roles="legal_manager",
    )

    c2 = create_contract(
        test_db_session,
        {
            "contract_number": "CNT-102",
            "title": "Beta Software Subscription",
            "counterparty": "BetaTech Solutions",
            "status": "DRAFT",
            "effective_on": ref_date,
            "expires_on": ref_date + timedelta(days=50),  # Expires in 50 days
        },
        roles="legal_manager",
    )

    c3 = create_contract(
        test_db_session,
        {
            "contract_number": "CNT-103",
            "title": "Gamma Maintenance Contract",
            "counterparty": "Acme Dynamics",
            "status": "ACTIVE",
            "effective_on": ref_date,
            "expires_on": ref_date + timedelta(days=120),  # Expires in 120 days
        },
        roles="legal_manager",
    )

    # List all
    all_contracts = list_contracts(test_db_session, roles="stakeholder")
    assert len(all_contracts) == 3

    # Filter by status ACTIVE
    active_contracts = list_contracts(test_db_session, status="ACTIVE", roles="stakeholder")
    assert len(active_contracts) == 2
    assert {c.contract_number for c in active_contracts} == {"CNT-101", "CNT-103"}

    # Filter by counterparty "Acme"
    acme_contracts = list_contracts(test_db_session, counterparty="Acme", roles="stakeholder")
    assert len(acme_contracts) == 2
    assert {c.contract_number for c in acme_contracts} == {"CNT-101", "CNT-103"}

    # Filter by keyword "Software"
    software_contracts = list_contracts(test_db_session, keyword="Software", roles="stakeholder")
    assert len(software_contracts) == 1
    assert software_contracts[0].contract_number == "CNT-102"

    # Filter by renewal timeframe <= 30 days
    renewal_30 = list_contracts(
        test_db_session,
        renewal_days=30,
        current_date=ref_date,
        roles="stakeholder",
    )
    assert len(renewal_30) == 1
    assert renewal_30[0].contract_number == "CNT-101"

    # Filter by renewal timeframe <= 60 days
    renewal_60 = list_contracts(
        test_db_session,
        renewal_days=60,
        current_date=ref_date,
        roles="stakeholder",
    )
    assert len(renewal_60) == 2
    assert {c.contract_number for c in renewal_60} == {"CNT-101", "CNT-102"}

    # Negative renewal_days error
    with pytest.raises(ContractValidationError):
        list_contracts(test_db_session, renewal_days=-5, roles="stakeholder")


def test_update_and_delete_contract(test_db_session: Session):
    """Test contract modification and deletion with RBAC enforcement."""
    today = datetime.now(timezone.utc).date()
    created = create_contract(
        test_db_session,
        {
            "contract_number": "CNT-UPD-01",
            "title": "Initial Title",
            "counterparty": "Target Corp",
            "effective_on": today,
            "expires_on": today + timedelta(days=90),
        },
        roles="legal_manager",
    )

    # Update contract
    updated = update_contract(
        test_db_session,
        created.id,
        {"title": "Updated Title", "status": "TERMINATED"},
        roles="legal_manager",
    )
    assert updated.title == "Updated Title"
    assert updated.status == ContractStatus.TERMINATED

    # Delete contract with stakeholder role -> Permission Denied
    with pytest.raises(ContractPermissionDeniedError):
        delete_contract(test_db_session, created.id, roles="stakeholder")

    # Delete contract with legal_manager role -> Success
    delete_contract(test_db_session, created.id, roles="legal_manager")

    # Verify deleted
    with pytest.raises(ContractNotFoundError):
        get_contract_detail(test_db_session, created.id, roles="admin")


def test_record_amendment_success_and_hmac_integrity(test_db_session: Session):
    """Test recording contract amendments with SHA-256 HMAC integrity hash generation."""
    today = datetime.now(timezone.utc).date()
    created = create_contract(
        test_db_session,
        {
            "contract_number": "CNT-AMD-01",
            "title": "Master Services Agreement",
            "counterparty": "Apex Corp",
            "effective_on": today,
            "expires_on": today + timedelta(days=365),
        },
        roles="legal_manager",
    )

    # Record first amendment
    log1 = record_amendment(
        test_db_session,
        contract_id=created.id,
        summary="Added SLA credit terms for monthly uptime",
        user_id="user_legal_01",
        roles="legal_manager",
    )

    assert log1.id is not None
    assert log1.contract_id == created.id
    assert log1.amendment_number == 1
    assert log1.summary == "Added SLA credit terms for monthly uptime"
    assert log1.user_id == "user_legal_01"
    assert len(log1.integrity_hash) == 64
    assert verify_amendment_integrity(log1) is True

    # Record second amendment - sequential index check
    log2 = record_amendment(
        test_db_session,
        contract_id=created.id,
        summary="Updated termination notice window from 30 to 60 days",
        user_id="user_admin_02",
        roles="admin",
    )

    assert log2.amendment_number == 2
    assert log2.contract_id == created.id
    assert verify_amendment_integrity(log2) is True

    # Retrieve history
    history = get_amendment_history(test_db_session, created.id, roles="auditor")
    assert len(history) == 2
    assert history[0].amendment_number == 1
    assert history[1].amendment_number == 2


def test_record_amendment_tamper_detection(test_db_session: Session):
    """Test detection of tampered amendment audit records using HMAC integrity verification."""
    today = datetime.now(timezone.utc).date()
    created = create_contract(
        test_db_session,
        {
            "contract_number": "CNT-AMD-02",
            "title": "Vendor Supply Contract",
            "counterparty": "Global Logistics",
            "effective_on": today,
            "expires_on": today + timedelta(days=180),
        },
        roles="legal_manager",
    )

    log = record_amendment(
        test_db_session,
        contract_id=created.id,
        summary="Original amendment text summary",
        user_id="user_auditor",
        roles="legal_manager",
    )

    assert verify_amendment_integrity(log) is True

    # Simulate database record tampering (modifying summary text directly)
    db_record = (
        test_db_session.query(AmendmentAuditLog)
        .filter(AmendmentAuditLog.id == log.id)
        .first()
    )
    db_record.summary = "TAMPERED SUMMARY TEXT"
    test_db_session.commit()

    # Verification fails on tampered log
    assert verify_amendment_integrity(db_record) is False

    # Fetching history raises AmendmentIntegrityError
    with pytest.raises(AmendmentIntegrityError) as exc_info:
        get_amendment_history(test_db_session, created.id, roles="auditor")
    assert "tampering detected" in str(exc_info.value).lower()


def test_record_amendment_authorization_and_validation(test_db_session: Session):
    """Test RBAC role requirements and parameter validation for contract amendments."""
    today = datetime.now(timezone.utc).date()
    created = create_contract(
        test_db_session,
        {
            "contract_number": "CNT-AMD-03",
            "title": "Software License Agreement",
            "counterparty": "Tech Vendor",
            "effective_on": today,
            "expires_on": today + timedelta(days=90),
        },
        roles="legal_manager",
    )

    # Stakeholder cannot record amendment (write action)
    with pytest.raises(ContractPermissionDeniedError):
        record_amendment(
            test_db_session,
            contract_id=created.id,
            summary="Attempted change",
            user_id="user_stakeholder",
            roles="stakeholder",
        )

    # Missing summary
    with pytest.raises(ContractValidationError):
        record_amendment(
            test_db_session,
            contract_id=created.id,
            summary="   ",
            user_id="user_legal",
            roles="legal_manager",
        )

    # Non-existent contract ID
    with pytest.raises(ContractNotFoundError):
        record_amendment(
            test_db_session,
            contract_id="non-existent-contract-uuid",
            summary="Valid summary",
            user_id="user_legal",
            roles="legal_manager",
        )
