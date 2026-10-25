"""Unit tests for AttachmentStorageAdapter and validation checks in src/connectors/storage.py."""

import hashlib
import os
import shutil
import tempfile
import pytest

from src.connectors.storage import (
    AttachmentStorageAdapter,
    FileAttachmentStorageAdapter,
    validate_attachment,
)
from src.services.escalation.storage_port import (
    AttachmentNotFoundError,
    AttachmentStoragePort,
    AttachmentValidationError,
)


@pytest.fixture
def temp_storage_dir():
    """Fixture providing a temporary directory for attachment storage tests."""
    temp_dir = tempfile.mkdtemp(prefix="test_ticket_storage_")
    yield temp_dir
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


@pytest.fixture
def storage_adapter(temp_storage_dir):
    """Fixture providing an AttachmentStorageAdapter instance with temp directory."""
    return AttachmentStorageAdapter(
        storage_path=temp_storage_dir,
        max_size_bytes=1024 * 1024,  # 1 MB for testing
        allowed_extensions=[".pdf", ".docx", ".png", ".jpg", ".jpeg", ".txt", ".csv"],
    )


def test_implements_attachment_storage_port(storage_adapter):
    """Verify that AttachmentStorageAdapter inherits from AttachmentStoragePort."""
    assert isinstance(storage_adapter, AttachmentStoragePort)
    assert FileAttachmentStorageAdapter is AttachmentStorageAdapter


def test_compute_hash(storage_adapter):
    """Verify SHA-256 hash computation."""
    content = b"Sample ticket attachment contents."
    expected_hash = hashlib.sha256(content).hexdigest()
    assert storage_adapter.compute_hash(content) == expected_hash


def test_validate_attachment_success():
    """Verify valid attachments pass validation without error."""
    validate_attachment("report.pdf", b"PDF bytes", max_size_bytes=1024, allowed_extensions=[".pdf"])
    validate_attachment("IMAGE.PNG", b"PNG bytes", max_size_bytes=1024, allowed_extensions=[".png"])
    validate_attachment("data.csv", b"CSV bytes", max_size_bytes=1024, allowed_extensions=["csv"])


def test_validate_attachment_empty_filename():
    """Verify empty or missing filename raises AttachmentValidationError."""
    with pytest.raises(AttachmentValidationError, match="Filename cannot be empty"):
        validate_attachment("", b"content")

    with pytest.raises(AttachmentValidationError, match="Filename cannot be empty"):
        validate_attachment("   ", b"content")


def test_validate_attachment_empty_content():
    """Verify 0-byte content raises AttachmentValidationError."""
    with pytest.raises(AttachmentValidationError, match="File attachment cannot be empty"):
        validate_attachment("test.pdf", b"")


def test_validate_attachment_exceeds_max_size():
    """Verify content exceeding max_size_bytes raises AttachmentValidationError."""
    large_content = b"x" * 2048
    with pytest.raises(AttachmentValidationError, match="exceeds maximum allowed limit"):
        validate_attachment("test.pdf", large_content, max_size_bytes=1024)


def test_validate_attachment_unsupported_extension():
    """Verify unsupported file extensions raise AttachmentValidationError."""
    with pytest.raises(AttachmentValidationError, match="is not supported"):
        validate_attachment("script.sh", b"echo hello", allowed_extensions=[".pdf", ".txt"])

    with pytest.raises(AttachmentValidationError, match="is not supported"):
        validate_attachment("noextension", b"some data", allowed_extensions=[".pdf", ".txt"])


def test_save_and_get_attachment(storage_adapter, temp_storage_dir):
    """Verify saving an attachment and retrieving its content."""
    filename = "log.txt"
    content = b"Error log data for ticket investigation."

    result = storage_adapter.save_attachment(filename, content)

    assert result["filename"] == "log.txt"
    assert os.path.exists(result["file_path"])
    assert result["file_size_bytes"] == len(content)
    assert result["file_hash"] == hashlib.sha256(content).hexdigest()
    assert "/api/v1/tickets/attachments/" in result["attachment_url"]

    # Retrieve stored content
    retrieved = storage_adapter.get_attachment(result["file_path"])
    assert retrieved == content

    # Retrieve using stored_filename relative path
    retrieved_rel = storage_adapter.get_attachment(result["stored_filename"])
    assert retrieved_rel == content


def test_save_attachment_with_ticket_id(storage_adapter):
    """Verify saving attachment scoped under ticket_id directory."""
    ticket_id = "tkt-12345-uuid"
    filename = "screenshot.png"
    content = b"PNG image data bytes."

    result = storage_adapter.save_attachment(filename, content, ticket_id=ticket_id)

    assert ticket_id in result["file_path"]
    assert f"/api/v1/tickets/{ticket_id}/attachments/" in result["attachment_url"]
    assert storage_adapter.exists(result["file_path"])


def test_get_attachment_not_found(storage_adapter):
    """Verify get_attachment raises AttachmentNotFoundError for non-existent files."""
    with pytest.raises(AttachmentNotFoundError):
        storage_adapter.get_attachment("non_existent_file.pdf")

    with pytest.raises(AttachmentNotFoundError):
        storage_adapter.get_attachment("")


def test_delete_attachment(storage_adapter):
    """Verify deleting stored attachment."""
    result = storage_adapter.save_attachment("doc.pdf", b"PDF file data")
    file_path = result["file_path"]

    assert storage_adapter.exists(file_path) is True
    deleted = storage_adapter.delete_attachment(file_path)
    assert deleted is True
    assert storage_adapter.exists(file_path) is False

    # Deleting non-existent file returns False
    assert storage_adapter.delete_attachment("non_existent.pdf") is False
    assert storage_adapter.delete_attachment("") is False
