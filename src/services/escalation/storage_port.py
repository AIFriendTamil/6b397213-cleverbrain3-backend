"""Port interface and exception definitions for file attachment storage."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AttachmentValidationError(ValueError):
    """Raised when an attachment fails file size, extension, or format validation."""

    pass


class AttachmentNotFoundError(FileNotFoundError):
    """Raised when a requested attachment cannot be found in storage."""

    pass


class AttachmentStorageError(RuntimeError):
    """Raised when an error occurs during attachment persistence or retrieval."""

    pass


class AttachmentStoragePort(ABC):
    """Abstract port interface for support ticket file attachment storage."""

    @abstractmethod
    def validate_attachment(
        self,
        filename: str,
        content: bytes,
        max_size_bytes: Optional[int] = None,
        allowed_extensions: Optional[List[str]] = None,
    ) -> None:
        """Validate attachment filename, size, and extension."""
        pass

    @abstractmethod
    def save_attachment(
        self,
        filename: str,
        content: bytes,
        ticket_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate and persist file attachment bytes to storage.

        Returns metadata dict containing filename, stored_filename, file_path,
        file_size_bytes, file_hash, and attachment_url.
        """
        pass

    @abstractmethod
    def get_attachment(self, file_path_or_id: str) -> bytes:
        """Retrieve stored file attachment content bytes."""
        pass

    @abstractmethod
    def delete_attachment(self, file_path_or_id: str) -> bool:
        """Delete stored file attachment."""
        pass

    @abstractmethod
    def exists(self, file_path_or_id: str) -> bool:
        """Check if file attachment exists in storage."""
        pass

    @abstractmethod
    def compute_hash(self, content: bytes) -> str:
        """Compute SHA-256 hex digest for content bytes."""
        pass
