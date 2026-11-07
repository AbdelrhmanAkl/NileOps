"""Banking operations models for the NileOps simulation environment."""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Branch(Base):
    """A bank branch (synthetic)."""

    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    name_ar: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    governorate: Mapped[str] = mapped_column(String(60), nullable=False)


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
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    governorate: Mapped[str | None] = mapped_column(String(60), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
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
    account_number: Mapped[str | None] = mapped_column(
        String(34), unique=True, index=True, nullable=True
    )
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0.00"), nullable=False
    )
    simulation_only: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class AccountTransaction(Base):
    """One ledger line. `balance_after` is the account balance right after this line."""

    __tablename__ = "account_transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_transaction_amount_positive"),
        CheckConstraint(
            "kind IN ('deposit', 'salary', 'withdrawal', 'transfer_in', "
            "'transfer_out', 'purchase', 'fee', 'interest', 'loan_installment')",
            name="ck_transaction_kind",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("bank_accounts.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    description_ar: Mapped[str] = mapped_column(String(200), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class Loan(Base):
    __tablename__ = "loans"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'closed', 'overdue')", name="ck_loan_status"),
        CheckConstraint("principal_amount > 0 AND tenure_months > 0", name="ck_loan_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    loan_ref: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    loan_type: Mapped[str] = mapped_column(String(20), nullable=False)
    principal_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    annual_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    tenure_months: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_installment: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    simulation_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


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
