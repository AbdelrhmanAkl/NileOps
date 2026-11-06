"""Links between portal logins, bank customers and their KYC applications.

Kept in separate tables so existing databases only need ``create_all`` to gain them.
Ownership rule: a customer user may only ever touch rows reachable through these links.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base
from src.models.user import utc_now


class CustomerAccount(Base):
    """One portal login <-> one bank customer."""

    __tablename__ = "portal_customer_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class CustomerKycLink(Base):
    """A KYC application submitted by a portal customer."""

    __tablename__ = "portal_kyc_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("kyc_applications.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True, nullable=False
    )
