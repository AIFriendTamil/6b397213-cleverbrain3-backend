"""Integration tests for GET /api/v1/contracts and POST /api/v1/contracts API endpoints."""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.deps import get_current_session
from src.api.main import app
from src.models.base import Base, create_db_engine, get_db
from src.models.contract import AmendmentAuditLog
from src.models.session import Session as SessionModel
from src.models.tenant import Tenant as TenantModel
from src.models.user import User as UserModel


@pytest.fixture
def test_db():
    """Fixture providing an isolated in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with DBSession(engine) as session:
        yield session


@pytest.fixture
def test_session_data(test_db):
    """Fixture creating tenant, user, and session records, returning session model and token string."""
    tenant = TenantModel(name="CLM Test Tenant")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="legal@example.com",
        password_hash="secret_hash",
        full_name="Legal Manager User",
        is_active=True,
    )
    test_db.add(user)
    test_db.flush()

    session_record = SessionModel(
        user_id=user.id,
        tenant_id=tenant.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    test_db.add(session_record)
    test_db.commit()
    test_db.refresh(session_record)
    return session_record


@pytest.fixture
def client(test_db):
    """Fixture overriding get_db dependency and returning a FastAPI TestClient instance."""
    def _get_db_override():
        yield test_db

    app.dependency_overrides[get_db] = _get_db_override
    client_instance = TestClient(app)
    yield client_instance
    app.dependency_overrides.clear()


def test_search_contracts_unauthenticated(client):
    """Verify that unauthenticated requests to GET /api/v1/contracts return 401 Unauthorized."""
    response = client.get("/api/v1/contracts")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert data["detail"]["code"] == "UNAUTHORIZED"


def test_search_contracts_empty(client, test_session_data):
    """Verify that authenticated requests to empty repository return an empty list."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    response = client.get("/api/v1/contracts", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


def test_create_contract_success(client, test_session_data):
    """Verify successful contract creation via POST /api/v1/contracts with valid payload and write role."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    expiry_str = (datetime.now(timezone.utc) + timedelta(days=180)).strftime("%Y-%m-%d")

    payload = {
        "contract_number": "CNT-API-2026-001",
        "title": "Enterprise Cloud License Agreement",
        "counterparty": "Global Tech Innovations",
        "status": "ACTIVE",
        "effective_on": today_str,
        "expires_on": expiry_str,
        "auto_renew": True,
        "clauses": [
            {
                "title": "Data Sovereignty",
                "clause_type": "CONFIDENTIALITY",
                "content": "All data shall remain in regional data centers.",
            }
        ],
        "sla_commitments": [
            {
                "metric_name": "API Availability",
                "target_value": "99.9%",
                "penalty_terms": "10% service credit for failure",
            }
        ],
    }

    response = client.post("/api/v1/contracts", json=payload, headers=headers)
    assert response.status_code == status.HTTP_201_CREATED

    data = response.json()
    assert data["contract_number"] == "CNT-API-2026-001"
    assert data["title"] == "Enterprise Cloud License Agreement"
    assert data["counterparty"] == "Global Tech Innovations"
    assert data["status"] == "ACTIVE"
    assert data["auto_renew"] is True
    assert len(data["clauses"]) == 1
    assert data["clauses"][0]["title"] == "Data Sovereignty"
    assert len(data["sla_commitments"]) == 1
    assert data["sla_commitments"][0]["metric_name"] == "API Availability"


def test_create_contract_unauthorized_role(client, test_session_data):
    """Verify that users with read-only roles (e.g. stakeholder) cannot create contracts (HTTP 403)."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "stakeholder"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    payload = {
        "contract_number": "CNT-API-2026-002",
        "title": "Unauthorized Draft Agreement",
        "counterparty": "Vendor Corp",
        "status": "DRAFT",
        "effective_on": today_str,
        "expires_on": today_str,
    }

    response = client.post("/api/v1/contracts", json=payload, headers=headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    data = response.json()
    assert data["detail"]["code"] == "FORBIDDEN"


def test_create_contract_duplicate_number(client, test_session_data):
    """Verify that creating a contract with duplicate contract_number returns 400 Bad Request."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    payload = {
        "contract_number": "CNT-DUP-001",
        "title": "First Creation",
        "counterparty": "Vendor A",
        "effective_on": today_str,
        "expires_on": today_str,
    }

    resp1 = client.post("/api/v1/contracts", json=payload, headers=headers)
    assert resp1.status_code == status.HTTP_201_CREATED

    resp2 = client.post("/api/v1/contracts", json=payload, headers=headers)
    assert resp2.status_code == status.HTTP_400_BAD_REQUEST
    data = resp2.json()
    assert data["detail"]["code"] == "BAD_REQUEST"


def test_search_contracts_filtering(client, test_session_data):
    """Verify contract search filtering by status, counterparty, keyword, and renewal timeframe."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today = datetime.now(timezone.utc).date()

    # Create 2 contracts
    c1_payload = {
        "contract_number": "CNT-FILTER-1",
        "title": "Software Maintenance Agreement",
        "counterparty": "SoftWorks Inc",
        "status": "ACTIVE",
        "effective_on": today.strftime("%Y-%m-%d"),
        "expires_on": (today + timedelta(days=30)).strftime("%Y-%m-%d"),
    }
    c2_payload = {
        "contract_number": "CNT-FILTER-2",
        "title": "Hardware Supply Contract",
        "counterparty": "HardGear Co",
        "status": "DRAFT",
        "effective_on": today.strftime("%Y-%m-%d"),
        "expires_on": (today + timedelta(days=120)).strftime("%Y-%m-%d"),
    }

    client.post("/api/v1/contracts", json=c1_payload, headers=headers)
    client.post("/api/v1/contracts", json=c2_payload, headers=headers)

    # Filter by status = ACTIVE
    r_status = client.get("/api/v1/contracts?status=ACTIVE", headers=headers)
    assert r_status.status_code == status.HTTP_200_OK
    data_status = r_status.json()
    assert len(data_status) == 1
    assert data_status[0]["contract_number"] == "CNT-FILTER-1"

    # Filter by counterparty = SoftWorks
    r_cp = client.get("/api/v1/contracts?counterparty=SoftWorks", headers=headers)
    assert r_cp.status_code == status.HTTP_200_OK
    data_cp = r_cp.json()
    assert len(data_cp) == 1
    assert data_cp[0]["contract_number"] == "CNT-FILTER-1"

    # Filter by keyword = Hardware
    r_kw = client.get("/api/v1/contracts?keyword=Hardware", headers=headers)
    assert r_kw.status_code == status.HTTP_200_OK
    data_kw = r_kw.json()
    assert len(data_kw) == 1
    assert data_kw[0]["contract_number"] == "CNT-FILTER-2"

    # Filter by renewal_days = 40 (should match CNT-FILTER-1 expiring in 30 days, but not CNT-FILTER-2 expiring in 120 days)
    r_ren = client.get("/api/v1/contracts?renewal_days=40", headers=headers)
    assert r_ren.status_code == status.HTTP_200_OK
    data_ren = r_ren.json()
    assert len(data_ren) == 1
    assert data_ren[0]["contract_number"] == "CNT-FILTER-1"


def test_search_contracts_permission_denied(client, test_session_data):
    """Verify that requests with unauthorized/invalid roles return 403 Forbidden."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "unauthorized_role_xyz"}
    response = client.get("/api/v1/contracts", headers=headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    data = response.json()
    assert data["detail"]["code"] == "FORBIDDEN"


def test_get_contract_by_id_success(client, test_session_data):
    """Verify GET /api/v1/contracts/{id} returns full contract details including clauses and SLA commitments."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    payload = {
        "contract_number": "CNT-GET-001",
        "title": "Master Services Agreement",
        "counterparty": "Acme Corp",
        "status": "ACTIVE",
        "effective_on": today_str,
        "expires_on": today_str,
        "clauses": [
            {
                "title": "Confidentiality Clause",
                "clause_type": "CONFIDENTIALITY",
                "content": "Strict NDA applies.",
            }
        ],
        "sla_commitments": [
            {
                "metric_name": "Uptime",
                "target_value": "99.9%",
                "penalty_terms": "Credit applied on breach.",
            }
        ],
    }

    create_resp = client.post("/api/v1/contracts", json=payload, headers=headers)
    assert create_resp.status_code == status.HTTP_201_CREATED
    contract_id = create_resp.json()["id"]

    get_resp = client.get(f"/api/v1/contracts/{contract_id}", headers=headers)
    assert get_resp.status_code == status.HTTP_200_OK
    data = get_resp.json()
    assert data["id"] == contract_id
    assert data["contract_number"] == "CNT-GET-001"
    assert len(data["clauses"]) == 1
    assert data["clauses"][0]["title"] == "Confidentiality Clause"
    assert len(data["sla_commitments"]) == 1
    assert data["sla_commitments"][0]["metric_name"] == "Uptime"


def test_get_contract_by_id_not_found(client, test_session_data):
    """Verify GET /api/v1/contracts/{id} returns 404 NOT_FOUND for nonexistent contract ID."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    response = client.get("/api/v1/contracts/nonexistent-uuid-1234", headers=headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["detail"]["code"] == "NOT_FOUND"


def test_get_contract_by_id_permission_denied(client, test_session_data):
    """Verify GET /api/v1/contracts/{id} returns 403 FORBIDDEN when user lacks read role."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "invalid_role"}
    response = client.get("/api/v1/contracts/some-contract-id", headers=headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    data = response.json()
    assert data["detail"]["code"] == "FORBIDDEN"


def test_delete_contract_by_id_success(client, test_session_data):
    """Verify DELETE /api/v1/contracts/{id} deletes contract and returns 204 No Content with empty body."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    payload = {
        "contract_number": "CNT-DEL-001",
        "title": "Temporary Agreement",
        "counterparty": "Disposable Vendor",
        "effective_on": today_str,
        "expires_on": today_str,
    }

    create_resp = client.post("/api/v1/contracts", json=payload, headers=headers)
    assert create_resp.status_code == status.HTTP_201_CREATED
    contract_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/v1/contracts/{contract_id}", headers=headers)
    assert del_resp.status_code == status.HTTP_204_NO_CONTENT
    assert del_resp.content == b""  # Empty body as required by 204 No Content

    # Confirm record no longer exists
    get_resp = client.get(f"/api/v1/contracts/{contract_id}", headers=headers)
    assert get_resp.status_code == status.HTTP_404_NOT_FOUND


def test_delete_contract_by_id_not_found(client, test_session_data):
    """Verify DELETE /api/v1/contracts/{id} returns 404 NOT_FOUND for nonexistent contract ID."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    response = client.delete("/api/v1/contracts/nonexistent-uuid-9999", headers=headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["detail"]["code"] == "NOT_FOUND"


def test_delete_contract_by_id_permission_denied(client, test_session_data):
    """Verify DELETE /api/v1/contracts/{id} returns 403 FORBIDDEN when user has read-only role."""
    headers_write = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    payload = {
        "contract_number": "CNT-DEL-002",
        "title": "Protected Contract",
        "counterparty": "Important Vendor",
        "effective_on": today_str,
        "expires_on": today_str,
    }

    create_resp = client.post("/api/v1/contracts", json=payload, headers=headers_write)
    assert create_resp.status_code == status.HTTP_201_CREATED
    contract_id = create_resp.json()["id"]

    # Try deleting with stakeholder (read-only) role
    headers_read_only = {"X-Session-ID": test_session_data.id, "X-User-Roles": "stakeholder"}
    del_resp = client.delete(f"/api/v1/contracts/{contract_id}", headers=headers_read_only)
    assert del_resp.status_code == status.HTTP_403_FORBIDDEN
    data = del_resp.json()
    assert data["detail"]["code"] == "FORBIDDEN"


def test_create_and_get_amendments_success(client, test_session_data):
    """Verify recording amendments and retrieving amendment history with HMAC verification."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    contract_payload = {
        "contract_number": "CNT-AMD-TEST-001",
        "title": "Vendor SLA Agreement",
        "counterparty": "Vendor Inc",
        "effective_on": today_str,
        "expires_on": today_str,
    }

    create_resp = client.post("/api/v1/contracts", json=contract_payload, headers=headers)
    assert create_resp.status_code == status.HTTP_201_CREATED
    contract_id = create_resp.json()["id"]

    # Record 1st amendment
    amd1_payload = {"summary": "Added liability cap clause of $1,000,000."}
    resp_amd1 = client.post(f"/api/v1/contracts/{contract_id}/amendments", json=amd1_payload, headers=headers)
    assert resp_amd1.status_code == status.HTTP_200_OK
    data_amd1 = resp_amd1.json()
    assert data_amd1["contract_id"] == contract_id
    assert data_amd1["amendment_number"] == 1
    assert data_amd1["summary"] == "Added liability cap clause of $1,000,000."
    assert len(data_amd1["integrity_hash"]) == 64

    # Record 2nd amendment
    amd2_payload = {"summary": "Updated payment terms to Net 60 days."}
    resp_amd2 = client.post(f"/api/v1/contracts/{contract_id}/amendments", json=amd2_payload, headers=headers)
    assert resp_amd2.status_code == status.HTTP_200_OK
    data_amd2 = resp_amd2.json()
    assert data_amd2["amendment_number"] == 2

    # Get amendment history
    resp_hist = client.get(f"/api/v1/contracts/{contract_id}/amendments", headers=headers)
    assert resp_hist.status_code == status.HTTP_200_OK
    history = resp_hist.json()
    assert len(history) == 2
    assert history[0]["amendment_number"] == 1
    assert history[1]["amendment_number"] == 2


def test_create_amendment_contract_not_found(client, test_session_data):
    """Verify posting amendment to nonexistent contract returns 404 NOT_FOUND."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    payload = {"summary": "Amendment for missing contract"}

    response = client.post("/api/v1/contracts/nonexistent-uuid-9999/amendments", json=payload, headers=headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["detail"]["code"] == "NOT_FOUND"


def test_create_amendment_unauthorized_role(client, test_session_data):
    """Verify stakeholder role (read-only) cannot record amendment (403 FORBIDDEN)."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    create_resp = client.post(
        "/api/v1/contracts",
        json={
            "contract_number": "CNT-AMD-TEST-002",
            "title": "Auth Test Contract",
            "counterparty": "Test Corp",
            "effective_on": today_str,
            "expires_on": today_str,
        },
        headers=headers,
    )
    contract_id = create_resp.json()["id"]

    read_headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "stakeholder"}
    payload = {"summary": "Unauthorized attempt"}

    response = client.post(f"/api/v1/contracts/{contract_id}/amendments", json=payload, headers=read_headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    data = response.json()
    assert data["detail"]["code"] == "FORBIDDEN"


def test_create_amendment_validation_error(client, test_session_data):
    """Verify empty or whitespace-only summary returns 400 BAD_REQUEST."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    create_resp = client.post(
        "/api/v1/contracts",
        json={
            "contract_number": "CNT-AMD-TEST-003",
            "title": "Validation Test Contract",
            "counterparty": "Test Corp",
            "effective_on": today_str,
            "expires_on": today_str,
        },
        headers=headers,
    )
    contract_id = create_resp.json()["id"]

    payload = {"summary": "   "}
    response = client.post(f"/api/v1/contracts/{contract_id}/amendments", json=payload, headers=headers)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert data["detail"]["code"] == "BAD_REQUEST"


def test_get_amendments_not_found(client, test_session_data):
    """Verify fetching amendment history for nonexistent contract returns 404 NOT_FOUND."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    response = client.get("/api/v1/contracts/nonexistent-uuid-8888/amendments", headers=headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["detail"]["code"] == "NOT_FOUND"


def test_get_amendments_tamper_detection(client, test_session_data, test_db):
    """Verify that tampered audit log records trigger HTTP 500 INTEGRITY_ERROR."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    create_resp = client.post(
        "/api/v1/contracts",
        json={
            "contract_number": "CNT-AMD-TAMPER-01",
            "title": "Tamper Test Contract",
            "counterparty": "Tamper Corp",
            "effective_on": today_str,
            "expires_on": today_str,
        },
        headers=headers,
    )
    contract_id = create_resp.json()["id"]

    client.post(
        f"/api/v1/contracts/{contract_id}/amendments",
        json={"summary": "Genuine amendment text"},
        headers=headers,
    )

    # Tamper with record directly in DB
    db_record = test_db.query(AmendmentAuditLog).filter(AmendmentAuditLog.contract_id == contract_id).first()
    db_record.summary = "TAMPERED SUMMARY CONTENT"
    test_db.commit()

    # Request amendment history -> HMAC failure
    response = client.get(f"/api/v1/contracts/{contract_id}/amendments", headers=headers)
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    data = response.json()
    assert data["detail"]["code"] == "INTEGRITY_ERROR"


def test_upload_and_list_documents_success(client, test_session_data):
    """Verify document upload increments version number and listing returns document version history."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    create_resp = client.post(
        "/api/v1/contracts",
        json={
            "contract_number": "CNT-DOC-TEST-001",
            "title": "Document Test Agreement",
            "counterparty": "DocuCorp",
            "effective_on": today_str,
            "expires_on": today_str,
        },
        headers=headers,
    )
    assert create_resp.status_code == status.HTTP_201_CREATED
    contract_id = create_resp.json()["id"]

    # Upload version 1 document
    file1_content = b"PDF Document Version 1 Content Bytes"
    files1 = {"file": ("agreement_v1.pdf", file1_content, "application/pdf")}
    upload1_resp = client.post(f"/api/v1/contracts/{contract_id}/documents", files=files1, headers=headers)
    assert upload1_resp.status_code == status.HTTP_201_CREATED
    doc1_data = upload1_resp.json()
    assert doc1_data["contract_id"] == contract_id
    assert doc1_data["version_number"] == 1
    assert doc1_data["file_size_bytes"] == len(file1_content)

    # Upload version 2 document
    file2_content = b"PDF Document Version 2 Amended Content Bytes"
    files2 = {"file": ("agreement_v2.pdf", file2_content, "application/pdf")}
    upload2_resp = client.post(f"/api/v1/contracts/{contract_id}/documents", files=files2, headers=headers)
    assert upload2_resp.status_code == status.HTTP_201_CREATED
    doc2_data = upload2_resp.json()
    assert doc2_data["version_number"] == 2

    # List documents
    list_resp = client.get(f"/api/v1/contracts/{contract_id}/documents", headers=headers)
    assert list_resp.status_code == status.HTTP_200_OK
    docs = list_resp.json()
    assert len(docs) == 2
    assert docs[0]["version_number"] == 2
    assert docs[1]["version_number"] == 1


def test_download_document_success(client, test_session_data):
    """Verify download endpoint streams exact document file content."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    create_resp = client.post(
        "/api/v1/contracts",
        json={
            "contract_number": "CNT-DOC-DL-001",
            "title": "Download Test Contract",
            "counterparty": "Download Inc",
            "effective_on": today_str,
            "expires_on": today_str,
        },
        headers=headers,
    )
    contract_id = create_resp.json()["id"]

    file_content = b"Sample Contract PDF Content"
    files = {"file": ("contract_file.pdf", file_content, "application/pdf")}
    upload_resp = client.post(f"/api/v1/contracts/{contract_id}/documents", files=files, headers=headers)
    assert upload_resp.status_code == status.HTTP_201_CREATED
    doc_id = upload_resp.json()["id"]

    # Download by version ID
    download_resp = client.get(f"/api/v1/contracts/{contract_id}/documents/{doc_id}/download", headers=headers)
    assert download_resp.status_code == status.HTTP_200_OK
    assert download_resp.content == file_content
    assert "Content-Disposition" in download_resp.headers

    # Download by version number "1"
    download_v1_resp = client.get(f"/api/v1/contracts/{contract_id}/documents/1/download", headers=headers)
    assert download_v1_resp.status_code == status.HTTP_200_OK
    assert download_v1_resp.content == file_content


def test_document_endpoints_not_found(client, test_session_data):
    """Verify document endpoints return 404 NOT_FOUND for nonexistent contracts or version IDs."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}

    # List documents for missing contract
    r_list = client.get("/api/v1/contracts/nonexistent-uuid-0001/documents", headers=headers)
    assert r_list.status_code == status.HTTP_404_NOT_FOUND
    assert r_list.json()["detail"]["code"] == "NOT_FOUND"

    # Upload document for missing contract
    files = {"file": ("test.pdf", b"Some content", "application/pdf")}
    r_upload = client.post("/api/v1/contracts/nonexistent-uuid-0001/documents", files=files, headers=headers)
    assert r_upload.status_code == status.HTTP_404_NOT_FOUND
    assert r_upload.json()["detail"]["code"] == "NOT_FOUND"

    # Download missing document version
    r_download = client.get("/api/v1/contracts/nonexistent-uuid-0001/documents/v1/download", headers=headers)
    assert r_download.status_code == status.HTTP_404_NOT_FOUND
    assert r_download.json()["detail"]["code"] == "NOT_FOUND"


def test_document_endpoints_permission_denied(client, test_session_data):
    """Verify unauthorized role cannot upload document versions (403 FORBIDDEN)."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    create_resp = client.post(
        "/api/v1/contracts",
        json={
            "contract_number": "CNT-DOC-PERM-01",
            "title": "Permission Test Contract",
            "counterparty": "Perm Corp",
            "effective_on": today_str,
            "expires_on": today_str,
        },
        headers=headers,
    )
    contract_id = create_resp.json()["id"]

    # Stakeholder role is read-only -> cannot upload
    read_headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "stakeholder"}
    files = {"file": ("unauthorized.pdf", b"Data bytes", "application/pdf")}
    upload_resp = client.post(f"/api/v1/contracts/{contract_id}/documents", files=files, headers=read_headers)
    assert upload_resp.status_code == status.HTTP_403_FORBIDDEN
    assert upload_resp.json()["detail"]["code"] == "FORBIDDEN"


def test_upload_document_validation_error(client, test_session_data):
    """Verify uploading invalid document extension or failed hash check returns 400 BAD_REQUEST."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    create_resp = client.post(
        "/api/v1/contracts",
        json={
            "contract_number": "CNT-DOC-VAL-01",
            "title": "Validation Test Contract",
            "counterparty": "Val Corp",
            "effective_on": today_str,
            "expires_on": today_str,
        },
        headers=headers,
    )
    contract_id = create_resp.json()["id"]

    # Illegal extension .exe
    bad_ext_files = {"file": ("malicious.exe", b"binary data", "application/octet-stream")}
    resp_ext = client.post(f"/api/v1/contracts/{contract_id}/documents", files=bad_ext_files, headers=headers)
    assert resp_ext.status_code == status.HTTP_400_BAD_REQUEST
    assert resp_ext.json()["detail"]["code"] == "BAD_REQUEST"

    # Expected hash mismatch
    files = {"file": ("valid.pdf", b"Some content", "application/pdf")}
    data_form = {"expected_hash": "invalid_hash_1234567890abcdef"}
    resp_hash = client.post(
        f"/api/v1/contracts/{contract_id}/documents",
        files=files,
        data=data_form,
        headers=headers,
    )
    assert resp_hash.status_code == status.HTTP_400_BAD_REQUEST
    assert resp_hash.json()["detail"]["code"] == "BAD_REQUEST"


def test_esignature_requests_unauthenticated(client):
    """Verify that unauthenticated requests to GET /api/v1/esignature/requests return 401 Unauthorized."""
    response = client.get("/api/v1/esignature/requests")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_create_and_list_esignature_requests(client, test_session_data):
    """Verify creating e-signature request via POST and listing via GET /api/v1/esignature/requests."""
    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Create contract first
    create_contract_resp = client.post(
        "/api/v1/contracts",
        json={
            "contract_number": "CNT-ESIG-API-001",
            "title": "E-Signature Test Contract",
            "counterparty": "Signer Corp",
            "effective_on": today_str,
            "expires_on": today_str,
        },
        headers=headers,
    )
    assert create_contract_resp.status_code == status.HTTP_201_CREATED
    contract_id = create_contract_resp.json()["id"]

    # Create e-signature request
    esig_payload = {
        "contract_id": contract_id,
        "recipient_email": "signer@example.com",
        "recipient_name": "John Signer",
        "subject": "Please sign contract",
    }
    create_esig_resp = client.post("/api/v1/esignature/requests", json=esig_payload, headers=headers)
    assert create_esig_resp.status_code == status.HTTP_201_CREATED
    esig_data = create_esig_resp.json()
    assert esig_data["contract_id"] == contract_id
    assert esig_data["recipient_email"] == "signer@example.com"
    assert esig_data["recipient_name"] == "John Signer"
    assert esig_data["execution_status"] == "PENDING"
    assert esig_data["provider_ref_id"] is not None

    # List requests via tracking hub endpoint
    list_esig_resp = client.get("/api/v1/esignature/requests", headers=headers)
    assert list_esig_resp.status_code == status.HTTP_200_OK
    requests = list_esig_resp.json()
    assert len(requests) >= 1
    matched = [r for r in requests if r["id"] == esig_data["id"]]
    assert len(matched) == 1
    assert matched[0]["execution_status"] == "PENDING"

    # Filter list by status
    list_pending_resp = client.get("/api/v1/esignature/requests?status=PENDING", headers=headers)
    assert list_pending_resp.status_code == status.HTTP_200_OK
    assert len(list_pending_resp.json()) >= 1

    # Filter list by contract_id
    list_contract_resp = client.get(f"/api/v1/esignature/requests?contract_id={contract_id}", headers=headers)
    assert list_contract_resp.status_code == status.HTTP_200_OK
    assert len(list_contract_resp.json()) == 1


def test_esignature_webhook_receiver(client, test_session_data):
    """Verify processing e-signature webhook event updates request status in DB."""
    import hmac
    import hashlib
    import json

    headers = {"X-Session-ID": test_session_data.id, "X-User-Roles": "legal_manager"}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Create contract and signature request
    c_resp = client.post(
        "/api/v1/contracts",
        json={
            "contract_number": "CNT-WEBHOOK-01",
            "title": "Webhook Test Contract",
            "counterparty": "Webhook Corp",
            "effective_on": today_str,
            "expires_on": today_str,
        },
        headers=headers,
    )
    contract_id = c_resp.json()["id"]

    esig_resp = client.post(
        "/api/v1/esignature/requests",
        json={
            "contract_id": contract_id,
            "recipient_email": "webhook.signer@example.com",
            "recipient_name": "Webhook Signer",
        },
        headers=headers,
    )
    provider_ref_id = esig_resp.json()["provider_ref_id"]

    # Send webhook payload updating status to SIGNED
    future_time = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    webhook_payload = {
        "provider_ref_id": provider_ref_id,
        "execution_status": "SIGNED",
        "event_timestamp": future_time,
    }
    payload_bytes = json.dumps(webhook_payload).encode("utf-8")

    # Calculate HMAC signature using default secret
    secret = "clm_esign_webhook_secret_key_change_me_in_production".encode("utf-8")
    expected_sig = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()

    webhook_headers = {"X-Signature": f"sha256={expected_sig}"}
    wh_resp = client.post(
        "/api/v1/esignature/webhooks/mock",
        content=payload_bytes,
        headers=webhook_headers,
    )
    assert wh_resp.status_code == status.HTTP_200_OK
    wh_data = wh_resp.json()
    assert wh_data["status"] == "updated"
    assert wh_data["provider_ref_id"] == provider_ref_id
    assert wh_data["execution_status"] == "SIGNED"

    # Verify status changed in list tracking hub
    list_resp = client.get(f"/api/v1/esignature/requests?contract_id={contract_id}", headers=headers)
    assert list_resp.status_code == status.HTTP_200_OK
    requests = list_resp.json()
    assert requests[0]["execution_status"] == "SIGNED"


def test_esignature_webhook_invalid_signature(client):
    """Verify invalid webhook HMAC signature returns 401 Unauthorized."""
    payload = json.dumps({"provider_ref_id": "fake_esig_123", "execution_status": "SIGNED"}).encode("utf-8")
    headers = {"X-Signature": "sha256=invalid_signature_hash_value"}

    wh_resp = client.post(
        "/api/v1/esignature/webhooks/mock",
        content=payload,
        headers=headers,
    )
    assert wh_resp.status_code == status.HTTP_401_UNAUTHORIZED
    assert wh_resp.json()["detail"]["code"] == "UNAUTHORIZED"
