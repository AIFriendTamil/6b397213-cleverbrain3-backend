"""Unit tests for MockESignProvider and ESignaturePort."""

import json
import pytest

from src.connectors.esignature.mock_esign_provider import (
    MockESignProvider,
    MockESignatureProvider,
)
from src.services.clm.ports import (
    ESignaturePort,
    ESignatureRequestNotFoundError,
)


@pytest.fixture
def esign_provider():
    """Fixture providing a MockESignProvider instance."""
    return MockESignProvider(webhook_secret="test-webhook-secret-key")


def test_implements_esignature_port(esign_provider):
    """Verify that MockESignProvider implements ESignaturePort."""
    assert isinstance(esign_provider, ESignaturePort)
    assert MockESignProvider is MockESignatureProvider


def test_create_signature_request(esign_provider):
    """Test creating a mock signature request."""
    doc_content = b"Sample Contract Document Content"
    req = esign_provider.create_signature_request(
        contract_id="contract-123",
        document_content=doc_content,
        filename="agreement.pdf",
        recipient_email="signer@example.com",
        recipient_name="John Doe",
        subject="Please Sign Agreement",
    )

    assert req["contract_id"] == "contract-123"
    assert req["execution_status"] == "PENDING"
    assert req["recipient_email"] == "signer@example.com"
    assert req["recipient_name"] == "John Doe"
    assert req["filename"] == "agreement.pdf"
    assert req["subject"] == "Please Sign Agreement"
    assert req["provider_ref_id"].startswith("mock_esig_")
    assert "created_at" in req
    assert "status_updated_at" in req


def test_get_signature_status(esign_provider):
    """Test retrieving signature request status."""
    req = esign_provider.create_signature_request(
        contract_id="contract-456",
        document_content=b"Test Content",
        filename="test.pdf",
        recipient_email="alice@example.com",
        recipient_name="Alice Smith",
    )
    provider_ref_id = req["provider_ref_id"]

    status_data = esign_provider.get_signature_status(provider_ref_id)
    assert status_data["provider_ref_id"] == provider_ref_id
    assert status_data["execution_status"] == "PENDING"
    assert status_data["contract_id"] == "contract-456"

    with pytest.raises(ESignatureRequestNotFoundError):
        esign_provider.get_signature_status("invalid_provider_ref_id")


def test_cancel_signature_request(esign_provider):
    """Test canceling / voiding an active signature request."""
    req = esign_provider.create_signature_request(
        contract_id="contract-789",
        document_content=b"Cancel test content",
        filename="cancel.pdf",
        recipient_email="bob@example.com",
        recipient_name="Bob Jones",
    )
    provider_ref_id = req["provider_ref_id"]

    canceled = esign_provider.cancel_signature_request(provider_ref_id)
    assert canceled["execution_status"] == "VOIDED"

    updated_status = esign_provider.get_signature_status(provider_ref_id)
    assert updated_status["execution_status"] == "VOIDED"

    with pytest.raises(ESignatureRequestNotFoundError):
        esign_provider.cancel_signature_request("unknown_ref_id")


def test_verify_webhook_signature(esign_provider):
    """Test HMAC webhook signature verification."""
    payload_bytes, sig_header = esign_provider.generate_webhook_payload(
        provider_ref_id="mock_esig_001",
        execution_status="SIGNED",
    )

    assert esign_provider.verify_webhook_signature(payload_bytes, sig_header) is True
    assert (
        esign_provider.verify_webhook_signature(
            payload_bytes, sig_header, secret="test-webhook-secret-key"
        )
        is True
    )

    # Test invalid signature
    assert (
        esign_provider.verify_webhook_signature(payload_bytes, "sha256=invalid_sig")
        is False
    )
    assert (
        esign_provider.verify_webhook_signature(
            payload_bytes, sig_header, secret="wrong-secret"
        )
        is False
    )
    assert esign_provider.verify_webhook_signature(payload_bytes, "") is False


def test_parse_webhook_event(esign_provider):
    """Test parsing raw bytes and dict webhook payloads."""
    payload_bytes, _ = esign_provider.generate_webhook_payload(
        provider_ref_id="mock_esig_002",
        execution_status="SIGNED",
    )

    parsed = esign_provider.parse_webhook_event(payload_bytes)
    assert parsed["provider_ref_id"] == "mock_esig_002"
    assert parsed["execution_status"] == "SIGNED"
    assert "event_timestamp" in parsed

    # Test dict payload with DECLINED status
    dict_payload = {
        "provider_ref_id": "mock_esig_003",
        "status": "declined",
        "recipient_email": "charlie@example.com",
    }
    parsed_dict = esign_provider.parse_webhook_event(dict_payload)
    assert parsed_dict["provider_ref_id"] == "mock_esig_003"
    assert parsed_dict["execution_status"] == "DECLINED"
    assert parsed_dict["recipient_email"] == "charlie@example.com"


def test_simulate_signer_action(esign_provider):
    """Test simulating signer actions."""
    req = esign_provider.create_signature_request(
        contract_id="contract-sim",
        document_content=b"Simulation Content",
        filename="sim.pdf",
        recipient_email="sim@example.com",
        recipient_name="Sim User",
    )
    provider_ref_id = req["provider_ref_id"]

    sim_res = esign_provider.simulate_signer_action(provider_ref_id, "SIGNED")
    assert sim_res["execution_status"] == "SIGNED"

    updated = esign_provider.get_signature_status(provider_ref_id)
    assert updated["execution_status"] == "SIGNED"

    with pytest.raises(ESignatureRequestNotFoundError):
        esign_provider.simulate_signer_action("non_existent_ref", "SIGNED")
