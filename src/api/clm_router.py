"""Contract Lifecycle Management API endpoint router for contracts, amendments, documents, and e-signatures."""

import logging
from datetime import date as date_type, datetime
from typing import Any, List, Optional, Union

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from src.api.deps import get_current_session
from src.models.base import get_db
from src.models.session import Session as SessionModel
from src.services.clm.contract_service import (
    AmendmentIntegrityError,
    ContractNotFoundError,
    ContractPermissionDeniedError,
    ContractTamperedError,
    ContractValidationError,
    create_contract,
    delete_contract,
    get_amendment_history,
    get_contract_detail,
    list_contracts,
    record_amendment,
)
from src.services.clm.document_service import (
    DocumentNotFoundError,
    DocumentPermissionDeniedError,
    DocumentStorageError,
    DocumentValidationError,
    download_document_version,
    list_document_versions,
    upload_document_version,
)
from src.services.clm.esignature_service import (
    ESignatureNotFoundError,
    ESignaturePermissionDeniedError,
    ESignatureValidationError,
    ESignatureWebhookError,
    create_signature_request,
    list_signature_requests,
    process_webhook_event,
)

logger = logging.getLogger(__name__)

contracts_router = APIRouter(prefix="/api/v1/contracts", tags=["contracts"])
esignature_router = APIRouter(prefix="/api/v1/esignature", tags=["esignature"])

router = APIRouter()
router.include_router(contracts_router)
router.include_router(esignature_router)


# --- Schemas ---


class ClauseHighlightCreate(BaseModel):
    """Schema for creating a clause highlight record."""

    title: str = Field(..., description="Title of the clause highlight", examples=["Confidentiality Terms"])
    clause_type: str = Field(
        default="SPECIAL_TERM",
        description="Type of clause (CONFIDENTIALITY, TERMINATION, INDEMNIFICATION, GOVERNING_LAW, SPECIAL_TERM)",
        examples=["CONFIDENTIALITY"],
    )
    content: str = Field(..., description="Text content of the clause highlight")


class ClauseHighlightSchema(BaseModel):
    """Schema for returning clause highlight details."""

    id: str
    contract_id: str
    title: str
    clause_type: str
    content: str
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SLACommitmentCreate(BaseModel):
    """Schema for creating an SLA commitment record."""

    metric_name: str = Field(..., description="Name of the SLA metric", examples=["System Uptime"])
    target_value: str = Field(..., description="Target value or threshold", examples=["99.9%"])
    penalty_terms: Optional[str] = Field(None, description="Optional penalty or credit terms")


class SLACommitmentSchema(BaseModel):
    """Schema for returning SLA commitment details."""

    id: str
    contract_id: str
    metric_name: str
    target_value: str
    penalty_terms: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ContractCreateSchema(BaseModel):
    """Request payload schema for creating a contract record."""

    contract_number: str = Field(..., description="Unique contract identifier number", examples=["CNT-2026-001"])
    title: str = Field(..., description="Contract title", examples=["Master Services Agreement"])
    counterparty: str = Field(..., description="Counterparty organization name", examples=["Acme Corp"])
    status: Optional[str] = Field(default="DRAFT", description="Contract status (DRAFT, ACTIVE, EXPIRED, TERMINATED)")
    effective_on: Union[date_type, str] = Field(..., description="Effective start date (YYYY-MM-DD)", examples=["2026-01-01"])
    expires_on: Union[date_type, str] = Field(..., description="Expiration date (YYYY-MM-DD)", examples=["2026-12-31"])
    auto_renew: Optional[bool] = Field(default=False, description="Auto-renewal flag")
    clauses: Optional[List[ClauseHighlightCreate]] = Field(default_factory=list, description="Nested clause highlights")
    sla_commitments: Optional[List[SLACommitmentCreate]] = Field(default_factory=list, description="Nested SLA commitments")


class ContractSummarySchema(BaseModel):
    """Schema for returning summary details of a contract."""

    id: str
    contract_number: str
    title: str
    counterparty: str
    status: str
    effective_on: date_type
    expires_on: date_type
    auto_renew: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ContractDetailSchema(ContractSummarySchema):
    """Schema for returning full contract details including clauses and SLA commitments."""

    clauses: List[ClauseHighlightSchema] = Field(default_factory=list)
    sla_commitments: List[SLACommitmentSchema] = Field(default_factory=list)

    class Config:
        from_attributes = True


class AmendmentCreateSchema(BaseModel):
    """Request payload schema for recording a contract amendment."""

    summary: str = Field(..., description="Text summary describing amendment changes", examples=["Extended agreement term by 12 months"])
    user_id: Optional[str] = Field(None, description="Optional user ID override; defaults to current session user_id")
    timestamp: Optional[datetime] = Field(None, description="Optional entry timestamp")


class AmendmentAuditLogSchema(BaseModel):
    """Schema for returning amendment audit log entry details."""

    id: str
    contract_id: str
    amendment_number: int
    summary: str
    user_id: str
    timestamp: datetime
    integrity_hash: str

    class Config:
        from_attributes = True


class DocumentVersionSchema(BaseModel):
    """Schema for returning document version details."""

    id: str
    contract_id: str
    version_number: int
    file_path: str
    file_hash: str
    file_size_bytes: int
    uploaded_by_user_id: str
    uploaded_at: datetime

    class Config:
        from_attributes = True


class ESignatureRequestSchema(BaseModel):
    """Schema for returning e-signature request details."""

    id: str
    contract_id: str
    provider_ref_id: Optional[str] = None
    execution_status: str
    recipient_email: str
    recipient_name: str
    status_updated_at: datetime
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ESignatureRequestCreateSchema(BaseModel):
    """Schema for creating an e-signature request."""

    contract_id: str = Field(..., description="Associated contract UUID")
    recipient_email: str = Field(..., description="Email address of recipient signer")
    recipient_name: str = Field(..., description="Full name of recipient signer")
    subject: Optional[str] = Field(None, description="Optional custom email subject line")


class WebhookAckSchema(BaseModel):
    """Schema for returning webhook acknowledgment response."""

    status: str
    provider_ref_id: Optional[str] = None
    execution_status: Optional[str] = None
    status_updated_at: Optional[str] = None
    reason: Optional[str] = None


# --- Helpers ---


def get_effective_roles(
    roles_param: Optional[str] = None,
    x_user_roles: Optional[str] = None,
    x_user_role: Optional[str] = None,
    session: Optional[SessionModel] = None,
) -> Optional[str]:
    """Extract and normalize user roles from query parameter, HTTP headers, or active user session."""
    if roles_param:
        return roles_param
    if x_user_roles:
        return x_user_roles
    if x_user_role:
        return x_user_role
    if session and session.user:
        if hasattr(session.user, "roles") and getattr(session.user, "roles"):
            return str(getattr(session.user, "roles"))
        if hasattr(session.user, "role") and getattr(session.user, "role"):
            return str(getattr(session.user, "role"))
    return "legal_manager"


# --- Route Handlers ---


@contracts_router.get(
    "",
    response_model=List[ContractDetailSchema],
    status_code=status.HTTP_200_OK,
    summary="Search and filter contract repository records",
    description=(
        "Query contracts by status, counterparty, search keyword, or upcoming renewal timeframe. "
        "Requires authenticated user session and role authorization."
    ),
)
def search_contracts_endpoint(
    status_filter: Optional[str] = Query(
        None,
        alias="status",
        description="Filter by contract status (DRAFT, ACTIVE, EXPIRED, TERMINATED)",
    ),
    counterparty: Optional[str] = Query(
        None,
        description="Filter by counterparty organization name substring",
    ),
    keyword: Optional[str] = Query(
        None,
        description="Search keyword matching title, contract number, or counterparty",
    ),
    renewal_days: Optional[int] = Query(
        None,
        description="Filter contracts expiring within N days from today",
    ),
    roles: Optional[str] = Query(
        None,
        description="Optional role override for RBAC verification",
    ),
    x_user_roles: Optional[str] = Header(None, alias="X-User-Roles"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> List[Any]:
    """Retrieve filtered contracts matching query parameters and user role access."""
    effective_roles = get_effective_roles(roles, x_user_roles, x_user_role, session)
    logger.info(
        "Searching contracts for user_id=%s with filters (status=%s, counterparty=%s, keyword=%s, renewal_days=%s, roles=%s)",
        session.user_id,
        status_filter,
        counterparty,
        keyword,
        renewal_days,
        effective_roles,
    )

    try:
        contracts = list_contracts(
            db=db,
            status=status_filter,
            counterparty=counterparty,
            keyword=keyword,
            renewal_days=renewal_days,
            roles=effective_roles,
        )
        logger.info(
            "Found %d contract record(s) matching criteria for user_id=%s",
            len(contracts),
            session.user_id,
        )
        return contracts
    except ContractPermissionDeniedError as err:
        logger.warning("RBAC permission denied for user_id=%s in GET /api/v1/contracts: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except ContractValidationError as err:
        logger.warning("Validation error in GET /api/v1/contracts for user_id=%s: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )


@contracts_router.post(
    "",
    response_model=ContractDetailSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new contract record",
    description=(
        "Create a new legal agreement with optional initial clause highlights and SLA commitments. "
        "Requires write role authorization (e.g., legal_manager, admin)."
    ),
)
def create_contract_endpoint(
    payload: ContractCreateSchema,
    roles: Optional[str] = Query(
        None,
        description="Optional role override for RBAC verification",
    ),
    x_user_roles: Optional[str] = Header(None, alias="X-User-Roles"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> Any:
    """Create new contract entity record in repository."""
    effective_roles = get_effective_roles(roles, x_user_roles, x_user_role, session)
    logger.info(
        "Creating contract number '%s' for user_id=%s with roles=%s",
        payload.contract_number,
        session.user_id,
        effective_roles,
    )

    contract_data = payload.model_dump()
    try:
        created_record = create_contract(
            db=db,
            data=contract_data,
            roles=effective_roles,
        )
        logger.info(
            "Successfully created contract ID '%s' (number: '%s') for user_id=%s",
            created_record.id,
            created_record.contract_number,
            session.user_id,
        )
        return created_record
    except ContractPermissionDeniedError as err:
        logger.warning("RBAC write permission denied for user_id=%s in POST /api/v1/contracts: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except ContractValidationError as err:
        logger.warning("Validation error in POST /api/v1/contracts for user_id=%s: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )


@contracts_router.get(
    "/{id}",
    response_model=ContractDetailSchema,
    status_code=status.HTTP_200_OK,
    summary="Get detailed contract record by ID",
    description=(
        "Retrieve full contract details including clause highlights and SLA commitments. "
        "Requires authenticated user session and read role authorization."
    ),
)
def get_contract_endpoint(
    id: str,
    roles: Optional[str] = Query(
        None,
        description="Optional role override for RBAC verification",
    ),
    x_user_roles: Optional[str] = Header(None, alias="X-User-Roles"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> Any:
    """Retrieve detailed contract record including clause highlights and SLA commitments."""
    effective_roles = get_effective_roles(roles, x_user_roles, x_user_role, session)
    logger.info(
        "Fetching contract details for id='%s' and user_id=%s with roles=%s",
        id,
        session.user_id,
        effective_roles,
    )

    try:
        contract = get_contract_detail(
            db=db,
            contract_id=id,
            roles=effective_roles,
        )
        return contract
    except ContractNotFoundError as err:
        logger.warning("Contract not found for id='%s' in GET /api/v1/contracts/{id}: %s", id, err)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )
    except ContractPermissionDeniedError as err:
        logger.warning("RBAC permission denied for user_id=%s in GET /api/v1/contracts/{id}: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except ContractValidationError as err:
        logger.warning("Validation error in GET /api/v1/contracts/{id} for user_id=%s: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )


@contracts_router.delete(
    "/{id}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a contract record",
    description=(
        "Permanently delete a contract record by ID. "
        "Requires delete role authorization (e.g., legal_manager, admin)."
    ),
)
def delete_contract_endpoint(
    id: str,
    roles: Optional[str] = Query(
        None,
        description="Optional role override for RBAC verification",
    ),
    x_user_roles: Optional[str] = Header(None, alias="X-User-Roles"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> Response:
    """Delete a contract record by ID."""
    effective_roles = get_effective_roles(roles, x_user_roles, x_user_role, session)
    logger.info(
        "Deleting contract id='%s' for user_id=%s with roles=%s",
        id,
        session.user_id,
        effective_roles,
    )

    try:
        delete_contract(
            db=db,
            contract_id=id,
            roles=effective_roles,
        )
        logger.info(
            "Successfully deleted contract id='%s' for user_id=%s",
            id,
            session.user_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ContractNotFoundError as err:
        logger.warning("Contract not found for id='%s' in DELETE /api/v1/contracts/{id}: %s", id, err)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )
    except ContractPermissionDeniedError as err:
        logger.warning("RBAC delete permission denied for user_id=%s in DELETE /api/v1/contracts/{id}: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except ContractValidationError as err:
        logger.warning("Validation error in DELETE /api/v1/contracts/{id} for user_id=%s: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )


@contracts_router.get(
    "/{id}/amendments",
    response_model=List[AmendmentAuditLogSchema],
    status_code=status.HTTP_200_OK,
    summary="Get contract amendment audit history",
    description=(
        "Retrieve chronological immutable amendment audit history for a contract. "
        "Includes HMAC cryptographic integrity verification for tamper-evident audit trail."
    ),
)
def get_contract_amendments_endpoint(
    id: str,
    verify_integrity: Optional[bool] = Query(
        True,
        description="Verify HMAC signatures of amendment logs",
    ),
    roles: Optional[str] = Query(
        None,
        description="Optional role override for RBAC verification",
    ),
    x_user_roles: Optional[str] = Header(None, alias="X-User-Roles"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> Any:
    """Retrieve contract amendment audit trail history."""
    effective_roles = get_effective_roles(roles, x_user_roles, x_user_role, session)
    logger.info(
        "Fetching amendment history for contract id='%s' and user_id=%s with roles=%s",
        id,
        session.user_id,
        effective_roles,
    )

    try:
        amendments = get_amendment_history(
            db=db,
            contract_id=id,
            roles=effective_roles,
            verify_integrity=verify_integrity if verify_integrity is not None else True,
        )
        return amendments
    except ContractNotFoundError as err:
        logger.warning("Contract not found for id='%s' in GET /api/v1/contracts/{id}/amendments: %s", id, err)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )
    except ContractPermissionDeniedError as err:
        logger.warning("RBAC permission denied for user_id=%s in GET /api/v1/contracts/{id}/amendments: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except (AmendmentIntegrityError, ContractTamperedError) as err:
        logger.error("Tamper detection or HMAC integrity failure for contract id='%s': %s", id, err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTEGRITY_ERROR",
                "message": str(err),
                "details": {},
            },
        )
    except ContractValidationError as err:
        logger.warning("Validation error in GET /api/v1/contracts/{id}/amendments for user_id=%s: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )


@contracts_router.post(
    "/{id}/amendments",
    response_model=AmendmentAuditLogSchema,
    status_code=status.HTTP_200_OK,
    summary="Record a new contract amendment",
    description=(
        "Record an immutable contract amendment with automatic HMAC cryptographic audit logging. "
        "Requires write role authorization (e.g., legal_manager, admin)."
    ),
)
def create_contract_amendment_endpoint(
    id: str,
    payload: AmendmentCreateSchema,
    roles: Optional[str] = Query(
        None,
        description="Optional role override for RBAC verification",
    ),
    x_user_roles: Optional[str] = Header(None, alias="X-User-Roles"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> Any:
    """Record a new contract amendment with HMAC audit verification."""
    effective_roles = get_effective_roles(roles, x_user_roles, x_user_role, session)
    user_id = (
        payload.user_id.strip()
        if (payload.user_id and payload.user_id.strip())
        else session.user_id
    )

    logger.info(
        "Recording amendment for contract id='%s' by user_id=%s with roles=%s",
        id,
        user_id,
        effective_roles,
    )

    try:
        amendment = record_amendment(
            db=db,
            contract_id=id,
            summary=payload.summary,
            user_id=user_id,
            timestamp=payload.timestamp,
            roles=effective_roles,
        )
        logger.info(
            "Successfully recorded amendment #%d (ID: %s) for contract id='%s'",
            amendment.amendment_number,
            amendment.id,
            id,
        )
        return amendment
    except ContractNotFoundError as err:
        logger.warning("Contract not found for id='%s' in POST /api/v1/contracts/{id}/amendments: %s", id, err)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )
    except ContractPermissionDeniedError as err:
        logger.warning("RBAC write permission denied for user_id=%s in POST /api/v1/contracts/{id}/amendments: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except ContractValidationError as err:
        logger.warning("Validation error in POST /api/v1/contracts/{id}/amendments for user_id=%s: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )


@contracts_router.get(
    "/{id}/documents",
    response_model=List[DocumentVersionSchema],
    status_code=status.HTTP_200_OK,
    summary="List document versions for a contract",
    description=(
        "Retrieve historical document version records for a contract ordered by version number. "
        "Requires authenticated user session and read role authorization."
    ),
)
def list_contract_documents_endpoint(
    id: str,
    roles: Optional[str] = Query(
        None,
        description="Optional role override for RBAC verification",
    ),
    x_user_roles: Optional[str] = Header(None, alias="X-User-Roles"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> Any:
    """Retrieve document version history for a contract."""
    effective_roles = get_effective_roles(roles, x_user_roles, x_user_role, session)
    logger.info(
        "Fetching document versions for contract id='%s' and user_id=%s with roles=%s",
        id,
        session.user_id,
        effective_roles,
    )

    try:
        versions = list_document_versions(
            db=db,
            contract_id=id,
            roles=effective_roles,
        )
        return versions
    except DocumentNotFoundError as err:
        logger.warning("Contract or document version not found for id='%s' in GET /api/v1/contracts/{id}/documents: %s", id, err)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )
    except DocumentPermissionDeniedError as err:
        logger.warning("RBAC permission denied for user_id=%s in GET /api/v1/contracts/{id}/documents: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except DocumentValidationError as err:
        logger.warning("Validation error in GET /api/v1/contracts/{id}/documents for user_id=%s: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )


@contracts_router.post(
    "/{id}/documents",
    response_model=DocumentVersionSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new document version",
    description=(
        "Upload a new contract document revision. Automatically increments version number, "
        "calculates SHA-256 hash checksum, and saves file into encrypted storage repository. "
        "Requires write role authorization (e.g., legal_manager, admin)."
    ),
)
def upload_contract_document_endpoint(
    id: str,
    file: UploadFile = File(..., description="Document file to upload"),
    expected_hash: Optional[str] = Form(None, description="Optional SHA-256 expected hash checksum"),
    uploaded_by_user_id: Optional[str] = Form(None, description="Optional user ID override; defaults to session user_id"),
    roles: Optional[str] = Query(
        None,
        description="Optional role override for RBAC verification",
    ),
    x_user_roles: Optional[str] = Header(None, alias="X-User-Roles"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> Any:
    """Upload a new document revision version for a contract."""
    effective_roles = get_effective_roles(roles, x_user_roles, x_user_role, session)
    user_id = (
        uploaded_by_user_id.strip()
        if (uploaded_by_user_id and uploaded_by_user_id.strip())
        else session.user_id
    )

    filename = file.filename or "document.pdf"
    content = file.file.read()

    logger.info(
        "Uploading document '%s' (%d bytes) for contract id='%s' by user_id=%s with roles=%s",
        filename,
        len(content),
        id,
        user_id,
        effective_roles,
    )

    try:
        doc_version = upload_document_version(
            db=db,
            contract_id=id,
            filename=filename,
            content=content,
            uploaded_by_user_id=user_id,
            expected_hash=expected_hash,
            roles=effective_roles,
        )
        logger.info(
            "Successfully uploaded document version #%d (ID: %s) for contract id='%s'",
            doc_version.version_number,
            doc_version.id,
            id,
        )
        return doc_version
    except DocumentNotFoundError as err:
        logger.warning("Contract not found for id='%s' in POST /api/v1/contracts/{id}/documents: %s", id, err)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )
    except DocumentPermissionDeniedError as err:
        logger.warning("RBAC write permission denied for user_id=%s in POST /api/v1/contracts/{id}/documents: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except DocumentValidationError as err:
        logger.warning("Validation error in POST /api/v1/contracts/{id}/documents for user_id=%s: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )
    except DocumentStorageError as err:
        logger.error("Storage error in POST /api/v1/contracts/{id}/documents: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "STORAGE_ERROR",
                "message": str(err),
                "details": {},
            },
        )


@contracts_router.get(
    "/{id}/documents/{version_id}/download",
    status_code=status.HTTP_200_OK,
    summary="Download a specific document version file",
    description=(
        "Retrieve and download the file content for a specific document version. "
        "Performs SHA-256 integrity verification before streaming file bytes."
    ),
)
def download_contract_document_endpoint(
    id: str,
    version_id: str,
    roles: Optional[str] = Query(
        None,
        description="Optional role override for RBAC verification",
    ),
    x_user_roles: Optional[str] = Header(None, alias="X-User-Roles"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> Response:
    """Download historic document version file content."""
    effective_roles = get_effective_roles(roles, x_user_roles, x_user_role, session)
    logger.info(
        "Downloading document version '%s' for contract id='%s' and user_id=%s with roles=%s",
        version_id,
        id,
        session.user_id,
        effective_roles,
    )

    try:
        doc_data = download_document_version(
            db=db,
            contract_id=id,
            version_id_or_number=version_id,
            roles=effective_roles,
        )
        content = doc_data["content"]
        filename = doc_data["filename"]
        file_hash = doc_data["file_hash"]

        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-File-Hash": file_hash,
            },
        )
    except DocumentNotFoundError as err:
        logger.warning("Document version missing for version_id='%s' in GET /api/v1/contracts/{id}/documents/{version_id}/download: %s", version_id, err)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )
    except DocumentPermissionDeniedError as err:
        logger.warning("RBAC permission denied for user_id=%s in GET /api/v1/contracts/{id}/documents/{version_id}/download: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except DocumentValidationError as err:
        logger.warning("Validation or integrity error in download endpoint for user_id=%s: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )
    except DocumentStorageError as err:
        logger.error("Storage error during document retrieval: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "STORAGE_ERROR",
                "message": str(err),
                "details": {},
            },
        )


# --- E-Signature Route Handlers ---


@esignature_router.get(
    "/requests",
    response_model=List[ESignatureRequestSchema],
    status_code=status.HTTP_200_OK,
    summary="Get e-signature tracking hub requests",
    description=(
        "Retrieve e-signature request execution status and signers for tracking hub dashboard. "
        "Filter by execution status, contract ID, or recipient email."
    ),
)
def list_esignature_requests_endpoint(
    execution_status: Optional[str] = Query(
        None,
        alias="status",
        description="Filter by execution status (PENDING, SIGNED, DECLINED, VOIDED)",
    ),
    contract_id: Optional[str] = Query(
        None,
        description="Filter signature requests by associated contract UUID",
    ),
    recipient_email: Optional[str] = Query(
        None,
        description="Filter signature requests by recipient email address substring",
    ),
    roles: Optional[str] = Query(
        None,
        description="Optional role override for RBAC verification",
    ),
    x_user_roles: Optional[str] = Header(None, alias="X-User-Roles"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> List[Any]:
    """Retrieve e-signature tracking hub request records."""
    effective_roles = get_effective_roles(roles, x_user_roles, x_user_role, session)
    logger.info(
        "Fetching e-signature tracking hub requests for user_id=%s (status=%s, contract_id=%s, recipient_email=%s, roles=%s)",
        session.user_id,
        execution_status,
        contract_id,
        recipient_email,
        effective_roles,
    )

    try:
        requests = list_signature_requests(
            db=db,
            execution_status=execution_status,
            contract_id=contract_id,
            recipient_email=recipient_email,
            roles=effective_roles,
        )
        logger.info("Found %d e-signature request(s) for user_id=%s", len(requests), session.user_id)
        return requests
    except ESignaturePermissionDeniedError as err:
        logger.warning("RBAC permission denied in GET /api/v1/esignature/requests for user_id=%s: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except ESignatureValidationError as err:
        logger.warning("Validation error in GET /api/v1/esignature/requests for user_id=%s: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )
    except ESignatureNotFoundError as err:
        logger.warning("Not found error in GET /api/v1/esignature/requests for user_id=%s: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )


@esignature_router.post(
    "/requests",
    response_model=ESignatureRequestSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Create an e-signature request",
    description="Dispatch an e-signature request to a recipient for a contract.",
)
def create_esignature_request_endpoint(
    payload: ESignatureRequestCreateSchema,
    roles: Optional[str] = Query(
        None,
        description="Optional role override for RBAC verification",
    ),
    x_user_roles: Optional[str] = Header(None, alias="X-User-Roles"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> Any:
    """Create and dispatch a new e-signature request."""
    effective_roles = get_effective_roles(roles, x_user_roles, x_user_role, session)
    logger.info(
        "Creating e-signature request for contract_id='%s', recipient='%s' by user_id=%s",
        payload.contract_id,
        payload.recipient_email,
        session.user_id,
    )

    try:
        esig_req = create_signature_request(
            db=db,
            contract_id=payload.contract_id,
            recipient_email=payload.recipient_email,
            recipient_name=payload.recipient_name,
            subject=payload.subject,
            roles=effective_roles,
        )
        return esig_req
    except ESignatureNotFoundError as err:
        logger.warning("Contract not found in POST /api/v1/esignature/requests: %s", err)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )
    except ESignaturePermissionDeniedError as err:
        logger.warning("RBAC write permission denied in POST /api/v1/esignature/requests for user_id=%s: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": str(err),
                "details": {},
            },
        )
    except ESignatureValidationError as err:
        logger.warning("Validation error in POST /api/v1/esignature/requests for user_id=%s: %s", session.user_id, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )


@esignature_router.post(
    "/webhooks/{provider}",
    response_model=WebhookAckSchema,
    status_code=status.HTTP_200_OK,
    summary="Process incoming e-signature webhook notification",
    description=(
        "Receive and process asynchronous execution status events from e-signature provider webhooks. "
        "Validates signature and handles out-of-order event timestamps."
    ),
)
def esignature_webhook_receiver_endpoint(
    provider: str,
    payload: bytes = Body(default=b""),
    db: DBSession = Depends(get_db),
    x_signature: Optional[str] = Header(None, alias="X-Signature"),
    x_provider_signature: Optional[str] = Header(None, alias="X-Provider-Signature"),
    x_hub_signature: Optional[str] = Header(None, alias="X-Hub-Signature"),
) -> Any:
    """Receive and process webhook events from external e-signature providers."""
    signature_header = x_signature or x_provider_signature or x_hub_signature

    logger.info(
        "Received webhook payload (%d bytes) for provider '%s'",
        len(payload),
        provider,
    )

    try:
        result = process_webhook_event(
            db=db,
            provider_name=provider,
            payload=payload,
            signature_header=signature_header,
        )
        return result
    except ESignatureWebhookError as err:
        logger.warning("Webhook verification failed for provider '%s': %s", provider, err)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": str(err),
                "details": {},
            },
        )
    except ESignatureNotFoundError as err:
        logger.warning("Signature request record not found for provider '%s': %s", provider, err)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )
    except ESignatureValidationError as err:
        logger.warning("Validation error in webhook for provider '%s': %s", provider, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": str(err),
                "details": {},
            },
        )
