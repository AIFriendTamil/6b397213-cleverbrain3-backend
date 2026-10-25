"""SQLAlchemy ORM models for Contract Lifecycle Management and E-Signature Repository."""

import enum
import uuid
from datetime import date as date_type, datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class ContractStatus(str, enum.Enum):
    """Enumeration of contract status states."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    TERMINATED = "TERMINATED"


class ClauseType(str, enum.Enum):
    """Enumeration of clause highlight types."""

    CONFIDENTIALITY = "CONFIDENTIALITY"
    TERMINATION = "TERMINATION"
    INDEMNIFICATION = "INDEMNIFICATION"
    GOVERNING_LAW = "GOVERNING_LAW"
    SPECIAL_TERM = "SPECIAL_TERM"


class ESignatureExecutionStatus(str, enum.Enum):
    """Enumeration of e-signature request execution states."""

    PENDING = "PENDING"
    SIGNED = "SIGNED"
    DECLINED = "DECLINED"
    VOIDED = "VOIDED"


class RenewalWarningStage(str, enum.Enum):
    """Enumeration of renewal alert warning stages."""

    DAYS_90 = "DAYS_90"
    DAYS_60 = "DAYS_60"
    DAYS_30 = "DAYS_30"


class Contract(Base):
    """Contract ORM entity model mapping to the contracts table."""

    __tablename__ = "contracts"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    contract_number: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    counterparty: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    status: Mapped[ContractStatus] = mapped_column(
        Enum(ContractStatus, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ContractStatus.DRAFT,
        index=True,
    )
    effective_on: Mapped[date_type] = mapped_column(
        Date,
        nullable=False,
    )
    expires_on: Mapped[date_type] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )
    auto_renew: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    clauses: Mapped[List["ClauseHighlight"]] = relationship(
        "ClauseHighlight",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    sla_commitments: Mapped[List["SLACommitment"]] = relationship(
        "SLACommitment",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    document_versions: Mapped[List["DocumentVersion"]] = relationship(
        "DocumentVersion",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    amendment_logs: Mapped[List["AmendmentAuditLog"]] = relationship(
        "AmendmentAuditLog",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    esignature_requests: Mapped[List["ESignatureRequest"]] = relationship(
        "ESignatureRequest",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    renewal_notification_logs: Mapped[List["RenewalNotificationLog"]] = relationship(
        "RenewalNotificationLog",
        back_populates="contract",
        cascade="all, delete-orphan",
    )


class ClauseHighlight(Base):
    """ClauseHighlight ORM entity model mapping to the clause_highlights table."""

    __tablename__ = "clause_highlights"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    contract_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    clause_type: Mapped[ClauseType] = mapped_column(
        Enum(ClauseType, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    contract: Mapped["Contract"] = relationship(
        "Contract",
        back_populates="clauses",
    )


class SLACommitment(Base):
    """SLACommitment ORM entity model mapping to the sla_commitments table."""

    __tablename__ = "sla_commitments"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    contract_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    metric_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    target_value: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    penalty_terms: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    contract: Mapped["Contract"] = relationship(
        "Contract",
        back_populates="sla_commitments",
    )


class DocumentVersion(Base):
    """DocumentVersion ORM entity model mapping to the document_versions table."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "contract_id",
            "version_number",
            name="uq_document_versions_contract_version",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    contract_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    file_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    file_size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    uploaded_by_user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    contract: Mapped["Contract"] = relationship(
        "Contract",
        back_populates="document_versions",
    )


class AmendmentAuditLog(Base):
    """AmendmentAuditLog ORM entity model mapping to the amendment_audit_logs table."""

    __tablename__ = "amendment_audit_logs"
    __table_args__ = (
        UniqueConstraint(
            "contract_id",
            "amendment_number",
            name="uq_amendment_audit_logs_contract_amendment",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    contract_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amendment_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    integrity_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    contract: Mapped["Contract"] = relationship(
        "Contract",
        back_populates="amendment_logs",
    )


class ESignatureRequest(Base):
    """ESignatureRequest ORM entity model mapping to the esignature_requests table."""

    __tablename__ = "esignature_requests"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    contract_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_ref_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    execution_status: Mapped[ESignatureExecutionStatus] = mapped_column(
        Enum(ESignatureExecutionStatus, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ESignatureExecutionStatus.PENDING,
        index=True,
    )
    recipient_email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    recipient_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    status_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    contract: Mapped["Contract"] = relationship(
        "Contract",
        back_populates="esignature_requests",
    )


class RenewalNotificationLog(Base):
    """RenewalNotificationLog ORM entity model mapping to the renewal_notification_logs table."""

    __tablename__ = "renewal_notification_logs"
    __table_args__ = (
        UniqueConstraint(
            "contract_id",
            "warning_stage",
            "dispatched_at_date",
            name="uq_renewal_notification_log_contract_stage_date",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    contract_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    warning_stage: Mapped[RenewalWarningStage] = mapped_column(
        Enum(RenewalWarningStage, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    dispatched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    dispatched_at_date: Mapped[date_type] = mapped_column(
        Date,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).date(),
    )
    recipient_target: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    contract: Mapped["Contract"] = relationship(
        "Contract",
        back_populates="renewal_notification_logs",
    )
