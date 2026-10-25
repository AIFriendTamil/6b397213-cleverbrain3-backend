"""Port interfaces for Contract Lifecycle Management (CLM) domain services."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class StorageIntegrityError(Exception):
    """Raised when SHA-256 integrity check fails for a stored document."""

    pass


class StorageNotFoundError(Exception):
    """Raised when a requested document is not found in storage."""

    pass


class ESignatureError(Exception):
    """Base exception for e-signature provider operations."""

    pass


class ESignatureRequestNotFoundError(ESignatureError):
    """Raised when an e-signature request is not found."""

    pass


class ESignatureWebhookVerificationError(ESignatureError):
    """Raised when e-signature webhook signature verification fails."""

    pass


class DocumentStoragePort(ABC):
    """Abstract port interface defining operations for contract document storage."""

    @abstractmethod
    def store_document(
        self,
        contract_id: str,
        version_number: int,
        filename: str,
        content: bytes,
    ) -> Dict[str, Any]:
        """Store a document file version with optional encryption.

        Args:
            contract_id: Contract unique identifier.
            version_number: Integer version number of document.
            filename: Original file name.
            content: Raw unencrypted file bytes.

        Returns:
            Dict containing metadata keys: 'file_path', 'file_hash', 'file_size_bytes'.
        """
        pass

    @abstractmethod
    def retrieve_document(
        self,
        file_path: str,
        expected_hash: Optional[str] = None,
    ) -> bytes:
        """Retrieve and decrypt document content, optionally verifying SHA-256 hash integrity.

        Args:
            file_path: Relative or absolute path/key of stored document.
            expected_hash: Optional SHA-256 hex string to verify integrity.

        Returns:
            Raw unencrypted file bytes.

        Raises:
            StorageNotFoundError: If file does not exist.
            StorageIntegrityError: If SHA-256 hash mismatch is detected.
        """
        pass

    @abstractmethod
    def delete_document(self, file_path: str) -> bool:
        """Delete a stored document file.

        Args:
            file_path: Relative or absolute path/key of stored document.

        Returns:
            True if deleted, False if not found or failed.
        """
        pass

    @abstractmethod
    def verify_integrity(self, file_path: str, expected_hash: str) -> bool:
        """Verify that stored document matches the expected SHA-256 hash.

        Args:
            file_path: Path/key of stored document.
            expected_hash: Expected SHA-256 hex hash string.

        Returns:
            True if valid, False if mismatch or file not found.
        """
        pass

    @abstractmethod
    def compute_hash(self, content: bytes) -> str:
        """Compute the SHA-256 hex hash of the given raw content bytes.

        Args:
            content: Raw file bytes.

        Returns:
            SHA-256 hex string (64 characters).
        """
        pass


class ESignaturePort(ABC):
    """Abstract port interface defining vendor-agnostic e-signature provider operations."""

    @abstractmethod
    def create_signature_request(
        self,
        contract_id: str,
        document_content: bytes,
        filename: str,
        recipient_email: str,
        recipient_name: str,
        subject: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create and dispatch an e-signature request to a recipient.

        Args:
            contract_id: Contract unique identifier.
            document_content: Raw bytes of the document to sign.
            filename: Name of the document file.
            recipient_email: Email address of recipient signer.
            recipient_name: Full name of recipient signer.
            subject: Optional subject line for signature request email.

        Returns:
            Dict containing signature request metadata:
                - provider_ref_id: Unique reference ID assigned by provider.
                - execution_status: Execution status string ('PENDING', 'SIGNED', etc.).
                - recipient_email: Recipient email address.
                - recipient_name: Recipient full name.
                - contract_id: Associated contract ID.
                - created_at: Timestamp when created.
                - status_updated_at: Timestamp when status was updated.
        """
        pass

    @abstractmethod
    def get_signature_status(self, provider_ref_id: str) -> Dict[str, Any]:
        """Fetch current status details for a signature request by provider reference ID.

        Args:
            provider_ref_id: Vendor-specific signature request identifier.

        Returns:
            Dict containing status details (provider_ref_id, execution_status, status_updated_at, etc.).

        Raises:
            ESignatureRequestNotFoundError: If provider_ref_id is not found.
        """
        pass

    @abstractmethod
    def cancel_signature_request(self, provider_ref_id: str) -> Dict[str, Any]:
        """Cancel or void an active signature request.

        Args:
            provider_ref_id: Vendor-specific signature request identifier.

        Returns:
            Dict containing updated status details with execution_status='VOIDED'.

        Raises:
            ESignatureRequestNotFoundError: If provider_ref_id is not found.
        """
        pass

    @abstractmethod
    def verify_webhook_signature(
        self,
        payload: bytes,
        signature_header: str,
        secret: Optional[str] = None,
    ) -> bool:
        """Verify the HMAC signature of an incoming webhook payload.

        Args:
            payload: Raw bytes of the incoming webhook body.
            signature_header: Signature header value from incoming request.
            secret: Optional HMAC key; defaults to configured webhook secret if None.

        Returns:
            True if valid signature, False otherwise.
        """
        pass

    @abstractmethod
    def parse_webhook_event(
        self,
        payload: Any,
    ) -> Dict[str, Any]:
        """Parse raw webhook body or dictionary into a normalized event structure.

        Args:
            payload: Raw bytes or parsed dictionary payload from provider.

        Returns:
            Dict containing normalized webhook fields:
                - provider_ref_id: Provider reference ID.
                - execution_status: Standardized status ('PENDING', 'SIGNED', 'DECLINED', 'VOIDED').
                - event_timestamp: ISO format string or datetime of event.
                - recipient_email: Recipient email if present.
                - event_type: Event type string.
        """
        pass
