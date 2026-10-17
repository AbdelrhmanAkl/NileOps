from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.session import Base


def utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


class ApplicationStatus(str, Enum):
    PENDING = "قيد المراجعة"
    IN_REVIEW = "تحت المراجعة"
    NEEDS_INFORMATION = "مطلوب استكمال بيانات"
    APPROVED = "مكتمل وفق قواعد التحقق"
    REJECTED = "غير مستوفٍ لقواعد التحقق"


class DocumentStatus(str, Enum):
    MISSING = "غير مرفق"
    UPLOADED = "تم الإرفاق"
    UNDER_REVIEW = "قيد الفحص"
    VALID = "مستوفٍ مبدئيًا"
    INVALID = "يحتاج إلى مراجعة"
    EXPIRED = "منتهي الصلاحية"


class ValidationSeverity(str, Enum):
    INFO = "معلومات"
    WARNING = "تحذير"
    ERROR = "خطأ"


class KYCApplication(Base):
    """Synthetic customer onboarding and KYC review application."""

    __tablename__ = "kyc_applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    application_number: Mapped[str] = mapped_column(
        String(30), unique=True, index=True, nullable=False
    )
    customer_name: Mapped[str] = mapped_column(String(150), nullable=False)
    national_id_last_four: Mapped[str | None] = mapped_column(
        String(4), nullable=True
    )
    application_type: Mapped[str] = mapped_column(
        String(80), default="فتح حساب جديد", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default=ApplicationStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    branch_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    documents: Mapped[list["KYCDocument"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
    )
    validation_results: Mapped[list["ValidationResult"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
    )


class KYCDocument(Base):
    """Metadata for a required or uploaded KYC document."""

    __tablename__ = "kyc_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("kyc_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default=DocumentStatus.MISSING.value, nullable=False
    )
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    application: Mapped["KYCApplication"] = relationship(
        back_populates="documents"
    )


class ValidationResult(Base):
    """Deterministic validation finding for an application."""

    __tablename__ = "validation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("kyc_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_code: Mapped[str] = mapped_column(String(100), nullable=False)
    title_ar: Mapped[str] = mapped_column(String(200), nullable=False)
    details_ar: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(
        String(30), default=ValidationSeverity.INFO.value, nullable=False
    )
    passed: Mapped[bool] = mapped_column(default=False, nullable=False)
    evidence: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    application: Mapped["KYCApplication"] = relationship(
        back_populates="validation_results"
    )


class AuditEvent(Base):
    """Audit trail entry for a human or system action."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("kyc_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_type: Mapped[str] = mapped_column(
        String(30), default="system", nullable=False
    )
    actor_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    details_ar: Mapped[str] = mapped_column(Text, nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    application: Mapped["KYCApplication"] = relationship(
        back_populates="audit_events"
    )
