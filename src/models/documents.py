"""Models for uploaded KYC document files and OCR-extracted fields."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base
from src.models.kyc import utc_now


class DocumentFile(Base):
    """Metadata of a stored upload. The file itself lives outside the database."""

    __tablename__ = "document_files"
    __table_args__ = (
        UniqueConstraint("document_id", "sha256", name="uq_document_file_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("kyc_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stored_name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    original_name: Mapped[str] = mapped_column(String(120), nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(100), nullable=False)
    ocr_status: Mapped[str] = mapped_column(
        String(30), default="not_run", nullable=False
    )
    ocr_engine: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ExtractedField(Base):
    """A single OCR-extracted field with confidence and source location."""

    __tablename__ = "extracted_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_file_id: Mapped[int] = mapped_column(
        ForeignKey("document_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_name: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    method: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
