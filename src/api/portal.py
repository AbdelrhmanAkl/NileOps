"""Customer portal API: a bank customer sees and creates only their own items.

Isolation rules (all tested):
* every endpoint requires the ``customer`` role; staff roles get 403 here, and customers get 403
  on every staff endpoint (those list their allowed staff roles explicitly);
* every query is filtered through the caller's own customer id; other customers' ids answer 404;
* responses never carry internal notes, routing reasons, suggested replies, staff names or audit details.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.api.complaints import register_complaint
from src.api.documents import UPLOADABLE_STATUSES, UploadResponse, save_document_upload
from src.api.banking import customer_has_active_account
from src.core.auth import require_customer
from src.core.config import get_settings
from src.core.passwords import validate_password
from src.core.security import hash_password
from src.core.throttle import register_throttle
from src.db.session import get_db
from src.models.banking import (
    AccountOpeningRequest,
    BankAccount,
    BankingAuditEvent,
    CardIssuanceRequest,
    Complaint,
    Customer,
)
from src.models.kyc import ApplicationStatus, AuditEvent, DocumentStatus, KYCApplication, KYCDocument
from src.models.portal import CustomerAccount, CustomerKycLink
from src.models.user import User
from src.services.kyc_validator import REQUIRED_DOCUMENTS

router = APIRouter(prefix="/api/v1/portal", tags=["بوابة العميل"])

_USERNAME_HELP = "اسم المستخدم: 3 إلى 50 حرفًا إنجليزيًا أو أرقامًا أو (_ . -) ويبدأ بحرف أو رقم."
MAX_OPEN_REQUESTS = 3
MAX_OPEN_COMPLAINTS = 10
MAX_OPEN_KYC = 2

ACCOUNT_TEXT = {
    "pending": ("تم استلام طلبك", "طلبك بانتظار بدء المراجعة."),
    "under_review": ("قيد المراجعة", "يراجع موظف البنك طلبك الآن."),
    "needs_information": ("مطلوب استكمال", "يحتاج البنك إلى معلومات إضافية؛ سيتواصل معك موظف."),
    "approved": ("تمت الموافقة", "تمت الموافقة على طلبك (محاكاة)."),
    "rejected": ("لم تتم الموافقة", "لم تتم الموافقة على الطلب. يمكنك التواصل مع الفرع."),
    "cancelled": ("ملغى", "تم إلغاء الطلب."),
}
CARD_TEXT = {
    **ACCOUNT_TEXT,
    "approved": ("تمت الموافقة", "تمت الموافقة على طلب البطاقة وبانتظار الإصدار (محاكاة)."),
    "issued": ("تم الإصدار", "تم إصدار البطاقة (محاكاة)."),
}
COMPLAINT_TEXT = {
    "new": ("تم الاستلام", "تم استلام شكواك."),
    "classified": ("بانتظار التوجيه", "شكواك بانتظار توجيهها إلى القسم المختص."),
    "assigned": ("تم التوجيه", "تم توجيه شكواك إلى القسم المختص."),
    "in_progress": ("قيد المعالجة", "يعمل القسم المختص على شكواك."),
    "resolved": ("تم الحل", "تم حل الشكوى. يمكنك التواصل معنا إن لم تكن راضيًا."),
    "closed": ("مغلقة", "تم إغلاق الشكوى."),
}
KYC_TEXT = {
    ApplicationStatus.PENDING.value: ("تم الاستلام", "طلبك بانتظار المراجعة. ارفع المستندات المطلوبة."),
    ApplicationStatus.IN_REVIEW.value: ("قيد المراجعة", "يراجع موظف البنك مستنداتك."),
    ApplicationStatus.NEEDS_INFORMATION.value: ("مطلوب استكمال", "يرجى استكمال أو استبدال المستندات الموضحة."),
    ApplicationStatus.APPROVED.value: ("مكتمل", "اكتملت مراجعة بياناتك."),
    ApplicationStatus.REJECTED.value: ("غير مستوفٍ", "لم تستوفِ البيانات المتطلبات. تواصل مع الفرع."),
}
DOC_TEXT = {
    DocumentStatus.MISSING.value: "مطلوب رفعه",
    DocumentStatus.UPLOADED.value: "تم الرفع — بانتظار المراجعة",
    DocumentStatus.UNDER_REVIEW.value: "قيد الفحص",
    DocumentStatus.VALID.value: "مقبول",
    DocumentStatus.INVALID.value: "غير مقبول — يرجى رفع مستند أوضح",
    DocumentStatus.EXPIRED.value: "منتهي الصلاحية — يرجى رفع مستند ساري",
}


def _text(table: dict, status: str) -> tuple[str, str]:
    return table.get(status, (status, ""))


def create_customer_profile(db: Session, user: User, full_name: str) -> Customer:
    """Create the bank customer and link it to a (just flushed) customer login."""
    customer = Customer(customer_ref=f"TMP-{uuid4().hex[:20]}", full_name_ar=full_name.strip())
    db.add(customer)
    db.flush()
    customer.customer_ref = f"CUS-{customer.id:06d}"
    db.add(CustomerAccount(user_id=user.id, customer_id=customer.id))
    db.flush()
    return customer


def current_customer(
    user: User = Depends(require_customer), db: Session = Depends(get_db)
) -> tuple[User, Customer]:
    link = db.scalar(select(CustomerAccount).where(CustomerAccount.user_id == user.id))
    customer = db.get(Customer, link.customer_id) if link else None
    if customer is None:
        raise HTTPException(status_code=403, detail="حسابك غير مرتبط بملف عميل. تواصل مع البنك.")
    return user, customer


def _audit(db: Session, entity_type: str, entity_id: int, action: str, user: User, details: str) -> None:
    db.add(
        BankingAuditEvent(
            entity_type=entity_type, entity_id=entity_id, action=action,
            actor_role=user.role, actor_username=user.username, details_ar=details,
        )
    )


# ------------------------------------------------------------------ register
class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=3, max_length=100)
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class RegisterResponse(BaseModel):
    message: str
    username: str


@router.post("/register", response_model=RegisterResponse, status_code=201, summary="إنشاء حساب عميل جديد (تسجيل ذاتي)")
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> RegisterResponse:
    import re

    if not get_settings().allow_self_registration:
        raise HTTPException(status_code=403, detail="التسجيل الذاتي غير متاح حاليًا. تواصل مع البنك لإنشاء حسابك.")
    address = request.client.host if request.client else "unknown"
    wait = register_throttle.retry_after("register", address)
    if wait:
        raise HTTPException(status_code=429, detail="تم إنشاء عدد كبير من الحسابات من هذا الجهاز. حاول لاحقًا.", headers={"Retry-After": str(wait)})
    username = payload.username.strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,49}$", username):
        raise HTTPException(status_code=422, detail=_USERNAME_HELP)
    try:
        validate_password(payload.password, username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if db.scalar(select(User).where(func.lower(User.username) == username.lower())):
        raise HTTPException(status_code=409, detail="اسم المستخدم مستخدم من قبل.")
    try:
        user = User(username=username, password_hash=hash_password(payload.password), role="customer", is_active=True)
        db.add(user)
        db.flush()
        customer = create_customer_profile(db, user, payload.full_name)
        _audit(db, "customer", customer.id, "CUSTOMER_REGISTERED", user, "إنشاء حساب عميل بالتسجيل الذاتي.")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="اسم المستخدم مستخدم من قبل.") from exc
    register_throttle.register_failure("register", address)  # counts every successful registration
    return RegisterResponse(message="تم إنشاء حسابك. يمكنك تسجيل الدخول الآن.", username=username)


# ------------------------------------------------------------------ overview
class StatusView(BaseModel):
    title: str
    message: str


class RequestItem(BaseModel):
    id: int
    number: str
    kind: str
    detail: str
    status: str
    status_title: str
    status_message: str
    created_at: datetime
    updated_at: datetime | None = None


class AccountItem(BaseModel):
    id: int
    account_ref: str
    account_type: str
    status: str
    currency: str


class Overview(BaseModel):
    customer_name: str
    customer_ref: str
    accounts: list[AccountItem]
    account_requests: list[RequestItem]
    card_requests: list[RequestItem]
    complaints: list[RequestItem]
    kyc_applications: list[RequestItem]
    simulation_only: bool = True


def _account_item(r: AccountOpeningRequest) -> RequestItem:
    title, message = _text(ACCOUNT_TEXT, r.status)
    kind = {"savings": "حساب توفير", "current": "حساب جاري"}.get(r.account_type, r.account_type)
    return RequestItem(id=r.id, number=r.request_number, kind="account", detail=kind, status=r.status,
                       status_title=title, status_message=message, created_at=r.created_at, updated_at=r.updated_at)


def _card_item(r: CardIssuanceRequest) -> RequestItem:
    title, message = _text(CARD_TEXT, r.status)
    kind = {"debit": "بطاقة خصم مباشر", "prepaid": "بطاقة مدفوعة مسبقًا"}.get(r.card_type, r.card_type)
    return RequestItem(id=r.id, number=r.request_number, kind="card", detail=kind, status=r.status,
                       status_title=title, status_message=message, created_at=r.created_at, updated_at=r.updated_at)


def _complaint_item(c: Complaint) -> RequestItem:
    title, message = _text(COMPLAINT_TEXT, c.status)
    return RequestItem(id=c.id, number=c.complaint_number, kind="complaint", detail=c.category, status=c.status,
                       status_title=title, status_message=message, created_at=c.created_at, updated_at=c.updated_at)


def _kyc_item(a: KYCApplication) -> RequestItem:
    title, message = _text(KYC_TEXT, a.status)
    return RequestItem(id=a.id, number=a.application_number, kind="kyc", detail=a.application_type, status=a.status,
                       status_title=title, status_message=message, created_at=a.created_at, updated_at=a.updated_at)


def _kyc_ids(db: Session, customer: Customer) -> list[int]:
    return list(db.scalars(select(CustomerKycLink.application_id).where(CustomerKycLink.customer_id == customer.id)))


@router.get("/overview", response_model=Overview, summary="ملخص حسابي وطلباتي")
def overview(ctx: tuple[User, Customer] = Depends(current_customer), db: Session = Depends(get_db)) -> Overview:
    _, c = ctx
    newest = lambda model: select(model).where(model.customer_id == c.id).order_by(model.id.desc()).limit(50)  # noqa: E731
    kyc = db.scalars(
        select(KYCApplication).where(KYCApplication.id.in_(_kyc_ids(db, c))).order_by(KYCApplication.id.desc())
    ).all()
    return Overview(
        customer_name=c.full_name_ar,
        customer_ref=c.customer_ref,
        accounts=[AccountItem(id=a.id, account_ref=a.account_ref, account_type=a.account_type, status=a.status, currency=a.currency)
                  for a in db.scalars(newest(BankAccount))],
        account_requests=[_account_item(r) for r in db.scalars(newest(AccountOpeningRequest))],
        card_requests=[_card_item(r) for r in db.scalars(newest(CardIssuanceRequest))],
        complaints=[_complaint_item(x) for x in db.scalars(newest(Complaint))],
        kyc_applications=[_kyc_item(a) for a in kyc],
    )


# -------------------------------------------------------------- new requests
def _open_count(db: Session, model, customer_id: int, closed: tuple[str, ...]) -> int:
    return db.scalar(
        select(func.count()).select_from(model).where(model.customer_id == customer_id, model.status.notin_(closed))
    ) or 0


class AccountRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_type: Literal["savings", "current"]
    branch_name: str | None = Field(default=None, max_length=100)


@router.post("/account-requests", response_model=RequestItem, status_code=201, summary="طلب فتح حساب (محاكاة)")
def new_account_request(payload: AccountRequestIn, ctx: tuple[User, Customer] = Depends(current_customer),
                        db: Session = Depends(get_db)) -> RequestItem:
    user, c = ctx
    if _open_count(db, AccountOpeningRequest, c.id, ("approved", "rejected", "cancelled")) >= MAX_OPEN_REQUESTS:
        raise HTTPException(status_code=409, detail="لديك طلبات فتح حساب مفتوحة بالفعل. انتظر نتيجتها أولًا.")
    if customer_has_active_account(db, c.id, payload.account_type):
        raise HTTPException(status_code=409, detail="لديك حساب نشط من هذا النوع بالفعل.")
    same_type_open = db.scalar(
        select(func.count()).select_from(AccountOpeningRequest).where(
            AccountOpeningRequest.customer_id == c.id,
            AccountOpeningRequest.account_type == payload.account_type,
            AccountOpeningRequest.status.notin_(("approved", "rejected", "cancelled")),
        )
    )
    if same_type_open:
        raise HTTPException(status_code=409, detail="لديك طلب مفتوح لنفس نوع الحساب بالفعل. انتظر نتيجته.")
    r = AccountOpeningRequest(request_number=f"TMP-{uuid4().hex[:20]}", customer_id=c.id, account_type=payload.account_type,
                              status="pending", branch_name=(payload.branch_name or "").strip() or None, simulation_only=True)
    db.add(r)
    db.flush()
    r.request_number = f"WEB-ACC-{r.id:06d}"
    _audit(db, "account_opening_request", r.id, "CUSTOMER_REQUEST_CREATED", user, "قدّم العميل طلب فتح حساب (محاكاة).")
    db.commit()
    db.refresh(r)
    return _account_item(r)


class CardRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_type: Literal["debit", "prepaid"]
    account_id: int = Field(description="حساب نشط يخص العميل")


@router.post("/card-requests", response_model=RequestItem, status_code=201, summary="طلب بطاقة (محاكاة)")
def new_card_request(payload: CardRequestIn, ctx: tuple[User, Customer] = Depends(current_customer),
                     db: Session = Depends(get_db)) -> RequestItem:
    user, c = ctx
    account = db.scalar(select(BankAccount).where(BankAccount.id == payload.account_id, BankAccount.customer_id == c.id))
    if account is None:
        raise HTTPException(status_code=404, detail="الحساب غير موجود.")
    if account.status != "active":
        raise HTTPException(status_code=409, detail="يمكن طلب بطاقة لحساب نشط فقط.")
    if _open_count(db, CardIssuanceRequest, c.id, ("rejected", "issued", "cancelled")) >= MAX_OPEN_REQUESTS:
        raise HTTPException(status_code=409, detail="لديك طلبات بطاقات مفتوحة بالفعل. انتظر نتيجتها أولًا.")
    r = CardIssuanceRequest(request_number=f"TMP-{uuid4().hex[:20]}", customer_id=c.id, account_id=payload.account_id,
                            card_type=payload.card_type, status="pending", simulation_only=True)
    db.add(r)
    db.flush()
    r.request_number = f"WEB-CARD-{r.id:06d}"
    _audit(db, "card_issuance_request", r.id, "CUSTOMER_REQUEST_CREATED", user, "قدّم العميل طلب بطاقة (محاكاة).")
    db.commit()
    db.refresh(r)
    return _card_item(r)


class ComplaintIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=10, max_length=2000)


@router.post("/complaints", response_model=RequestItem, status_code=201, summary="تقديم شكوى")
def new_complaint(payload: ComplaintIn, ctx: tuple[User, Customer] = Depends(current_customer),
                  db: Session = Depends(get_db)) -> RequestItem:
    user, c = ctx
    if _open_count(db, Complaint, c.id, ("resolved", "closed")) >= MAX_OPEN_COMPLAINTS:
        raise HTTPException(status_code=409, detail="لديك شكاوى مفتوحة كثيرة. انتظر معالجتها أولًا.")
    complaint, _ = register_complaint(db, payload.description, None, c.id, user)
    return _complaint_item(complaint)


class TimelineStep(BaseModel):
    title: str
    at: datetime | None
    done: bool


class ComplaintView(RequestItem):
    description: str
    timeline: list[TimelineStep]


@router.get("/complaints/{complaint_id}", response_model=ComplaintView, summary="تفاصيل شكوى من شكاواي")
def complaint_detail(complaint_id: int, ctx: tuple[User, Customer] = Depends(current_customer),
                     db: Session = Depends(get_db)) -> ComplaintView:
    _, c = ctx
    complaint = db.scalar(select(Complaint).where(Complaint.id == complaint_id, Complaint.customer_id == c.id))
    if complaint is None:
        raise HTTPException(status_code=404, detail="الشكوى غير موجودة.")
    events = db.scalars(
        select(BankingAuditEvent).where(BankingAuditEvent.entity_type == "complaint", BankingAuditEvent.entity_id == complaint.id)
        .order_by(BankingAuditEvent.id)
    ).all()
    first = events[0].created_at if events else complaint.created_at
    order = ["classified", "assigned", "in_progress", "resolved", "closed"]
    reached = order.index(complaint.status) if complaint.status in order else 0
    steps = [TimelineStep(title="تم استلام الشكوى", at=first, done=True)]
    for i, (key, title) in enumerate((("assigned", "تم توجيهها للقسم المختص"), ("in_progress", "قيد المعالجة"),
                                       ("resolved", "تم الحل"), ("closed", "أُغلقت"))):
        steps.append(TimelineStep(title=title, at=complaint.updated_at if reached >= order.index(key) and complaint.status == key else None,
                                  done=reached >= order.index(key)))
    return ComplaintView(**_complaint_item(complaint).model_dump(), description=complaint.description, timeline=steps)


# ---------------------------------------------------------------------- KYC
class KycDoc(BaseModel):
    id: int
    document_type: str
    status: str
    status_text: str
    can_upload: bool
    file_name: str | None


class KycView(RequestItem):
    documents: list[KycDoc]


def _owned_application(db: Session, customer: Customer, application_id: int) -> KYCApplication:
    link = db.scalar(select(CustomerKycLink).where(
        CustomerKycLink.application_id == application_id, CustomerKycLink.customer_id == customer.id))
    app = db.get(KYCApplication, application_id) if link else None
    if app is None:
        raise HTTPException(status_code=404, detail="الطلب غير موجود.")
    return app


def _kyc_view(app: KYCApplication) -> KycView:
    locked = app.status in (ApplicationStatus.APPROVED.value, ApplicationStatus.REJECTED.value)
    docs = [
        KycDoc(id=d.id, document_type=d.document_type, status=d.status, status_text=DOC_TEXT.get(d.status, d.status),
               can_upload=(d.status in UPLOADABLE_STATUSES) and not locked, file_name=d.file_name)
        for d in sorted(app.documents, key=lambda d: d.id)
    ]
    return KycView(**_kyc_item(app).model_dump(), documents=docs)


@router.post("/kyc-applications", response_model=KycView, status_code=201, summary="بدء طلب التحقق من بياناتي (اعرف عميلك)")
def new_kyc(ctx: tuple[User, Customer] = Depends(current_customer), db: Session = Depends(get_db)) -> KycView:
    user, c = ctx
    open_apps = db.scalar(
        select(func.count()).select_from(KYCApplication).where(
            KYCApplication.id.in_(_kyc_ids(db, c)),
            KYCApplication.status.notin_([ApplicationStatus.APPROVED.value, ApplicationStatus.REJECTED.value]),
        )
    ) or 0
    if open_apps >= MAX_OPEN_KYC:
        raise HTTPException(status_code=409, detail="لديك طلب تحقق مفتوح بالفعل. أكمله أولًا.")
    try:
        app = KYCApplication(application_number=f"TMP-{secrets.token_hex(8)}", customer_name=c.full_name_ar,
                             application_type="تحديث بيانات العميل", status=ApplicationStatus.PENDING.value)
        db.add(app)
        db.flush()
        app.application_number = f"KYC-WEB-{app.id:06d}"
        for doc_type in REQUIRED_DOCUMENTS:
            db.add(KYCDocument(application_id=app.id, document_type=doc_type, status=DocumentStatus.MISSING.value))
        db.add(CustomerKycLink(application_id=app.id, customer_id=c.id))
        db.add(AuditEvent(application_id=app.id, action="CUSTOMER_APPLICATION_CREATED", actor_type="customer",
                          actor_name=user.username, details_ar="بدأ العميل طلب تحقق جديدًا من البوابة.",
                          previous_status=None, new_status=app.status))
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="تعذر إنشاء الطلب.") from exc
    db.refresh(app)
    return _kyc_view(app)


@router.get("/kyc-applications/{application_id}", response_model=KycView, summary="تفاصيل طلب التحقق الخاص بي")
def kyc_detail(application_id: int, ctx: tuple[User, Customer] = Depends(current_customer),
               db: Session = Depends(get_db)) -> KycView:
    return _kyc_view(_owned_application(db, ctx[1], application_id))


@router.post("/kyc-applications/{application_id}/documents/{document_id}/files", response_model=UploadResponse,
             status_code=201, summary="رفع مستند لطلبي (PNG / JPEG / PDF)")
async def kyc_upload(application_id: int, document_id: int, file: UploadFile = File(...),
                     ctx: tuple[User, Customer] = Depends(current_customer), db: Session = Depends(get_db)) -> UploadResponse:
    user, c = ctx
    app = _owned_application(db, c, application_id)
    if app.status in (ApplicationStatus.APPROVED.value, ApplicationStatus.REJECTED.value):
        raise HTTPException(status_code=409, detail="اكتملت مراجعة هذا الطلب ولا يمكن رفع مستندات إضافية.")
    document = db.scalar(
        select(KYCDocument).where(KYCDocument.id == document_id, KYCDocument.application_id == app.id).with_for_update()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="المستند غير موجود.")
    return await save_document_upload(db, document, file, user.username, actor_type="customer")
