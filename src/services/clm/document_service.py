"""Document management service for Contract Lifecycle Management (CLM).

Provides business logic for document versioning, file validation, SHA-256 hash checks,
document storage integration, listing historical versions, and downloading files.
"""

import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Union

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from src.connectors.storage.local_storage import LocalStorageConnector
from src.lib.config import settings
from src.models.contract import Contract, DocumentVersion
from src.services.clm.ports import (
    DocumentStoragePort,
    StorageIntegrityError,
    StorageNotFoundError,
)

logger = logging.getLogger(__name__)


class DocumentServiceError(Exception):
    """Base exception class for document service operations."""

    pass


class DocumentNotFoundError(DocumentServiceError):
    """Raised when a contract or document version is not found."""

    pass


class DocumentValidationError(DocumentServiceError):
    """Raised when document payload, size, extension, or hash validation fails."""

    pass


class DocumentPermissionDeniedError(DocumentServiceError):
    """Raised when user role authorization check fails for a document operation."""

    pass


class DocumentStorageError(DocumentServiceError):
    """Raised when document storage operation encounters an error."""

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


def validate_document_access(
    roles: Optional[Union[str, Iterable[str]]],
    action: str = "read",
) -> Set[str]:
    """Verify if user roles permit the requested document operation action."""
    user_roles = normalize_roles(roles)

    if not user_roles:
        logger.warning(f"Document access denied: missing user roles for action='{action}'")
        raise DocumentPermissionDeniedError("Authentication required: missing user roles.")

    allowed_map = {
        "read": READ_ROLES,
        "write": WRITE_ROLES,
        "delete": DELETE_ROLES,
    }

    permitted_roles = allowed_map.get(action.lower())
    if not permitted_roles:
        raise ValueError(f"Unsupported action: '{action}'")

    if not user_roles.intersection(permitted_roles):
        logger.warning(
            f"Role access denied: roles={user_roles} lack permission for action='{action}'"
        )
        raise DocumentPermissionDeniedError(
            f"User role lacks authorization for '{action}' operation."
        )

    return user_roles


def compute_file_hash(content: bytes) -> str:
    """Calculate hex SHA-256 hash for document content bytes."""
    if not isinstance(content, bytes):
        raise ValueError("Document content must be bytes.")
    return hashlib.sha256(content).hexdigest()


def validate_file_upload(
    filename: str,
    content: bytes,
    expected_hash: Optional[str] = None,
    max_file_size_bytes: Optional[int] = None,
    allowed_extensions: Optional[List[str]] = None,
) -> str:
    """Validate uploaded document filename, file size, extension, and content SHA-256 hash."""
    if not filename or not filename.strip():
        raise DocumentValidationError("Filename must be a non-empty string.")

    if not isinstance(content, bytes) or len(content) == 0:
        raise DocumentValidationError("Document payload content cannot be empty.")

    max_size = max_file_size_bytes or settings.CLM_MAX_FILE_SIZE_BYTES
    if len(content) > max_size:
        raise DocumentValidationError(
            f"File size ({len(content)} bytes) exceeds maximum limit of {max_size} bytes."
        )

    raw_allowed = allowed_extensions or settings.CLM_ALLOWED_EXTENSIONS
    if isinstance(raw_allowed, str):
        valid_exts = [e.strip().lower() for e in raw_allowed.split(",") if e.strip()]
    else:
        valid_exts = [e.lower() for e in raw_allowed]

    _, ext = os.path.splitext(filename)
    ext_lower = ext.lower()

    if valid_exts and ext_lower not in valid_exts:
        raise DocumentValidationError(
            f"File extension '{ext}' is not permitted. Allowed extensions: {valid_exts}"
        )

    computed_hash = compute_file_hash(content)

    if expected_hash:
        clean_expected = expected_hash.strip().lower()
        if computed_hash.lower() != clean_expected:
            raise DocumentValidationError(
                f"File SHA-256 integrity check failed. Computed '{computed_hash}', expected '{clean_expected}'."
            )

    return computed_hash


def upload_document_version(
    db: DBSession,
    contract_id: str,
    filename: str,
    content: bytes,
    uploaded_by_user_id: str,
    storage_port: Optional[DocumentStoragePort] = None,
    expected_hash: Optional[str] = None,
    roles: Optional[Union[str, Iterable[str]]] = None,
) -> DocumentVersion:
    """Upload a new document version for a contract, incrementing version_number."""
    validate_document_access(roles, action="write")

    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise DocumentNotFoundError(f"Contract not found with id: {contract_id}")

    file_hash = validate_file_upload(
        filename=filename,
        content=content,
        expected_hash=expected_hash,
    )

    max_version = (
        db.query(func.max(DocumentVersion.version_number))
        .filter(DocumentVersion.contract_id == contract_id)
        .scalar()
    )
    next_version_number = (max_version or 0) + 1

    port = storage_port or LocalStorageConnector()

    try:
        storage_result = port.store_document(
            contract_id=contract_id,
            version_number=next_version_number,
            filename=filename,
            content=content,
        )
    except Exception as exc:
        logger.error(f"Failed to store document in storage port: {exc}", exc_info=True)
        raise DocumentStorageError(f"Failed to save document file to storage: {exc}") from exc

    doc_version = DocumentVersion(
        id=str(uuid.uuid4()),
        contract_id=contract_id,
        version_number=next_version_number,
        file_path=storage_result["file_path"],
        file_hash=file_hash,
        file_size_bytes=storage_result.get("file_size_bytes", len(content)),
        uploaded_by_user_id=uploaded_by_user_id or "system",
        uploaded_at=datetime.now(timezone.utc),
    )

    db.add(doc_version)
    db.commit()
    db.refresh(doc_version)

    logger.info(
        f"Uploaded document version {next_version_number} for contract {contract_id} "
        f"(id={doc_version.id}, hash={file_hash})"
    )
    return doc_version


def list_document_versions(
    db: DBSession,
    contract_id: str,
    roles: Optional[Union[str, Iterable[str]]] = None,
) -> List[DocumentVersion]:
    """Retrieve all document versions for a given contract ordered by version number."""
    validate_document_access(roles, action="read")

    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise DocumentNotFoundError(f"Contract not found with id: {contract_id}")

    versions = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.contract_id == contract_id)
        .order_by(DocumentVersion.version_number.desc())
        .all()
    )
    return versions


def get_document_version(
    db: DBSession,
    contract_id: str,
    version_id_or_number: Union[str, int],
    roles: Optional[Union[str, Iterable[str]]] = None,
) -> DocumentVersion:
    """Get a specific document version by version ID or version number."""
    validate_document_access(roles, action="read")

    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise DocumentNotFoundError(f"Contract not found with id: {contract_id}")

    query = db.query(DocumentVersion).filter(DocumentVersion.contract_id == contract_id)

    if isinstance(version_id_or_number, int) or (
        isinstance(version_id_or_number, str) and version_id_or_number.isdigit()
    ):
        v_num = int(version_id_or_number)
        doc_version = query.filter(DocumentVersion.version_number == v_num).first()
    else:
        doc_version = query.filter(DocumentVersion.id == str(version_id_or_number)).first()

    if not doc_version:
        raise DocumentNotFoundError(
            f"Document version '{version_id_or_number}' not found for contract {contract_id}."
        )

    return doc_version


def download_document_version(
    db: DBSession,
    contract_id: str,
    version_id_or_number: Union[str, int],
    storage_port: Optional[DocumentStoragePort] = None,
    roles: Optional[Union[str, Iterable[str]]] = None,
) -> Dict[str, Any]:
    """Download and decrypt document content for a specific document version."""
    doc_version = get_document_version(
        db=db,
        contract_id=contract_id,
        version_id_or_number=version_id_or_number,
        roles=roles,
    )

    port = storage_port or LocalStorageConnector()

    try:
        content = port.retrieve_document(
            file_path=doc_version.file_path,
            expected_hash=doc_version.file_hash,
        )
    except StorageNotFoundError as exc:
        raise DocumentNotFoundError(f"Document file missing from storage: {exc}") from exc
    except StorageIntegrityError as exc:
        raise DocumentValidationError(f"Document SHA-256 integrity validation failed: {exc}") from exc
    except Exception as exc:
        raise DocumentStorageError(f"Failed to retrieve document content: {exc}") from exc

    return {
        "version": doc_version,
        "content": content,
        "filename": os.path.basename(doc_version.file_path),
        "file_hash": doc_version.file_hash,
        "file_size_bytes": doc_version.file_size_bytes,
    }


def delete_document_version(
    db: DBSession,
    contract_id: str,
    version_id_or_number: Union[str, int],
    storage_port: Optional[DocumentStoragePort] = None,
    roles: Optional[Union[str, Iterable[str]]] = None,
) -> bool:
    """Delete a specific document version record and its underlying storage file."""
    validate_document_access(roles, action="delete")

    doc_version = get_document_version(
        db=db,
        contract_id=contract_id,
        version_id_or_number=version_id_or_number,
        roles=roles,
    )

    port = storage_port or LocalStorageConnector()
    try:
        port.delete_document(doc_version.file_path)
    except Exception as exc:
        logger.warning(f"Error removing storage file {doc_version.file_path}: {exc}")

    db.delete(doc_version)
    db.commit()

    logger.info(
        f"Deleted document version {doc_version.version_number} "
        f"(id={doc_version.id}) for contract {contract_id}."
    )
    return True
