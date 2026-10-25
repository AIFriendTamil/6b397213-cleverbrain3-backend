"""Local file system document storage connector with encryption and SHA-256 integrity verification."""

import hashlib
import hmac
import logging
import os
from typing import Any, Dict, Optional

from src.lib.config import settings
from src.services.clm.ports import (
    DocumentStoragePort,
    StorageIntegrityError,
    StorageNotFoundError,
)

logger = logging.getLogger(__name__)

AES_GCM_HEADER = b"ENC_AES256_GCM\n"
AES_CTR_HEADER = b"ENC_AES256_CTR\n"

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


class LocalStorageConnector(DocumentStoragePort):
    """Local file storage implementation supporting AES-256 encryption and SHA-256 integrity checking."""

    def __init__(
        self,
        storage_path: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_enabled: Optional[bool] = None,
        encryption_algorithm: Optional[str] = None,
    ) -> None:
        self.storage_path = storage_path or settings.CLM_STORAGE_PATH
        self.encryption_key = encryption_key or settings.CLM_STORAGE_ENCRYPTION_KEY
        self.encryption_enabled = (
            encryption_enabled
            if encryption_enabled is not None
            else settings.CLM_STORAGE_ENCRYPTION_ENABLED
        )
        self.encryption_algorithm = (
            encryption_algorithm or settings.CLM_STORAGE_ENCRYPTION_ALGORITHM
        )

        os.makedirs(self.storage_path, exist_ok=True)

    def _get_key_bytes(self) -> bytes:
        """Derive a 256-bit (32-byte) key from the configuration encryption key."""
        return hashlib.sha256(self.encryption_key.encode("utf-8")).digest()

    def _ctr_keystream(self, key: bytes, nonce: bytes, length: int) -> bytes:
        """Generate deterministic pseudo-random keystream bytes using HMAC-SHA256 counter mode."""
        stream = bytearray()
        counter = 0
        while len(stream) < length:
            block_counter = counter.to_bytes(8, "big")
            block = hmac.new(key, nonce + block_counter, hashlib.sha256).digest()
            stream.extend(block)
            counter += 1
        return bytes(stream[:length])

    def _encrypt(self, content: bytes) -> bytes:
        """Encrypt raw content bytes using AES-256 (GCM or CTR mode)."""
        raw_key = self._get_key_bytes()

        if HAS_CRYPTOGRAPHY:
            aesgcm = AESGCM(raw_key)
            nonce = os.urandom(12)
            ciphertext = aesgcm.encrypt(nonce, content, None)
            return AES_GCM_HEADER + nonce + ciphertext
        else:
            nonce = os.urandom(16)
            keystream = self._ctr_keystream(raw_key, nonce, len(content))
            ciphertext = bytes(a ^ b for a, b in zip(content, keystream))
            mac = hmac.new(raw_key, nonce + ciphertext, hashlib.sha256).digest()
            return AES_CTR_HEADER + nonce + mac + ciphertext

    def _decrypt(self, stored_bytes: bytes) -> bytes:
        """Decrypt stored payload bytes using header protocol."""
        raw_key = self._get_key_bytes()

        if stored_bytes.startswith(AES_GCM_HEADER):
            if not HAS_CRYPTOGRAPHY:
                raise RuntimeError(
                    "File is encrypted with AES-256-GCM but cryptography package is not installed."
                )
            header_len = len(AES_GCM_HEADER)
            nonce = stored_bytes[header_len : header_len + 12]
            ciphertext = stored_bytes[header_len + 12 :]
            aesgcm = AESGCM(raw_key)
            return aesgcm.decrypt(nonce, ciphertext, None)

        elif stored_bytes.startswith(AES_CTR_HEADER):
            header_len = len(AES_CTR_HEADER)
            nonce = stored_bytes[header_len : header_len + 16]
            mac = stored_bytes[header_len + 16 : header_len + 48]
            ciphertext = stored_bytes[header_len + 48 :]
            expected_mac = hmac.new(raw_key, nonce + ciphertext, hashlib.sha256).digest()
            if not hmac.compare_digest(mac, expected_mac):
                raise StorageIntegrityError("Encrypted payload MAC integrity check failed.")
            keystream = self._ctr_keystream(raw_key, nonce, len(ciphertext))
            return bytes(a ^ b for a, b in zip(ciphertext, keystream))

        else:
            return stored_bytes

    def _resolve_path(self, file_path: str) -> str:
        """Resolve full absolute file path from absolute or storage-relative path."""
        if os.isabs(file_path):
            return file_path
        return os.path.join(self.storage_path, file_path)

    def store_document(
        self,
        contract_id: str,
        version_number: int,
        filename: str,
        content: bytes,
    ) -> Dict[str, Any]:
        """Store a document version with optional encryption and return metadata."""
        if not isinstance(content, bytes):
            raise ValueError("Document content must be bytes.")

        file_hash = self.compute_hash(content)
        file_size_bytes = len(content)

        sanitized_filename = os.path.basename(filename)
        target_dir = os.path.join(self.storage_path, contract_id)
        os.makedirs(target_dir, exist_ok=True)

        target_file_name = f"v{version_number}_{sanitized_filename}"
        target_path = os.path.join(target_dir, target_file_name)

        if self.encryption_enabled:
            payload = self._encrypt(content)
        else:
            payload = content

        temp_file_path = f"{target_path}.tmp_{os.getpid()}"
        with open(temp_file_path, "wb") as f:
            f.write(payload)
        os.replace(temp_file_path, target_path)

        logger.info(
            f"Stored document for contract {contract_id} v{version_number} at {target_path} "
            f"(size={file_size_bytes} bytes, hash={file_hash})"
        )

        return {
            "file_path": target_path,
            "file_hash": file_hash,
            "file_size_bytes": file_size_bytes,
        }

    def save_document(
        self,
        contract_id: str,
        version_number: int,
        filename: str,
        content: bytes,
    ) -> Dict[str, Any]:
        """Alias for store_document."""
        return self.store_document(
            contract_id=contract_id,
            version_number=version_number,
            filename=filename,
            content=content,
        )

    def save_file(
        self,
        contract_id: str,
        version_number: int,
        filename: str,
        content: bytes,
    ) -> Dict[str, Any]:
        """Alias for store_document."""
        return self.store_document(
            contract_id=contract_id,
            version_number=version_number,
            filename=filename,
            content=content,
        )

    def retrieve_document(
        self,
        file_path: str,
        expected_hash: Optional[str] = None,
    ) -> bytes:
        """Retrieve and decrypt document content, verifying SHA-256 integrity if requested."""
        resolved_path = self._resolve_path(file_path)
        if not os.path.isfile(resolved_path):
            raise StorageNotFoundError(f"Document file not found at path: {file_path}")

        try:
            with open(resolved_path, "rb") as f:
                stored_bytes = f.read()
        except OSError as e:
            raise StorageNotFoundError(f"Failed to read file at {file_path}: {e}") from e

        if stored_bytes.startswith(AES_GCM_HEADER) or stored_bytes.startswith(AES_CTR_HEADER):
            content = self._decrypt(stored_bytes)
        elif self.encryption_enabled:
            try:
                content = self._decrypt(stored_bytes)
            except Exception:
                content = stored_bytes
        else:
            content = stored_bytes

        if expected_hash is not None:
            computed_hash = self.compute_hash(content)
            if computed_hash.lower() != expected_hash.lower():
                raise StorageIntegrityError(
                    f"SHA-256 integrity mismatch for {file_path}. Expected '{expected_hash}', got '{computed_hash}'."
                )

        return content

    def get_document(
        self,
        file_path: str,
        expected_hash: Optional[str] = None,
    ) -> bytes:
        """Alias for retrieve_document."""
        return self.retrieve_document(file_path=file_path, expected_hash=expected_hash)

    def get_file(
        self,
        file_path: str,
        expected_hash: Optional[str] = None,
    ) -> bytes:
        """Alias for retrieve_document."""
        return self.retrieve_document(file_path=file_path, expected_hash=expected_hash)

    def read_file(
        self,
        file_path: str,
        expected_hash: Optional[str] = None,
    ) -> bytes:
        """Alias for retrieve_document."""
        return self.retrieve_document(file_path=file_path, expected_hash=expected_hash)

    def delete_document(self, file_path: str) -> bool:
        """Delete stored document file."""
        resolved_path = self._resolve_path(file_path)
        if not os.path.isfile(resolved_path):
            return False
        try:
            os.remove(resolved_path)
            parent_dir = os.path.dirname(resolved_path)
            if os.path.exists(parent_dir) and not os.listdir(parent_dir):
                os.rmdir(parent_dir)
            return True
        except OSError as e:
            logger.warning(f"Failed to delete document at {file_path}: {e}")
            return False

    def delete_file(self, file_path: str) -> bool:
        """Alias for delete_document."""
        return self.delete_document(file_path=file_path)

    def verify_integrity(self, file_path: str, expected_hash: str) -> bool:
        """Verify that stored document content matches expected SHA-256 hash."""
        try:
            content = self.retrieve_document(file_path=file_path)
            computed_hash = self.compute_hash(content)
            return computed_hash.lower() == expected_hash.lower()
        except (StorageNotFoundError, StorageIntegrityError, Exception) as e:
            logger.warning(f"Integrity check failed for {file_path}: {e}")
            return False

    def compute_hash(self, content: bytes) -> str:
        """Compute SHA-256 hex digest for given content bytes."""
        if not isinstance(content, bytes):
            raise ValueError("Content must be bytes.")
        return hashlib.sha256(content).hexdigest()

    def calculate_hash(self, content: bytes) -> str:
        """Alias for compute_hash."""
        return self.compute_hash(content)


# Alias class name for backwards compatibility or alternative naming
LocalStorage = LocalStorageConnector
