"""Unit tests for Contract ORM entity models."""

import uuid
from datetime import date as date_type, datetime, timezone
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.contract import (
    AmendmentAuditLog,
    ClauseHighlight,
    ClauseType,
    Contract,
    ContractStatus,
    DocumentVersion,
    ESignatureExecutionStatus,
    ESignatureRequest,
    RenewalNotificationLog,
    RenewalWarningStage,
    SLACommitment,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_contract_creation_and_persistence(test_session: Session):
    """Verify Contract creation, default values, UUID generation, and retrieval."""
    today = datetime.now(timezone.utc).date()
    contract = Contract(
        contract_number="CNT-2026-001",
        title="Master Services Agreement",
        counterparty="Acme Corp",
        status=ContractStatus.ACTIVE,
        effective_on=today,
        expires_on=today,
        auto_renew=True,
    )
    test_session.add(contract)
    test_session.commit()

    retrieved = test_session.query(Contract).filter_by(contract_number="CNT-2026-001").first()
    assert retrieved is not None
    assert retrieved.id is not None
    assert str(uuid.UUID(retrieved.id)) == retrieved.id
    assert retrieved.title == "Master Services Agreement"
    assert retrieved.counterparty == "Acme Corp"
    assert retrieved.status == ContractStatus.ACTIVE
    assert retrieved.effective_on == today
    assert retrieved.expires_on == today
    assert retrieved.auto_renew is True
    assert isinstance(retrieved.created_at, datetime)
    assert isinstance(retrieved.updated_at, datetime)


def test_contract_relationships_and_cascades(test_session: Session):
    """Verify relationships and cascade delete behavior for all child entities."""
    today = datetime.now(timezone.utc).date()
    contract = Contract(
        contract_number="CNT-2026-002",
        title="SLA & Security Agreement",
        counterparty="Global Tech",
        status=ContractStatus.ACTIVE,
        effective_on=today,
        expires_on=today,
    )

    clause = ClauseHighlight(
        title="Confidentiality Terms",
        clause_type=ClauseType.CONFIDENTIALITY,
        content="Strict 5-year NDA term.",
    )
    sla = SLACommitment(
        metric_name="Uptime",
        target_value="99.9%",
        penalty_terms="10% service credit",
    )
    doc = DocumentVersion(
        version_number=1,
        file_path="/uploads/contracts/v1.pdf",
        file_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        file_size_bytes=1024,
        uploaded_by_user_id="user-123",
    )
    amendment = AmendmentAuditLog(
        amendment_number=1,
        summary="Scope expansion for region EU-West",
        user_id="user-123",
        integrity_hash="a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890",
    )
    esign = ESignatureRequest(
        provider_ref_id="docusign-ref-001",
        execution_status=ESignatureExecutionStatus.PENDING,
        recipient_email="legal@globaltech.com",
        recipient_name="Jane Doe",
    )
    renewal_log = RenewalNotificationLog(
        warning_stage=RenewalWarningStage.DAYS_90,
        recipient_target="legal@ourcompany.com",
    )

    contract.clauses.append(clause)
    contract.sla_commitments.append(sla)
    contract.document_versions.append(doc)
    contract.amendment_logs.append(amendment)
    contract.esignature_requests.append(esign)
    contract.renewal_notification_logs.append(renewal_log)

    test_session.add(contract)
    test_session.commit()

    retrieved = test_session.query(Contract).filter_by(contract_number="CNT-2026-002").first()
    assert len(retrieved.clauses) == 1
    assert retrieved.clauses[0].clause_type == ClauseType.CONFIDENTIALITY
    assert len(retrieved.sla_commitments) == 1
    assert retrieved.sla_commitments[0].metric_name == "Uptime"
    assert len(retrieved.document_versions) == 1
    assert retrieved.document_versions[0].version_number == 1
    assert len(retrieved.amendment_logs) == 1
    assert retrieved.amendment_logs[0].amendment_number == 1
    assert len(retrieved.esignature_requests) == 1
    assert retrieved.esignature_requests[0].execution_status == ESignatureExecutionStatus.PENDING
    assert len(retrieved.renewal_notification_logs) == 1
    assert retrieved.renewal_notification_logs[0].warning_stage == RenewalWarningStage.DAYS_90

    # Test cascade delete
    test_session.delete(retrieved)
    test_session.commit()

    assert test_session.query(ClauseHighlight).count() == 0
    assert test_session.query(SLACommitment).count() == 0
    assert test_session.query(DocumentVersion).count() == 0
    assert test_session.query(AmendmentAuditLog).count() == 0
    assert test_session.query(ESignatureRequest).count() == 0
    assert test_session.query(RenewalNotificationLog).count() == 0


def test_unique_constraints(test_session: Session):
    """Verify unique constraints on document versioning, amendment audit log, and renewal notification log."""
    today = datetime.now(timezone.utc).date()
    contract = Contract(
        contract_number="CNT-2026-003",
        title="Unique Constraint Test",
        counterparty="Beta LLC",
        status=ContractStatus.DRAFT,
        effective_on=today,
        expires_on=today,
    )
    test_session.add(contract)
    test_session.commit()

    # Document version duplicate constraint
    doc1 = DocumentVersion(
        contract_id=contract.id,
        version_number=1,
        file_path="/v1.pdf",
        file_hash="hash1",
        file_size_bytes=100,
        uploaded_by_user_id="user-1",
    )
    doc2 = DocumentVersion(
        contract_id=contract.id,
        version_number=1,
        file_path="/v1_dup.pdf",
        file_hash="hash2",
        file_size_bytes=100,
        uploaded_by_user_id="user-1",
    )
    test_session.add(doc1)
    test_session.commit()

    test_session.add(doc2)
    with pytest.raises(IntegrityError):
        test_session.commit()
    test_session.rollback()

    # Renewal notification log idempotency constraint
    renew1 = RenewalNotificationLog(
        contract_id=contract.id,
        warning_stage=RenewalWarningStage.DAYS_60,
        dispatched_at_date=today,
        recipient_target="target@company.com",
    )
    renew2 = RenewalNotificationLog(
        contract_id=contract.id,
        warning_stage=RenewalWarningStage.DAYS_60,
        dispatched_at_date=today,
        recipient_target="target@company.com",
    )
    test_session.add(renew1)
    test_session.commit()

    test_session.add(renew2)
    with pytest.raises(IntegrityError):
        test_session.commit()
    test_session.rollback()
