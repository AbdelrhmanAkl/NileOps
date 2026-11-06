"""Complaints management API for the simulated NileOps environment.

Complaints are classified and routed with deterministic rules. Low-confidence
cases wait for human triage, and every state change is written to the banking
audit trail. Suggested replies are drafts only; nothing is sent to customers.
"""

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
from src.models.banking import BankingAuditEvent, Complaint, Customer
from src.models.user import User
from src.services.complaint_router import (
    CATEGORIES,
    DEPARTMENTS,
    classify_complaint,
)

router = APIRouter(
    prefix="/api/v1/banking/complaints",
    tags=["الشكاوى"],
)

ComplaintStatus = Literal[
    "new", "classified", "assigned", "in_progress", "resolved", "closed"
]
Priority = Literal["low", "medium", "high", "urgent"]
CategoryName = Literal[
    "بطاقات", "حسابات", "تحويلات", "خدمة العملاء", "احتيال وأمان"
]
DepartmentName = Literal[
    "إدارة البطاقات",
    "خدمة الحسابات",
    "عمليات التحويلات",
    "خدمة العملاء",
    "إدارة مكافحة الاحتيال",
]

# Statuses an employee may set through /status (assignment has its own route).
ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "assigned": {"in_progress"},
    "in_progress": {"resolved"},
    "resolved": {"closed", "in_progress"},
}
ASSIGNABLE_STATUSES = {"classified", "assigned"}

assert set(CATEGORIES) == set(CategoryName.__args__)
assert set(DEPARTMENTS) == set(DepartmentName.__args__)


class ComplaintCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int | None = Field(default=None, ge=1)
    description: str = Field(min_length=10, max_length=2000)
    category: CategoryName | None = None


class ComplaintItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    complaint_number: str
    customer_id: int | None
    category: str
    description: str
    priority: str
    status: str
    assigned_department: str | None
    routing_reason: str | None
    suggested_reply: str | None
    created_at: datetime
    updated_at: datetime


class ComplaintListResponse(BaseModel):
    message: str
    total: int
    page: int
    page_size: int
    items: list[ComplaintItem]


class ComplaintHistoryEntry(BaseModel):
    action: str
    actor_role: str
    actor_username: str
    details_ar: str
    created_at: datetime


class ComplaintDetail(ComplaintItem):
    customer_name: str | None
    customer_ref: str | None
    reply_is_draft: bool = True
    needs_human_triage: bool
    history: list[ComplaintHistoryEntry]


class ComplaintCreateResponse(BaseModel):
    message: str
    complaint: ComplaintItem
    needs_human_triage: bool
    simulation_only: bool = True


class ComplaintAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department: DepartmentName
    priority: Priority | None = None
    notes: str = Field(min_length=3, max_length=2000)


class ComplaintStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["in_progress", "resolved", "closed"]
    notes: str = Field(min_length=3, max_length=2000)


class ComplaintActionResponse(BaseModel):
    message: str
    complaint_number: str
    previous_status: str
    new_status: str
    assigned_department: str | None
    priority: str
    actor_username: str
    audit_event_id: int
    simulation_only: bool = True


class ComplaintSummaryResponse(BaseModel):
    total: int
    awaiting_triage: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    by_category: dict[str, int]
    by_department: dict[str, int]


def _audit(
    db: Session,
    complaint: Complaint,
    action: str,
    user: User | None,
    details: str,
) -> BankingAuditEvent:
    # Complaint text is deliberately not copied into the audit trail.
    event = BankingAuditEvent(
        entity_type="complaint",
        entity_id=complaint.id,
        action=action,
        actor_role=user.role if user else "system",
        actor_username=user.username if user else "complaint_router",
        details_ar=details,
    )
    db.add(event)
    db.flush()
    return event


def _get_for_update(db: Session, complaint_id: int) -> Complaint:
    complaint = db.scalar(
        select(Complaint).where(Complaint.id == complaint_id).with_for_update()
    )
    if complaint is None:
        raise HTTPException(status_code=404, detail="الشكوى غير موجودة.")
    return complaint


def _count_by(db: Session, column) -> dict[str, int]:
    rows = db.execute(select(column, func.count()).group_by(column)).all()
    return {str(key): count for key, count in rows if key is not None}


def register_complaint(
    db: Session, description: str, category: str | None, customer_id: int | None, user: User | None
):
    """Classify, route, save and audit a new complaint. Shared by staff entry and the customer portal."""
    result = classify_complaint(description, category)
    status = "classified" if result.needs_human_triage else "assigned"
    try:
        complaint = Complaint(
            complaint_number=f"TMP-{uuid4().hex}",
            customer_id=customer_id,
            category=result.category,
            description=description.strip(),
            priority=result.priority,
            status=status,
            assigned_department=result.department,
            routing_reason=result.routing_reason,
            suggested_reply=result.suggested_reply,
        )
        db.add(complaint)
        db.flush()
        complaint.complaint_number = f"SIM-COMP-{complaint.id:06d}"
        _audit(db, complaint, "COMPLAINT_CREATED", user, "تم تسجيل شكوى جديدة في بيئة المحاكاة.")
        routing_text = (
            "بانتظار فرز بشري" if result.needs_human_triage else f"تم التوجيه تلقائيًا إلى: {result.department}"
        )
        _audit(
            db, complaint, "AUTO_CLASSIFIED", None,
            f"التصنيف: {result.category}. الأولوية: {result.priority}. {routing_text}.",
        )
        db.commit()
        db.refresh(complaint)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="تعذر حفظ الشكوى.") from exc
    return complaint, result


@router.post(
    "",
    response_model=ComplaintCreateResponse,
    status_code=201,
    summary="تسجيل شكوى وتصنيفها وتوجيهها تلقائيًا",
)
def create_complaint(
    payload: ComplaintCreateRequest,
    current_user: User = Depends(require_roles("reviewer", "admin")),
    db: Session = Depends(get_db),
) -> ComplaintCreateResponse:
    if payload.customer_id is not None:
        if db.get(Customer, payload.customer_id) is None:
            raise HTTPException(status_code=404, detail="العميل غير موجود.")

    complaint, result = register_complaint(
        db, payload.description, payload.category, payload.customer_id, current_user
    )

    return ComplaintCreateResponse(
        message="تم تسجيل الشكوى بنجاح.",
        complaint=ComplaintItem.model_validate(complaint),
        needs_human_triage=result.needs_human_triage,
    )


@router.get(
    "/summary",
    response_model=ComplaintSummaryResponse,
    summary="ملخص أعداد الشكاوى للوحة التحكم",
)
def complaints_summary(
    current_user: User = Depends(require_roles("viewer", "reviewer", "admin")),
    db: Session = Depends(get_db),
) -> ComplaintSummaryResponse:
    total = db.scalar(select(func.count()).select_from(Complaint)) or 0
    awaiting = db.scalar(
        select(func.count())
        .select_from(Complaint)
        .where(Complaint.status == "classified")
    ) or 0
    return ComplaintSummaryResponse(
        total=total,
        awaiting_triage=awaiting,
        by_status=_count_by(db, Complaint.status),
        by_priority=_count_by(db, Complaint.priority),
        by_category=_count_by(db, Complaint.category),
        by_department=_count_by(db, Complaint.assigned_department),
    )


@router.get(
    "",
    response_model=ComplaintListResponse,
    summary="عرض الشكاوى مع البحث والتصفية",
)
def list_complaints(
    q: str | None = Query(default=None, max_length=150),
    status: ComplaintStatus | None = None,
    priority: Priority | None = None,
    category: str | None = Query(default=None, max_length=80),
    department: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_roles("viewer", "reviewer", "admin")),
    db: Session = Depends(get_db),
) -> ComplaintListResponse:
    statement = select(Complaint).outerjoin(
        Customer, Complaint.customer_id == Customer.id
    )

    if q and q.strip():
        term = q.strip()
        statement = statement.where(
            or_(
                Complaint.complaint_number.contains(term),
                Complaint.description.contains(term),
                Customer.customer_ref.contains(term),
                Customer.full_name_ar.contains(term),
            )
        )
    if status:
        statement = statement.where(Complaint.status == status)
    if priority:
        statement = statement.where(Complaint.priority == priority)
    if category:
        statement = statement.where(Complaint.category == category)
    if department:
        statement = statement.where(Complaint.assigned_department == department)

    total = db.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ) or 0
    items = db.scalars(
        statement.order_by(Complaint.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return ComplaintListResponse(
        message="تم استرجاع الشكاوى.",
        total=total,
        page=page,
        page_size=page_size,
        items=[ComplaintItem.model_validate(item) for item in items],
    )


@router.get(
    "/{complaint_id}",
    response_model=ComplaintDetail,
    summary="عرض تفاصيل شكوى مع سجل الإجراءات",
)
def get_complaint(
    complaint_id: int,
    current_user: User = Depends(require_roles("viewer", "reviewer", "admin")),
    db: Session = Depends(get_db),
) -> ComplaintDetail:
    complaint = db.get(Complaint, complaint_id)
    if complaint is None:
        raise HTTPException(status_code=404, detail="الشكوى غير موجودة.")

    customer = (
        db.get(Customer, complaint.customer_id) if complaint.customer_id else None
    )
    events = db.scalars(
        select(BankingAuditEvent)
        .where(
            BankingAuditEvent.entity_type == "complaint",
            BankingAuditEvent.entity_id == complaint.id,
        )
        .order_by(BankingAuditEvent.id.asc())
    ).all()

    data = ComplaintItem.model_validate(complaint).model_dump()
    return ComplaintDetail(
        **data,
        customer_name=customer.full_name_ar if customer else None,
        customer_ref=customer.customer_ref if customer else None,
        needs_human_triage=complaint.status == "classified",
        history=[
            ComplaintHistoryEntry(
                action=e.action,
                actor_role=e.actor_role,
                actor_username=e.actor_username,
                details_ar=e.details_ar,
                created_at=e.created_at,
            )
            for e in events
        ],
    )


@router.post(
    "/{complaint_id}/assign",
    response_model=ComplaintActionResponse,
    summary="تعيين الشكوى لقسم (فرز أو إعادة توجيه بشري)",
)
def assign_complaint(
    complaint_id: int,
    payload: ComplaintAssignRequest,
    current_user: User = Depends(require_roles("reviewer", "admin")),
    db: Session = Depends(get_db),
) -> ComplaintActionResponse:
    complaint = _get_for_update(db, complaint_id)

    if complaint.status not in ASSIGNABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="لا يمكن تغيير قسم الشكوى بعد بدء معالجتها.",
        )

    previous_status = complaint.status
    previous_department = complaint.assigned_department
    try:
        complaint.status = "assigned"
        complaint.assigned_department = payload.department
        if payload.priority:
            complaint.priority = payload.priority
        complaint.routing_reason = (
            f"تعيين بشري بواسطة {current_user.username}: {payload.notes.strip()}"
        )
        event = _audit(
            db,
            complaint,
            "HUMAN_ASSIGNMENT",
            current_user,
            (
                f"القسم السابق: {previous_department or 'غير محدد'}. "
                f"القسم الجديد: {payload.department}. "
                f"الأولوية: {complaint.priority}. "
                f"ملاحظات: {payload.notes.strip()}"
            ),
        )
        response = ComplaintActionResponse(
            message="تم تعيين الشكوى بنجاح.",
            complaint_number=complaint.complaint_number,
            previous_status=previous_status,
            new_status="assigned",
            assigned_department=complaint.assigned_department,
            priority=complaint.priority,
            actor_username=current_user.username,
            audit_event_id=event.id,
        )
        db.commit()
        return response
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="تعذر حفظ تعيين الشكوى."
        ) from exc


@router.post(
    "/{complaint_id}/status",
    response_model=ComplaintActionResponse,
    summary="تحديث حالة معالجة الشكوى",
)
def update_complaint_status(
    complaint_id: int,
    payload: ComplaintStatusRequest,
    current_user: User = Depends(require_roles("reviewer", "admin")),
    db: Session = Depends(get_db),
) -> ComplaintActionResponse:
    complaint = _get_for_update(db, complaint_id)

    previous_status = complaint.status
    if payload.status not in ALLOWED_STATUS_TRANSITIONS.get(previous_status, set()):
        raise HTTPException(
            status_code=409,
            detail="انتقال حالة الشكوى غير مسموح به.",
        )

    try:
        complaint.status = payload.status
        event = _audit(
            db,
            complaint,
            "STATUS_CHANGE",
            current_user,
            (
                f"الحالة السابقة: {previous_status}. "
                f"الحالة الجديدة: {payload.status}. "
                f"ملاحظات: {payload.notes.strip()}"
            ),
        )
        response = ComplaintActionResponse(
            message="تم تحديث حالة الشكوى.",
            complaint_number=complaint.complaint_number,
            previous_status=previous_status,
            new_status=payload.status,
            assigned_department=complaint.assigned_department,
            priority=complaint.priority,
            actor_username=current_user.username,
            audit_event_id=event.id,
        )
        db.commit()
        return response
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="تعذر حفظ حالة الشكوى."
        ) from exc
