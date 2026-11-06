
"""Banking operations API for simulated account opening workflows."""

from datetime import datetime
from typing import Literal

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.core.auth import require_roles
from src.db.session import get_db
from src.models.banking import (
    AccountOpeningRequest,
    BankAccount,
    BankingAuditEvent,
    CardIssuanceRequest,
    Customer,
)
from src.models.kyc import ApplicationStatus, KYCApplication
from src.models.portal import CustomerKycLink
from src.models.user import User


router = APIRouter(
    prefix="/api/v1/banking",
    tags=["العمليات البنكية المحاكية"],
)


class AccountOpeningItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_number: str
    customer_id: int
    account_type: str
    status: str
    branch_name: str | None
    review_notes: str | None
    simulation_only: bool
    created_at: datetime
    updated_at: datetime


class AccountOpeningListResponse(BaseModel):
    message: str
    total: int
    page: int
    page_size: int
    items: list[AccountOpeningItem]


class AccountOpeningDetail(AccountOpeningItem):
    customer_name: str
    customer_ref: str
    kyc_approved: bool = False


def customer_has_approved_kyc(db: Session, customer_id: int) -> bool:
    """True when a human reviewer has approved a KYC application linked to this customer.

    Identity verification (KYC) must come before opening an account, as in a real bank.
    """
    return (
        db.scalar(
            select(func.count())
            .select_from(CustomerKycLink)
            .join(KYCApplication, KYCApplication.id == CustomerKycLink.application_id)
            .where(
                CustomerKycLink.customer_id == customer_id,
                KYCApplication.status == ApplicationStatus.APPROVED.value,
            )
        )
        or 0
    ) > 0


class AccountOpeningReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal[
        "under_review",
        "needs_information",
        "approved",
        "rejected",
    ]
    notes: str = Field(min_length=3, max_length=2000)


class AccountOpeningReviewResponse(BaseModel):
    message: str
    request_number: str
    previous_status: str
    new_status: str
    reviewer_name: str
    audit_event_id: int
    simulation_only: bool


ALLOWED_ACCOUNT_TRANSITIONS = {
    "pending": {"under_review", "needs_information"},
    "under_review": {"needs_information", "approved", "rejected"},
    "needs_information": {"under_review"},
}


@router.get(
    "/account-opening",
    response_model=AccountOpeningListResponse,
    summary="عرض طلبات فتح الحساب المحاكية",
)
def list_account_opening_requests(
    q: str | None = Query(default=None, max_length=150),
    status: str | None = Query(default=None, max_length=30),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(
        require_roles("viewer", "reviewer", "admin")
    ),
    db: Session = Depends(get_db),
) -> AccountOpeningListResponse:
    statement = (
        select(AccountOpeningRequest)
        .join(Customer)
    )

    if q and q.strip():
        search_term = q.strip()
        statement = statement.where(
            or_(
                AccountOpeningRequest.request_number.contains(search_term),
                Customer.customer_ref.contains(search_term),
                Customer.full_name_ar.contains(search_term),
            )
        )

    if status:
        statement = statement.where(
            AccountOpeningRequest.status == status
        )

    total = db.scalar(
        select(func.count()).select_from(
            statement.order_by(None).subquery()
        )
    ) or 0

    requests = db.scalars(
        statement.order_by(AccountOpeningRequest.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return AccountOpeningListResponse(
        message="تم استرجاع طلبات فتح الحساب المحاكية.",
        total=total,
        page=page,
        page_size=page_size,
        items=[
            AccountOpeningItem.model_validate(item)
            for item in requests
        ],
    )


@router.get(
    "/account-opening/{request_id}",
    response_model=AccountOpeningDetail,
    summary="عرض تفاصيل طلب فتح حساب",
)
def get_account_opening_request(
    request_id: int,
    current_user: User = Depends(
        require_roles("viewer", "reviewer", "admin")
    ),
    db: Session = Depends(get_db),
) -> AccountOpeningDetail:
    result = db.execute(
        select(AccountOpeningRequest, Customer)
        .join(
            Customer,
            AccountOpeningRequest.customer_id == Customer.id,
        )
        .where(AccountOpeningRequest.id == request_id)
    ).first()

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="طلب فتح الحساب غير موجود.",
        )

    request, customer = result

    response_data = AccountOpeningItem.model_validate(
        request
    ).model_dump()

    response_data["customer_name"] = customer.full_name_ar
    response_data["customer_ref"] = customer.customer_ref
    response_data["kyc_approved"] = customer_has_approved_kyc(db, customer.id)

    return AccountOpeningDetail.model_validate(response_data)


@router.post(
    "/account-opening/{request_id}/review",
    response_model=AccountOpeningReviewResponse,
    summary="مراجعة طلب فتح حساب وتسجيل القرار البشري",
)
def review_account_opening_request(
    request_id: int,
    payload: AccountOpeningReviewRequest,
    current_user: User = Depends(
        require_roles("reviewer", "admin")
    ),
    db: Session = Depends(get_db),
) -> AccountOpeningReviewResponse:
    request = db.scalar(
        select(AccountOpeningRequest)
        .where(AccountOpeningRequest.id == request_id)
        .with_for_update()
    )

    if request is None:
        raise HTTPException(
            status_code=404,
            detail="طلب فتح الحساب غير موجود.",
        )

    previous_status = request.status
    new_status = payload.decision

    if new_status not in ALLOWED_ACCOUNT_TRANSITIONS.get(
        previous_status, set()
    ):
        raise HTTPException(
            status_code=409,
            detail="انتقال حالة طلب فتح الحساب غير مسموح به.",
        )

    if new_status == "approved" and not customer_has_approved_kyc(db, request.customer_id):
        raise HTTPException(
            status_code=409,
            detail="لا يمكن اعتماد فتح الحساب قبل اعتماد طلب التحقق من هوية العميل (KYC).",
        )

    try:
        request.status = new_status
        request.review_notes = payload.notes.strip()

        opened_account = None
        if new_status == "approved":
            # Simulation: an approved opening request creates the (simulated) active account,
            # so the customer can then request a card for it.
            opened_account = BankAccount(
                account_ref=f"TMP-{uuid4().hex[:20]}",
                customer_id=request.customer_id,
                account_type=request.account_type,
                status="active",
                simulation_only=True,
            )
            db.add(opened_account)
            db.flush()
            opened_account.account_ref = f"SIM-ACC-{opened_account.id:06d}"

        audit_event = BankingAuditEvent(
            entity_type="account_opening_request",
            entity_id=request.id,
            action="HUMAN_REVIEW",
            actor_role=current_user.role,
            actor_username=current_user.username,
            details_ar=(
                f"قرار المراجعة: {new_status}. "
                f"ملاحظات المراجع: {payload.notes.strip()}"
            ),
        )

        db.add(audit_event)
        db.flush()

        response = AccountOpeningReviewResponse(
            message="تم تسجيل قرار المراجعة المحاكي بنجاح.",
            request_number=request.request_number,
            previous_status=previous_status,
            new_status=new_status,
            reviewer_name=current_user.username,
            audit_event_id=audit_event.id,
            simulation_only=True,
        )

        db.commit()
        return response

    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="تعذر حفظ قرار المراجعة.",
        ) from exc



class CardIssuanceItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_number: str
    customer_id: int
    account_id: int | None
    card_type: str
    status: str
    review_notes: str | None
    simulation_only: bool
    created_at: datetime
    updated_at: datetime


class CardIssuanceListResponse(BaseModel):
    message: str
    total: int
    page: int
    page_size: int
    items: list[CardIssuanceItem]


class CardIssuanceDetail(CardIssuanceItem):
    customer_name: str
    customer_ref: str
    account_ref: str | None
    account_status: str | None


class CardIssuanceReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["under_review", "approved", "rejected", "cancelled"]
    notes: str = Field(min_length=3, max_length=2000)


class CardIssuanceActionResponse(BaseModel):
    message: str
    request_number: str
    previous_status: str
    new_status: str
    actor_username: str
    audit_event_id: int
    simulation_only: bool


ALLOWED_CARD_TRANSITIONS = {
    "pending": {"under_review", "cancelled"},
    "under_review": {"approved", "rejected", "cancelled"},
    "approved": {"issued", "cancelled"},
}


@router.get(
    "/card-issuance",
    response_model=CardIssuanceListResponse,
    summary="عرض طلبات إصدار البطاقات المحاكية",
)
def list_card_issuance_requests(
    q: str | None = Query(default=None, max_length=150),
    status: str | None = Query(default=None, max_length=30),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(
        require_roles("viewer", "reviewer", "admin")
    ),
    db: Session = Depends(get_db),
) -> CardIssuanceListResponse:
    statement = (
        select(CardIssuanceRequest)
        .join(
            Customer,
            CardIssuanceRequest.customer_id == Customer.id,
        )
    )

    if q and q.strip():
        search_term = q.strip()
        statement = statement.where(
            or_(
                CardIssuanceRequest.request_number.contains(search_term),
                Customer.customer_ref.contains(search_term),
                Customer.full_name_ar.contains(search_term),
            )
        )

    if status:
        statement = statement.where(
            CardIssuanceRequest.status == status
        )

    total = db.scalar(
        select(func.count()).select_from(
            statement.order_by(None).subquery()
        )
    ) or 0

    requests = db.scalars(
        statement.order_by(CardIssuanceRequest.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return CardIssuanceListResponse(
        message="تم استرجاع طلبات إصدار البطاقات.",
        total=total,
        page=page,
        page_size=page_size,
        items=[
            CardIssuanceItem.model_validate(item)
            for item in requests
        ],
    )


@router.get(
    "/card-issuance/{request_id}",
    response_model=CardIssuanceDetail,
    summary="عرض تفاصيل طلب إصدار بطاقة",
)
def get_card_issuance_request(
    request_id: int,
    current_user: User = Depends(
        require_roles("viewer", "reviewer", "admin")
    ),
    db: Session = Depends(get_db),
) -> CardIssuanceDetail:
    result = db.execute(
        select(CardIssuanceRequest, Customer, BankAccount)
        .join(
            Customer,
            CardIssuanceRequest.customer_id == Customer.id,
        )
        .outerjoin(
            BankAccount,
            CardIssuanceRequest.account_id == BankAccount.id,
        )
        .where(CardIssuanceRequest.id == request_id)
    ).first()

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="طلب إصدار البطاقة غير موجود.",
        )

    request, customer, account = result

    response_data = CardIssuanceItem.model_validate(
        request
    ).model_dump()

    response_data["customer_name"] = customer.full_name_ar
    response_data["customer_ref"] = customer.customer_ref
    response_data["account_ref"] = (
        account.account_ref if account else None
    )
    response_data["account_status"] = (
        account.status if account else None
    )

    return CardIssuanceDetail.model_validate(response_data)


@router.post(
    "/card-issuance/{request_id}/review",
    response_model=CardIssuanceActionResponse,
    summary="مراجعة طلب إصدار بطاقة وتسجيل القرار البشري",
)
def review_card_issuance_request(
    request_id: int,
    payload: CardIssuanceReviewRequest,
    current_user: User = Depends(
        require_roles("reviewer", "admin")
    ),
    db: Session = Depends(get_db),
) -> CardIssuanceActionResponse:
    request = db.scalar(
        select(CardIssuanceRequest)
        .where(CardIssuanceRequest.id == request_id)
        .with_for_update()
    )

    if request is None:
        raise HTTPException(
            status_code=404,
            detail="طلب إصدار البطاقة غير موجود.",
        )

    previous_status = request.status
    new_status = payload.decision

    if new_status not in ALLOWED_CARD_TRANSITIONS.get(
        previous_status, set()
    ):
        raise HTTPException(
            status_code=409,
            detail="انتقال حالة طلب البطاقة غير مسموح به.",
        )

    if new_status == "approved":
        account = db.scalar(
            select(BankAccount).where(
                BankAccount.id == request.account_id,
                BankAccount.customer_id == request.customer_id,
                BankAccount.status == "active",
            )
        )

        if account is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "لا يمكن اعتماد طلب البطاقة: "
                    "لا يوجد حساب نشط مرتبط بنفس العميل."
                ),
            )

    return _save_card_issuance_action(
        request=request,
        previous_status=previous_status,
        new_status=new_status,
        notes=payload.notes,
        current_user=current_user,
        db=db,
        action="HUMAN_REVIEW",
    )


@router.post(
    "/card-issuance/{request_id}/issue",
    response_model=CardIssuanceActionResponse,
    summary="تنفيذ إصدار بطاقة محاكى بعد الموافقة",
)
def issue_card(
    request_id: int,
    current_user: User = Depends(
        require_roles("reviewer", "admin")
    ),
    db: Session = Depends(get_db),
) -> CardIssuanceActionResponse:
    request = db.scalar(
        select(CardIssuanceRequest)
        .where(CardIssuanceRequest.id == request_id)
        .with_for_update()
    )

    if request is None:
        raise HTTPException(
            status_code=404,
            detail="طلب إصدار البطاقة غير موجود.",
        )

    previous_status = request.status
    new_status = "issued"

    if new_status not in ALLOWED_CARD_TRANSITIONS.get(
        previous_status, set()
    ):
        raise HTTPException(
            status_code=409,
            detail="يجب اعتماد طلب البطاقة قبل إصدارها.",
        )

    account = db.scalar(
        select(BankAccount).where(
            BankAccount.id == request.account_id,
            BankAccount.customer_id == request.customer_id,
            BankAccount.status == "active",
        )
    )

    if account is None:
        raise HTTPException(
            status_code=409,
            detail="الحساب المرتبط غير نشط أو لا يخص العميل.",
        )

    return _save_card_issuance_action(
        request=request,
        previous_status=previous_status,
        new_status=new_status,
        notes="تم تنفيذ إصدار بطاقة محاكى بعد الموافقة البشرية.",
        current_user=current_user,
        db=db,
        action="SIMULATED_CARD_ISSUANCE",
    )


def _save_card_issuance_action(
    request: CardIssuanceRequest,
    previous_status: str,
    new_status: str,
    notes: str,
    current_user: User,
    db: Session,
    action: str,
) -> CardIssuanceActionResponse:
    try:
        request.status = new_status
        request.review_notes = notes.strip()

        audit_event = BankingAuditEvent(
            entity_type="card_issuance_request",
            entity_id=request.id,
            action=action,
            actor_role=current_user.role,
            actor_username=current_user.username,
            details_ar=(
                f"الحالة السابقة: {previous_status}. "
                f"الحالة الجديدة: {new_status}. "
                f"التفاصيل: {notes.strip()}"
            ),
        )

        db.add(audit_event)
        db.flush()

        response = CardIssuanceActionResponse(
            message="تم تسجيل العملية المحاكية بنجاح.",
            request_number=request.request_number,
            previous_status=previous_status,
            new_status=new_status,
            actor_username=current_user.username,
            audit_event_id=audit_event.id,
            simulation_only=True,
        )

        db.commit()
        return response

    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="تعذر حفظ عملية إصدار البطاقة.",
        ) from exc
