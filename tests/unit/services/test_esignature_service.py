"""Unit tests for E-Signature Execution Tracking Hub and Webhook Service (esignature_service.py)."""

from datetime import date as date_type, datetime, timezone
import pytest
from sqlalchemy.orm import Session

from src.connectors.esignature.mock_esign_provider import MockESignProvider
from src.models.base import Base, create_db_engine
from src.models.contract import Contract, ContractStatus, ESignatureExecutionStatus, ESignatureRequest
from src.services.clm.esignature_service import (
    ESignatureNotFoundError,
    ESignaturePermissionDeniedError,
    ESignatureService,
    ESignatureValidationError,
    ESignatureWebhookError,
    cancel_signature_request,
    create_signature_request,
    get_signature_request,
    list_signature_requests,
    normalize_roles,
    parse_datetime_utc,
    process_webhook_event,
    sync_signature_status,
    validate_esignature_access,
)


@pytest.fixture
def test_db_session():
    """Fixture providing an in-memory SQLite database session for e-signature service tests."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def sample_contract(test_db_session):
    """Fixture providing a saved Contract record in DB."""
    contract = Contract(
        id="contract-uuid-101",
        contract_number="CNT-2026-8888",
        title="Software License Agreement",
        counterparty="Acme Tech Solutions",
        status=ContractStatus.ACTIVE,
        effective_on=date_type(2026, 1, 1),
        expires_on=date_type(2026, 12, 31),
        auto_renew=False,
    )
    test_db_session.add(contract)
    test_db_session.commit()
    test_db_session.refresh(contract)
    return contract


def test_normalize_roles_and_access_validation():
    """Test RBAC role normalization and access validation rules."""
    assert normalize_roles("ADMIN, legal_manager") == {"admin", "legal_manager"}
    assert normalize_roles(None) == set()

    # Valid roles
    assert validate_esignature_access("stakeholder", "read") == {"stakeholder"}
    assert validate_esignature_access("legal_manager", "write") == {"legal_manager"}
    assert validate_esignature_access("admin", "delete") == {"admin"}
    assert validate_esignature_access("worker", "execute") == {"worker"}

    # Unauthorized roles
    with pytest.raises(ESignaturePermissionDeniedError):
        validate_esignature_access("unknown_role", "read")

    with pytest.raises(ESignaturePermissionDeniedError):
        validate_esignature_access("stakeholder", "write")

    with pytest.raises(ValueError):
        validate_esignature_access("admin", "invalid_action")


def test_parse_datetime_utc():
    """Test parsing various datetime representations into UTC timezone-aware objects."""
    now = datetime.now(timezone.utc)
    parsed_now = parse_datetime_utc(now)
    assert parsed_now.tzinfo == timezone.utc

    # ISO string with 'Z'
    dt_str = "2026-10-25T14:30:00Z"
    parsed_str = parse_datetime_utc(dt_str)
    assert parsed_str.year == 2026
    assert parsed_str.tzinfo == timezone.utc

    # ISO string with offset
    dt_offset = "2026-10-25T14:30:00+00:00"
    parsed_offset = parse_datetime_utc(dt_offset)
    assert parsed_offset.year == 2026

    # Naive datetime gets UTC assigned
    naive_dt = datetime(2026, 10, 25, 12, 0, 0)
    parsed_naive = parse_datetime_utc(naive_dt)
    assert parsed_naive.tzinfo == timezone.utc

    # Timestamp float
    ts = 1761400000.0
    parsed_ts = parse_datetime_utc(ts)
    assert parsed_ts.tzinfo == timezone.utc


def test_create_signature_request_success(test_db_session, sample_contract):
    """Test creating an e-signature request record successfully."""
    esig_req = create_signature_request(
        db=test_db_session,
        contract_id=sample_contract.id,
        recipient_email="jane.doe@example.com",
        recipient_name="Jane Doe",
        subject="Please Sign License Agreement",
        roles=["legal_manager"],
        correlation_id="trace-create-001",
    )

    assert esig_req.id is not None
    assert esig_req.contract_id == sample_contract.id
    assert esig_req.recipient_email == "jane.doe@example.com"
    assert esig_req.recipient_name == "Jane Doe"
    assert esig_req.execution_status == ESignatureExecutionStatus.PENDING
    assert esig_req.provider_ref_id.startswith("mock_esig_")

    # Verify stored in DB
    stored = test_db_session.query(ESignatureRequest).filter_by(id=esig_req.id).first()
    assert stored is not None
    assert stored.provider_ref_id == esig_req.provider_ref_id


def test_create_signature_request_validations(test_db_session, sample_contract):
    """Test parameter validations for create_signature_request."""
    # Contract not found
    with pytest.raises(ESignatureNotFoundError):
        create_signature_request(
            db=test_db_session,
            contract_id="nonexistent-contract-id",
            recipient_email="user@example.com",
            recipient_name="User",
            roles=["legal_manager"],
        )

    # Empty recipient email or invalid email format
    with pytest.raises(ESignatureValidationError):
        create_signature_request(
            db=test_db_session,
            contract_id=sample_contract.id,
            recipient_email="invalid_email_format",
            recipient_name="User",
            roles=["legal_manager"],
        )

    # Empty recipient name
    with pytest.raises(ESignatureValidationError):
        create_signature_request(
            db=test_db_session,
            contract_id=sample_contract.id,
            recipient_email="user@example.com",
            recipient_name="",
            roles=["legal_manager"],
        )


def test_list_signature_requests_tracking_hub(test_db_session, sample_contract):
    """Test retrieving signature requests for the E-Signature Tracking Hub."""
    # Create two signature requests
    req1 = create_signature_request(
        db=test_db_session,
        contract_id=sample_contract.id,
        recipient_email="alice@example.com",
        recipient_name="Alice",
        roles=["legal_manager"],
    )

    req2 = create_signature_request(
        db=test_db_session,
        contract_id=sample_contract.id,
        recipient_email="bob@example.com",
        recipient_name="Bob",
        roles=["legal_manager"],
    )

    # List all requests for stakeholder
    all_reqs = list_signature_requests(test_db_session, roles=["stakeholder"])
    assert len(all_reqs) == 2

    # Filter by execution status
    pending_reqs = list_signature_requests(
        test_db_session,
        execution_status="PENDING",
        roles=["stakeholder"],
    )
    assert len(pending_reqs) == 2

    # Filter by recipient email
    alice_reqs = list_signature_requests(
        test_db_session,
        recipient_email="alice",
        roles=["stakeholder"],
    )
    assert len(alice_reqs) == 1
    assert alice_reqs[0].recipient_name == "Alice"

    # Filter by contract ID
    contract_reqs = list_signature_requests(
        test_db_session,
        contract_id=sample_contract.id,
        roles=["stakeholder"],
    )
    assert len(contract_reqs) == 2


def test_get_signature_request_by_id_and_provider_ref(test_db_session, sample_contract):
    """Test fetching a signature request by ID or provider_ref_id."""
    created_req = create_signature_request(
        db=test_db_session,
        contract_id=sample_contract.id,
        recipient_email="charlie@example.com",
        recipient_name="Charlie",
        roles=["legal_manager"],
    )

    # Fetch by internal UUID
    fetched_by_id = get_signature_request(
        test_db_session,
        request_id=created_req.id,
        roles=["stakeholder"],
    )
    assert fetched_by_id.id == created_req.id

    # Fetch by provider_ref_id
    fetched_by_ref = get_signature_request(
        test_db_session,
        request_id=created_req.provider_ref_id,
        roles=["stakeholder"],
    )
    assert fetched_by_ref.id == created_req.id

    # Fetch unknown ID
    with pytest.raises(ESignatureNotFoundError):
        get_signature_request(
            test_db_session,
            request_id="nonexistent-request-id",
            roles=["stakeholder"],
        )


def test_out_of_order_webhook_timestamp_handling(test_db_session, sample_contract):
    """Test handling out-of-order webhook timestamps (NFR-5 requirement)."""
    # Create request with initial status_updated_at set to 2026-10-25 12:00:00 UTC
    provider = MockESignProvider(webhook_secret="test-secret")
    esig_req = create_signature_request(
        db=test_db_session,
        contract_id=sample_contract.id,
        recipient_email="dave@example.com",
        recipient_name="Dave",
        esign_port=provider,
        roles=["legal_manager"],
    )

    provider_ref_id = esig_req.provider_ref_id

    # Set status_updated_at explicitly to 2026-10-25 12:00:00 UTC
    base_ts = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
    esig_req.status_updated_at = base_ts
    test_db_session.add(esig_req)
    test_db_session.commit()
    test_db_session.refresh(esig_req)

    # 1. Simulate an OLDER / STALE webhook payload (timestamp: 2026-10-25 11:00:00 UTC)
    stale_payload = {
        "provider_ref_id": provider_ref_id,
        "execution_status": "SIGNED",
        "event_timestamp": "2026-10-25T11:00:00Z",
    }

    result_stale = process_webhook_event(
        db=test_db_session,
        provider_name="mock",
        payload=stale_payload,
        esign_port=provider,
        correlation_id="trace-webhook-stale",
    )

    assert result_stale["status"] == "ignored"
    assert result_stale["reason"] == "stale_timestamp"

    # Verify DB record remains unchanged (PENDING, 12:00:00)
    db_record_after_stale = get_signature_request(
        test_db_session,
        request_id=provider_ref_id,
        roles=["stakeholder"],
    )
    assert db_record_after_stale.execution_status == ESignatureExecutionStatus.PENDING
    assert db_record_after_stale.status_updated_at == base_ts

    # 2. Simulate a NEWER webhook payload (timestamp: 2026-10-25 13:00:00 UTC)
    newer_payload = {
        "provider_ref_id": provider_ref_id,
        "execution_status": "SIGNED",
        "event_timestamp": "2026-10-25T13:00:00Z",
    }

    result_newer = process_webhook_event(
        db=test_db_session,
        provider_name="mock",
        payload=newer_payload,
        esign_port=provider,
        correlation_id="trace-webhook-newer",
    )

    assert result_newer["status"] == "updated"
    assert result_newer["execution_status"] == "SIGNED"

    # Verify DB record is updated to SIGNED and 13:00:00
    db_record_updated = get_signature_request(
        test_db_session,
        request_id=provider_ref_id,
        roles=["stakeholder"],
    )
    assert db_record_updated.execution_status == ESignatureExecutionStatus.SIGNED
    assert db_record_updated.status_updated_at == datetime(2026, 10, 25, 13, 0, 0, tzinfo=timezone.utc)


def test_webhook_signature_verification_failure(test_db_session, sample_contract):
    """Test webhook HMAC signature verification failure handling."""
    provider = MockESignProvider(webhook_secret="test-secret")
    esig_req = create_signature_request(
        db=test_db_session,
        contract_id=sample_contract.id,
        recipient_email="eve@example.com",
        recipient_name="Eve",
        esign_port=provider,
        roles=["legal_manager"],
    )

    payload_bytes = f'{{"provider_ref_id": "{esig_req.provider_ref_id}", "execution_status": "SIGNED"}}'.encode("utf-8")
    invalid_signature = "sha256=invalid_hmac_signature_header_value"

    with pytest.raises(ESignatureWebhookError):
        process_webhook_event(
            db=test_db_session,
            provider_name="mock",
            payload=payload_bytes,
            signature_header=invalid_signature,
            esign_port=provider,
        )


def test_cancel_signature_request(test_db_session, sample_contract):
    """Test canceling / voiding an active e-signature request."""
    esig_req = create_signature_request(
        db=test_db_session,
        contract_id=sample_contract.id,
        recipient_email="frank@example.com",
        recipient_name="Frank",
        roles=["legal_manager"],
    )

    canceled = cancel_signature_request(
        db=test_db_session,
        request_id=esig_req.id,
        roles=["legal_manager"],
    )

    assert canceled.execution_status == ESignatureExecutionStatus.VOIDED

    # Verify updated in DB
    db_record = get_signature_request(
        test_db_session,
        request_id=esig_req.id,
        roles=["stakeholder"],
    )
    assert db_record.execution_status == ESignatureExecutionStatus.VOIDED


def test_sync_signature_status(test_db_session, sample_contract):
    """Test synchronizing signature request status from provider."""
    provider = MockESignProvider(webhook_secret="test-secret")
    esig_req = create_signature_request(
        db=test_db_session,
        contract_id=sample_contract.id,
        recipient_email="grace@example.com",
        recipient_name="Grace",
        esign_port=provider,
        roles=["legal_manager"],
    )

    # Provider status is PENDING initially
    synced = sync_signature_status(
        db=test_db_session,
        request_id=esig_req.id,
        esign_port=provider,
        roles=["legal_manager"],
    )
    assert synced.execution_status == ESignatureExecutionStatus.PENDING


def test_esignature_service_class_wrapper(test_db_session, sample_contract):
    """Test ESignatureService class proxies to static methods."""
    req = ESignatureService.create_signature_request(
        db=test_db_session,
        contract_id=sample_contract.id,
        recipient_email="helen@example.com",
        recipient_name="Helen",
        roles=["legal_manager"],
    )

    hub_reqs = ESignatureService.list_signature_requests(
        db=test_db_session,
        roles=["stakeholder"],
    )
    assert len(hub_reqs) == 1
    assert hub_reqs[0].id == req.id
