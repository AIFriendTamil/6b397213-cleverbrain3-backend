"""Unit tests for Document Service (backend/src/services/clm/document_service.py)."""

import hashlib
import os
import shutil
import tempfile
from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session

from src.connectors.storage.local_storage import LocalStorageConnector
from src.models.base import Base, create_db_engine
from src.models.contract import Contract, ContractStatus, DocumentVersion
from src.services.clm.document_service import (
    DocumentNotFoundError,
    DocumentPermissionDeniedError,
    DocumentStorageError,
    DocumentValidationError,
    compute_file_hash,
    delete_document_version,
    download_document_version,
    get_document_version,
    list_document_versions,
    normalize_roles,
    upload_document_version,
    validate_document_access,
    validate_file_upload,
)


@pytest.fixture
def temp_storage_dir():
    """Fixture providing a temporary directory for local storage tests."""
    temp_dir = tempfile.mkdtemp(prefix="test_clm_doc_service_")
    yield temp_dir
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


@pytest.fixture
def test_db_session():
    """Fixture providing an in-memory SQLite database session for unit tests."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def sample_contract(test_db_session: Session) -> Contract:
    """Fixture providing a sample persisted contract entity."""
    today = datetime.now(timezone.utc).date()
    contract = Contract(
        contract_number="CNT-DOC-TEST-001",
        title="Software License Agreement",
        counterparty="TechVendor Inc",
        status=ContractStatus.ACTIVE,
        effective_on=today,
        expires_on=today,
        auto_renew=False,
    )
    test_db_session.add(contract)
    test_db_session.commit()
    test_db_session.refresh(contract)
    return contract


def test_normalize_roles_and_validate_access():
    """Verify role string/list normalization and action permission checks."""
    assert normalize_roles("LEGAL_MANAGER, stakeholder") == {"legal_manager", "stakeholder"}
    assert normalize_roles(["Admin ", "Auditor"]) == {"admin", "auditor"}
    assert normalize_roles(None) == set()

    # Valid read/write/delete access
    assert validate_document_access("stakeholder", action="read") == {"stakeholder"}
    assert validate_document_access("legal_manager", action="write") == {"legal_manager"}
    assert validate_document_access("admin", action="delete") == {"admin"}

    # Unauthorized attempts
    with pytest.raises(DocumentPermissionDeniedError):
        validate_document_access(None, action="read")

    with pytest.raises(DocumentPermissionDeniedError):
        validate_document_access("stakeholder", action="write")

    with pytest.raises(DocumentPermissionDeniedError):
        validate_document_access("stakeholder", action="delete")


def test_file_validation_and_hash():
    """Verify file upload validation, allowed extensions, size limits, and SHA-256 checks."""
    content = b"PDF document payload for testing SHA-256 and file size validation."
    expected_hash = hashlib.sha256(content).hexdigest()

    assert compute_file_hash(content) == expected_hash

    # Valid validation
    calculated = validate_file_upload(
        filename="master_agreement.pdf",
        content=content,
        expected_hash=expected_hash,
        allowed_extensions=[".pdf", ".docx"],
    )
    assert calculated == expected_hash

    # Invalid extension
    with pytest.raises(DocumentValidationError):
        validate_file_upload(
            filename="script.exe",
            content=content,
            allowed_extensions=[".pdf", ".docx"],
        )

    # Exceeds max file size limit
    with pytest.raises(DocumentValidationError):
        validate_file_upload(
            filename="large.pdf",
            content=content,
            max_file_size_bytes=10,
        )

    # Hash mismatch
    with pytest.raises(DocumentValidationError):
        validate_file_upload(
            filename="test.pdf",
            content=content,
            expected_hash="0000000000000000000000000000000000000000000000000000000000000000",
        )


def test_upload_document_version_and_version_increment(
    test_db_session: Session, sample_contract: Contract, temp_storage_dir: str
):
    """Verify document upload, SHA-256 calculation, and version incrementing."""
    storage = LocalStorageConnector(storage_path=temp_storage_dir, encryption_enabled=True)

    content_v1 = b"Version 1 document payload content."
    doc_v1 = upload_document_version(
        db=test_db_session,
        contract_id=sample_contract.id,
        filename="agreement.pdf",
        content=content_v1,
        uploaded_by_user_id="usr-legal-01",
        storage_port=storage,
        roles="legal_manager",
    )

    assert doc_v1.version_number == 1
    assert doc_v1.file_hash == hashlib.sha256(content_v1).hexdigest()
    assert doc_v1.uploaded_by_user_id == "usr-legal-01"

    # Upload Version 2
    content_v2 = b"Version 2 updated document payload content with new terms."
    doc_v2 = upload_document_version(
        db=test_db_session,
        contract_id=sample_contract.id,
        filename="agreement.pdf",
        content=content_v2,
        uploaded_by_user_id="usr-legal-02",
        storage_port=storage,
        roles="admin",
    )

    assert doc_v2.version_number == 2
    assert doc_v2.file_hash == hashlib.sha256(content_v2).hexdigest()

    # Confirm database contains both historical revisions
    versions = list_document_versions(
        db=test_db_session,
        contract_id=sample_contract.id,
        roles="stakeholder",
    )
    assert len(versions) == 2
    assert versions[0].version_number == 2
    assert versions[1].version_number == 1


def test_upload_document_contract_not_found(test_db_session: Session):
    """Verify uploading a document for a non-existent contract raises DocumentNotFoundError."""
    with pytest.raises(DocumentNotFoundError):
        upload_document_version(
            db=test_db_session,
            contract_id="non-existent-contract-uuid",
            filename="doc.pdf",
            content=b"Some content",
            uploaded_by_user_id="user-1",
            roles="admin",
        )


def test_get_and_download_document_version(
    test_db_session: Session, sample_contract: Contract, temp_storage_dir: str
):
    """Verify retrieving document metadata and downloading decrypted bytes."""
    storage = LocalStorageConnector(storage_path=temp_storage_dir, encryption_enabled=True)
    content = b"Content for download verification test."

    doc_version = upload_document_version(
        db=test_db_session,
        contract_id=sample_contract.id,
        filename="clause_terms.docx",
        content=content,
        uploaded_by_user_id="usr-001",
        storage_port=storage,
        roles="legal_manager",
    )

    # Get version by number
    fetched_by_num = get_document_version(
        db=test_db_session,
        contract_id=sample_contract.id,
        version_id_or_number=1,
        roles="auditor",
    )
    assert fetched_by_num.id == doc_version.id

    # Get version by string ID
    fetched_by_id = get_document_version(
        db=test_db_session,
        contract_id=sample_contract.id,
        version_id_or_number=doc_version.id,
        roles="auditor",
    )
    assert fetched_by_id.version_number == 1

    # Download content
    download_res = download_document_version(
        db=test_db_session,
        contract_id=sample_contract.id,
        version_id_or_number=1,
        storage_port=storage,
        roles="executive",
    )

    assert download_res["content"] == content
    assert download_res["file_hash"] == hashlib.sha256(content).hexdigest()
    assert download_res["file_size_bytes"] == len(content)


def test_delete_document_version(
    test_db_session: Session, sample_contract: Contract, temp_storage_dir: str
):
    """Verify deleting a specific document version and file cleanup."""
    storage = LocalStorageConnector(storage_path=temp_storage_dir, encryption_enabled=False)

    doc_v1 = upload_document_version(
        db=test_db_session,
        contract_id=sample_contract.id,
        filename="v1.pdf",
        content=b"Document v1 payload",
        uploaded_by_user_id="usr-01",
        storage_port=storage,
        roles="admin",
    )

    # Delete v1
    success = delete_document_version(
        db=test_db_session,
        contract_id=sample_contract.id,
        version_id_or_number=1,
        storage_port=storage,
        roles="admin",
    )
    assert success is True

    # Confirm it's removed from DB
    with pytest.raises(DocumentNotFoundError):
        get_document_version(
            db=test_db_session,
            contract_id=sample_contract.id,
            version_id_or_number=1,
            roles="admin",
        )
