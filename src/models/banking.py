"""Banking operations models for the NileOps simulation environment."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_ref: Mapped[str] = mapped_column(
        String(30), unique=True, index=True, nullable=False
    )
    full_name_ar: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone_last_four: Mapped[str | None] = mapped_column(String(4), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class AccountOpeningRequest(Base):
    __tablename__ = "account_opening_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'under_review', 'needs_information', "
            "'approved', 'rejected')",
            name="ck_account_opening_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_number: Mapped[str] = mapped_column(
        String(40), unique=True, index=True, nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    account_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="pending", index=True, nullable=False
    )
    branch_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    simulation_only: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_ref: Mapped[str] = mapped_column(
        String(40), unique=True, index=True, nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    account_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="active", index=True, nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), default="EGP", nullable=False)
    simulation_only: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class CardIssuanceRequest(Base):
    __tablename__ = "card_issuance_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'under_review', 'approved', "
            "'rejected', 'issued', 'cancelled')",
            name="ck_card_issuance_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_number: Mapped[str] = mapped_column(
        String(40), unique=True, index=True, nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("bank_accounts.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    card_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="pending", index=True, nullable=False
    )
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    simulation_only: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class Complaint(Base):
    __tablename__ = "complaints"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'classified', 'assigned', 'in_progress', "
            "'resolved', 'closed')",
            name="ck_complaint_status",
        ),
        CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'urgent')",
            name="ck_complaint_priority",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_number: Mapped[str] = mapped_column(
        String(40), unique=True, index=True, nullable=False
    )
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True, nullable=True
    )
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(
        String(20), default="medium", index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), default="new", index=True, nullable=False
    )
    assigned_department: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    routing_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class BankingAuditEvent(Base):
    __tablename__ = "banking_audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_username: Mapped[str] = mapped_column(String(100), nullable=False)
    details_ar: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
