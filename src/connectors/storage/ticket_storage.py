"""File attachment storage adapter implementing local storage and validation checks for support tickets."""

import hashlib
import logging
import os
from pathlib import Path
import uuid
from typing import Any, Dict, List, Optional, Union

from src.lib.config import settings
from src.services.escalation.storage_port import (
    AttachmentNotFoundError,
    AttachmentStorageError,
    AttachmentStoragePort,
    AttachmentValidationError,
)

logger = logging.getLogger(__name__)


def validate_attachment(
    filename: str,
    content: bytes,
    max_size_bytes: Optional[int] = None,
    allowed_extensions: Optional[Union[List[str], str]] = None,
) -> None:
    """Validate attachment filename, file size, and extension.

    Raises AttachmentValidationError if validation fails.
    """
    if not filename or not isinstance(filename, str) or not filename.strip():
        raise AttachmentValidationError("Filename cannot be empty.")

    # Sanitize filename to prevent directory traversal
    sanitized_name = Path(filename).name
    if not sanitized_name or sanitized_name in (".", ".."):
        raise AttachmentValidationError("Invalid filename specified.")

    # Check for empty content
    if content is None or len(content) == 0:
        raise AttachmentValidationError("File attachment cannot be empty.")

    # Validate file size
    max_bytes = (
        max_size_bytes
        if max_size_bytes is not None
        else settings.TICKET_ATTACHMENT_MAX_SIZE_BYTES
    )
    if len(content) > max_bytes:
        raise AttachmentValidationError(
            f"File size ({len(content)} bytes) exceeds maximum allowed limit of {max_bytes} bytes."
        )

    # Validate file extension
    ext_list: List[str] = []
    configured_exts = (
        allowed_extensions
        if allowed_extensions is not None
        else settings.TICKET_ATTACHMENT_ALLOWED_EXTENSIONS
    )

    if isinstance(configured_exts, str):
        ext_list = [e.strip().lower() for e in configured_exts.split(",") if e.strip()]
    elif isinstance(configured_exts, list):
        ext_list = [e.strip().lower() for e in configured_exts if isinstance(e, str)]

    # Ensure leading dots on allowed extensions
    ext_list = [e if e.startswith(".") else f".{e}" for e in ext_list]

    file_ext = Path(sanitized_name).suffix.lower()
    if not file_ext or file_ext not in ext_list:
        allowed_str = ", ".join(ext_list)
        raise AttachmentValidationError(
            f"File extension '{file_ext}' is not supported. Allowed extensions: {allowed_str}"
        )


class AttachmentStorageAdapter(AttachmentStoragePort):
    """File attachment storage adapter implementing local filesystem storage for support tickets."""

    def __init__(
        self,
        storage_path: Optional[str] = None,
        max_size_bytes: Optional[int] = None,
        allowed_extensions: Optional[Union[List[str], str]] = None,
    ) -> None:
        self.storage_path = storage_path or settings.TICKET_ATTACHMENT_STORAGE_PATH
        self.max_size_bytes = (
            max_size_bytes
            if max_size_bytes is not None
            else settings.TICKET_ATTACHMENT_MAX_SIZE_BYTES
        )
        self.allowed_extensions = (
            allowed_extensions
            if allowed_extensions is not None
            else settings.TICKET_ATTACHMENT_ALLOWED_EXTENSIONS
        )

        try:
            os.makedirs(self.storage_path, exist_ok=True)
        except Exception as e:
            logger.error(
                f"Failed to create attachment storage directory '{self.storage_path}': {e}"
            )
            raise AttachmentStorageError(f"Could not initialize storage path: {e}") from e

    def compute_hash(self, content: bytes) -> str:
        """Compute SHA-256 hex digest for content bytes."""
        return hashlib.sha256(content).hexdigest()

    def validate_attachment(
        self,
        filename: str,
        content: bytes,
        max_size_bytes: Optional[int] = None,
        allowed_extensions: Optional[Union[List[str], str]] = None,
    ) -> None:
        """Validate attachment filename, size, and extension."""
        validate_attachment(
            filename=filename,
            content=content,
            max_size_bytes=max_size_bytes or self.max_size_bytes,
            allowed_extensions=allowed_extensions or self.allowed_extensions,
        )

    def save_attachment(
        self,
        filename: str,
        content: bytes,
        ticket_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate and persist file attachment bytes to storage."""
        self.validate_attachment(filename, content)

        sanitized_filename = Path(filename).name
        unique_id = uuid.uuid4().hex[:12]
        stored_filename = f"{unique_id}_{sanitized_filename}"

        if ticket_id:
            target_dir = os.path.join(self.storage_path, ticket_id)
        else:
            target_dir = self.storage_path

        try:
            os.makedirs(target_dir, exist_ok=True)
            file_path = os.path.abspath(os.path.join(target_dir, stored_filename))
            with open(file_path, "wb") as f:
                f.write(content)
        except Exception as e:
            logger.error(f"Failed to write attachment to '{target_dir}': {e}")
            raise AttachmentStorageError(f"Failed to write attachment file: {e}") from e

        file_hash = self.compute_hash(content)
        file_size = len(content)

        if ticket_id:
            attachment_url = f"/api/v1/tickets/{ticket_id}/attachments/{stored_filename}"
        else:
            attachment_url = f"/api/v1/tickets/attachments/{stored_filename}"

        logger.info(
            f"Successfully stored attachment '{filename}' as '{stored_filename}' ({file_size} bytes, hash={file_hash})"
        )

        return {
            "filename": sanitized_filename,
            "stored_filename": stored_filename,
            "file_path": file_path,
            "file_size_bytes": file_size,
            "file_hash": file_hash,
            "attachment_url": attachment_url,
        }

    def get_attachment(self, file_path_or_id: str) -> bytes:
        """Retrieve stored file attachment content bytes."""
        if not file_path_or_id:
            raise AttachmentNotFoundError("Attachment path or identifier cannot be empty.")

        if os.path.isabs(file_path_or_id):
            target_path = file_path_or_id
        else:
            target_path = os.path.abspath(os.path.join(self.storage_path, file_path_or_id))

        if not os.path.exists(target_path) or not os.path.isfile(target_path):
            raise AttachmentNotFoundError(f"Attachment file not found at '{file_path_or_id}'.")

        try:
            with open(target_path, "rb") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to read attachment from '{target_path}': {e}")
            raise AttachmentStorageError(f"Failed to read attachment file: {e}") from e

    def delete_attachment(self, file_path_or_id: str) -> bool:
        """Delete stored file attachment."""
        if not file_path_or_id:
            return False

        if os.path.isabs(file_path_or_id):
            target_path = file_path_or_id
        else:
            target_path = os.path.abspath(os.path.join(self.storage_path, file_path_or_id))

        if not os.path.exists(target_path):
            return False

        try:
            os.remove(target_path)
            logger.info(f"Deleted attachment file at '{target_path}'.")
            return True
        except Exception as e:
            logger.error(f"Failed to delete attachment at '{target_path}': {e}")
            raise AttachmentStorageError(f"Failed to delete attachment file: {e}") from e

    def exists(self, file_path_or_id: str) -> bool:
        """Check if file attachment exists in storage."""
        if not file_path_or_id:
            return False

        if os.path.isabs(file_path_or_id):
            target_path = file_path_or_id
        else:
            target_path = os.path.abspath(os.path.join(self.storage_path, file_path_or_id))

        return os.path.exists(target_path) and os.path.isfile(target_path)


# Alias for alternative naming compatibility
FileAttachmentStorageAdapter = AttachmentStorageAdapter

__all__ = [
    "AttachmentStorageAdapter",
    "FileAttachmentStorageAdapter",
    "validate_attachment",
]
