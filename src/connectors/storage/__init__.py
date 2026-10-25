"""Storage connectors package."""

from src.connectors.storage.local_storage import LocalStorage, LocalStorageConnector
from src.connectors.storage.ticket_storage import (
    AttachmentStorageAdapter,
    FileAttachmentStorageAdapter,
    validate_attachment,
)
from src.services.escalation.storage_port import (
    AttachmentNotFoundError,
    AttachmentStorageError,
    AttachmentStoragePort,
    AttachmentValidationError,
)

__all__ = [
    "AttachmentStorageAdapter",
    "FileAttachmentStorageAdapter",
    "validate_attachment",
    "AttachmentValidationError",
    "AttachmentNotFoundError",
    "AttachmentStorageError",
    "AttachmentStoragePort",
    "LocalStorageConnector",
    "LocalStorage",
]
