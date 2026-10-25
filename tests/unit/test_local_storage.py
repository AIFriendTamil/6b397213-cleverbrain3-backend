"""Unit tests for LocalStorageConnector and DocumentStoragePort."""

import hashlib
import os
import shutil
import tempfile
import pytest

from src.connectors.storage.local_storage import LocalStorageConnector, LocalStorage
from src.services.clm.ports import (
    DocumentStoragePort,
    StorageIntegrityError,
    StorageNotFoundError,
)


@pytest.fixture
def temp_storage_dir():
    """Fixture providing a temporary directory for local storage tests."""
    temp_dir = tempfile.mkdtemp(prefix="test_clm_storage_")
    yield temp_dir
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


@pytest.fixture
def encrypted_connector(temp_storage_dir):
    """Fixture providing a LocalStorageConnector with encryption enabled."""
    return LocalStorageConnector(
        storage_path=temp_storage_dir,
        encryption_key="test-secret-key-32b-length-str",
        encryption_enabled=True,
    )


@pytest.fixture
def unencrypted_connector(temp_storage_dir):
    """Fixture providing a LocalStorageConnector with encryption disabled."""
    return LocalStorageConnector(
        storage_path=temp_storage_dir,
        encryption_key="test-secret-key-32b-length-str",
        encryption_enabled=False,
    )


def test_implements_document_storage_port(encrypted_connector):
    """Verify that LocalStorageConnector inherits from and implements DocumentStoragePort."""
    assert isinstance(encrypted_connector, DocumentStoragePort)
    assert isinstance(LocalStorage(storage_path="/tmp"), DocumentStoragePort)


def test_compute_hash(encrypted_connector):
    """Verify SHA-256 hash computation for bytes."""
    sample_data = b"Hello, Contract Lifecycle Management!"
    expected_hash = hashlib.sha256(sample_data).hexdigest()

    computed = encrypted_connector.compute_hash(sample_data)
    assert computed == expected_hash
    assert encrypted_connector.calculate_hash(sample_data) == expected_hash


def test_store_and_retrieve_encrypted_document(encrypted_connector, temp_storage_dir):
    """Verify storing an encrypted document and retrieving it successfully."""
    contract_id = "cnt-uuid-100"
    version_number = 1
    filename = "master_agreement.pdf"
    content = b"PDF document payload contents for master agreement."

    result = encrypted_connector.store_document(
        contract_id=contract_id,
        version_number=version_number,
        filename=filename,
        content=content,
    )

    file_path = result["file_path"]
    file_hash = result["file_hash"]
    file_size = result["file_size_bytes"]

    assert os.path.exists(file_path)
    assert file_hash == hashlib.sha256(content).hexdigest()
    assert file_size == len(content)

    # Verify that data stored on disk is encrypted (not raw bytes)
    with open(file_path, "rb") as f:
        disk_bytes = f.read()
    assert disk_bytes != content

    # Retrieve and decrypt
    retrieved = encrypted_connector.retrieve_document(file_path, expected_hash=file_hash)
    assert retrieved == content


def test_store_and_retrieve_unencrypted_document(unencrypted_connector):
    """Verify storing and retrieving document when encryption is disabled."""
    contract_id = "cnt-uuid-200"
    version_number = 2
    filename = "terms.docx"
    content = b"Unencrypted document bytes."

    result = unencrypted_connector.store_document(
        contract_id=contract_id,
        version_number=version_number,
        filename=filename,
        content=content,
    )

    file_path = result["file_path"]
    with open(file_path, "rb") as f:
        disk_bytes = f.read()
    assert disk_bytes == content

    retrieved = unencrypted_connector.retrieve_document(file_path, expected_hash=result["file_hash"])
    assert retrieved == content


def test_integrity_verification_success_and_failure(encrypted_connector):
    """Verify SHA-256 integrity checks pass for valid files and fail on mismatches."""
    contract_id = "cnt-uuid-300"
    content = b"Integrity check test content."

    result = encrypted_connector.store_document(
        contract_id=contract_id,
        version_number=1,
        filename="clause.pdf",
        content=content,
    )

    file_path = result["file_path"]
    correct_hash = result["file_hash"]
    wrong_hash = "0" * 64

    # Verify integrity method
    assert encrypted_connector.verify_integrity(file_path, correct_hash) is True
    assert encrypted_connector.verify_integrity(file_path, wrong_hash) is False

    # Retrieve with wrong hash should raise StorageIntegrityError
    with pytest.raises(StorageIntegrityError):
        encrypted_connector.retrieve_document(file_path, expected_hash=wrong_hash)


def test_retrieve_nonexistent_file_raises_not_found(encrypted_connector):
    """Verify StorageNotFoundError when attempting to retrieve missing file."""
    with pytest.raises(StorageNotFoundError):
        encrypted_connector.retrieve_document("/path/to/nonexistent/file.pdf")


def test_delete_document(encrypted_connector):
    """Verify document deletion behavior."""
    result = encrypted_connector.store_document(
        contract_id="cnt-uuid-400",
        version_number=1,
        filename="to_delete.pdf",
        content=b"Delete me",
    )

    file_path = result["file_path"]
    assert os.path.exists(file_path)

    # Delete
    deleted = encrypted_connector.delete_document(file_path)
    assert deleted is True
    assert not os.path.exists(file_path)

    # Deleting again returns False
    assert encrypted_connector.delete_document(file_path) is False


def test_alias_methods(encrypted_connector):
    """Verify that alias methods work as expected."""
    contract_id = "cnt-uuid-500"
    content = b"Alias methods test payload."

    res1 = encrypted_connector.save_document(contract_id, 1, "a.pdf", content)
    res2 = encrypted_connector.save_file(contract_id, 2, "b.pdf", content)

    path1 = res1["file_path"]
    path2 = res2["file_path"]

    assert encrypted_connector.get_document(path1) == content
    assert encrypted_connector.get_file(path2) == content
    assert encrypted_connector.read_file(path1) == content

    assert encrypted_connector.delete_file(path1) is True
    assert encrypted_connector.delete_file(path2) is True


def test_relative_path_resolution(encrypted_connector):
    """Verify that relative file paths resolve relative to storage_path."""
    contract_id = "cnt-uuid-600"
    content = b"Relative path test payload."

    res = encrypted_connector.store_document(contract_id, 1, "relative.pdf", content)
    abs_path = res["file_path"]

    # Construct relative path from storage_path
    rel_path = os.path.relpath(abs_path, encrypted_connector.storage_path)

    # Retrieve using relative path
    retrieved = encrypted_connector.retrieve_document(rel_path)
    assert retrieved == content
