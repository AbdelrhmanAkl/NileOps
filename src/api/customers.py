"""Staff-side customer 360 view: one place for a customer's accounts, ledger, loans, requests and KYC state.

Read-only. Every profile view is written to the banking audit trail (who looked at which customer).
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.api.banking import customer_has_approved_kyc
from src.core.auth import require_roles
from src.db.session import get_db
from src.models.banking import (
    AccountOpeningRequest,
    AccountTransaction,
    BankAccount,
    BankingAuditEvent,
    Branch,
    CardIssuanceRequest,
    Complaint,
    Customer,
    Loan,
)
from src.models.portal import CustomerKycLink
from src.models.user import User

router = APIRouter(prefix="/api/v1/customers", tags=["العملاء"])


class CustomerRow(BaseModel):
    id: int
    customer_ref: str
    full_name: str
    branch_name: str | None
    governorate: str | None
    accounts_count: int
    kyc_state: str


class CustomerList(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[CustomerRow]


class AccountRow(BaseModel):
    id: int
    account_number: str | None
    account_type: str
    currency: str
    status: str
    balance: float
    branch_name: str | None


class TxRow(BaseModel):
    id: int
    posted_at: datetime
    account_number: str | None
    kind: str
    description: str
    amount: float
    currency: str
    balance_after: float


class LoanRow(BaseModel):
    id: int
    loan_ref: str
    loan_type: str
    principal_amount: float
    monthly_installment: float
    tenure_months: int
    status: str


class RequestRow(BaseModel):
    id: int
    kind: str
    number: str
    status: str
    detail: str | None
    created_at: datetime


class CustomerProfile(BaseModel):
    id: int
    customer_ref: str
    full_name: str
    branch_name: str | None
    governorate: str | None
    date_of_birth: date | None
    kyc_state: str
    totals_egp: float
    accounts: list[AccountRow]
    transactions: list[TxRow]
    loans: list[LoanRow]
    requests: list[RequestRow]
    simulation_only: bool = True


def kyc_state(db: Session, customer_id: int) -> str:
    """approved / pending / none, from the KYC applications linked to the customer."""
    if customer_has_approved_kyc(db, customer_id):
        return "approved"
    linked = db.scalar(select(func.count()).select_from(CustomerKycLink).where(CustomerKycLink.customer_id == customer_id))
    return "pending" if linked else "none"


@router.get("", response_model=CustomerList, summary="قائمة العملاء")
def list_customers(
    q: str | None = Query(default=None, max_length=150),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=15, ge=1, le=100),
    _: User = Depends(require_roles("viewer", "reviewer", "admin")),
    db: Session = Depends(get_db),
) -> CustomerList:
    statement = select(Customer)
    if q and q.strip():
        term = q.strip()
        statement = statement.where(or_(Customer.customer_ref.contains(term), Customer.full_name_ar.contains(term)))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.scalars(statement.order_by(Customer.id).offset((page - 1) * page_size).limit(page_size)).all()
    counts = dict(db.execute(
        select(BankAccount.customer_id, func.count()).where(BankAccount.customer_id.in_([c.id for c in rows]),
                                                            BankAccount.status == "active").group_by(BankAccount.customer_id)
    ).all()) if rows else {}
    return CustomerList(total=total, page=page, page_size=page_size, items=[
        CustomerRow(id=c.id, customer_ref=c.customer_ref, full_name=c.full_name_ar, branch_name=c.branch_name,
                    governorate=c.governorate, accounts_count=counts.get(c.id, 0), kyc_state=kyc_state(db, c.id))
        for c in rows])


@router.get("/{customer_id}", response_model=CustomerProfile, summary="ملف العميل (360)")
def customer_profile(
    customer_id: int,
    current_user: User = Depends(require_roles("viewer", "reviewer", "admin")),
    db: Session = Depends(get_db),
) -> CustomerProfile:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="العميل غير موجود.")
    accounts = db.scalars(select(BankAccount).where(BankAccount.customer_id == customer.id).order_by(BankAccount.id)).all()
    branches = {b.id: b.name_ar for b in db.scalars(select(Branch)).all()}
    by_id = {a.id: a for a in accounts}
    txs = db.scalars(
        select(AccountTransaction).where(AccountTransaction.account_id.in_(list(by_id) or [0]))
        .order_by(AccountTransaction.posted_at.desc(), AccountTransaction.id.desc()).limit(20)
    ).all() if by_id else []
    loans = db.scalars(select(Loan).where(Loan.customer_id == customer.id).order_by(Loan.id)).all()

    requests: list[RequestRow] = []
    for r in db.scalars(select(AccountOpeningRequest).where(AccountOpeningRequest.customer_id == customer.id)):
        requests.append(RequestRow(id=r.id, kind="account", number=r.request_number, status=r.status, detail=r.account_type, created_at=r.created_at))
    for r in db.scalars(select(CardIssuanceRequest).where(CardIssuanceRequest.customer_id == customer.id)):
        requests.append(RequestRow(id=r.id, kind="card", number=r.request_number, status=r.status, detail=r.card_type, created_at=r.created_at))
    for r in db.scalars(select(Complaint).where(Complaint.customer_id == customer.id)):
        requests.append(RequestRow(id=r.id, kind="complaint", number=r.complaint_number, status=r.status, detail=r.category, created_at=r.created_at))
    requests.sort(key=lambda r: r.created_at, reverse=True)

    db.add(BankingAuditEvent(
        entity_type="customer", entity_id=customer.id, action="CUSTOMER_PROFILE_VIEWED", actor_role=current_user.role,
        actor_username=current_user.username, details_ar="تم عرض ملف العميل.",
    ))
    db.commit()

    only_staff_pii = current_user.role in ("reviewer", "admin")
    return CustomerProfile(
        id=customer.id, customer_ref=customer.customer_ref, full_name=customer.full_name_ar, branch_name=customer.branch_name,
        governorate=customer.governorate, date_of_birth=customer.date_of_birth if only_staff_pii else None,
        kyc_state=kyc_state(db, customer.id),
        totals_egp=float(sum(a.balance for a in accounts if a.currency == "EGP" and a.status == "active")),
        accounts=[AccountRow(id=a.id, account_number=a.account_number, account_type=a.account_type, currency=a.currency,
                             status=a.status, balance=float(a.balance), branch_name=branches.get(a.branch_id)) for a in accounts],
        transactions=[TxRow(id=t.id, posted_at=t.posted_at, account_number=by_id[t.account_id].account_number, kind=t.kind,
                            description=t.description_ar, amount=float(t.amount), currency=t.currency, balance_after=float(t.balance_after))
                      for t in txs],
        loans=[LoanRow(id=x.id, loan_ref=x.loan_ref, loan_type=x.loan_type, principal_amount=float(x.principal_amount),
                       monthly_installment=float(x.monthly_installment), tenure_months=x.tenure_months, status=x.status) for x in loans],
        requests=requests[:15],
    )
