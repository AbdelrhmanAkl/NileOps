
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from src.core.config import get_settings
from src.db.session import get_db
from src.models.kyc import (
    ApplicationStatus,
    AuditEvent,
    DocumentStatus,
    KYCApplication,
    KYCDocument,
    ValidationResult,
    utc_now,
)

settings = get_settings()

app = FastAPI(
    title="NileOps — مساعد العمليات البنكية",
    description=(
        "واجهة برمجية لإدارة طلبات اعرف عميلك (KYC)، "
        "وعرض المستندات ونتائج التحقق وسجل التدقيق. "
        "البيانات الحالية اصطناعية لأغراض التطوير والاختبار."
    ),
    version="0.1.0",
)


class ApplicationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    application_number: str
    customer_name: str
    application_type: str
    status: str
    branch_name: str | None
    created_at: Any
    updated_at: Any


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_type: str
    status: str
    file_name: str | None
    reviewer_notes: str | None
    uploaded_at: Any
    created_at: Any


class ValidationResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_code: str
    title_ar: str
    details_ar: str
    severity: str
    passed: bool
    evidence: dict | list | None
    created_at: Any


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    actor_type: str
    actor_name: str | None
    details_ar: str
    previous_status: str | None
    new_status: str | None
    created_at: Any


class ApplicationDetail(ApplicationSummary):
    national_id_last_four: str | None
    notes: str | None
    documents: list[DocumentResponse]
    validation_results: list[ValidationResultResponse]
    audit_events: list[AuditEventResponse]


class ReviewRequest(BaseModel):
    decision: Literal["IN_REVIEW", "NEEDS_INFORMATION", "APPROVED", "REJECTED"]
    reviewer_name: str = Field(min_length=2, max_length=150)
    notes: str = Field(min_length=3, max_length=2000)


class ReviewResponse(BaseModel):
    message: str
    application_id: int
    application_number: str
    previous_status: str
    new_status: str
    reviewer_name: str
    audit_event_id: int


REVIEW_STATUS_MAP = {
    "IN_REVIEW": ApplicationStatus.IN_REVIEW.value,
    "NEEDS_INFORMATION": ApplicationStatus.NEEDS_INFORMATION.value,
    "APPROVED": ApplicationStatus.APPROVED.value,
    "REJECTED": ApplicationStatus.REJECTED.value,
}

ALLOWED_TRANSITIONS = {
    ApplicationStatus.PENDING.value: {
        ApplicationStatus.IN_REVIEW.value,
        ApplicationStatus.NEEDS_INFORMATION.value,
    },
    ApplicationStatus.IN_REVIEW.value: {
        ApplicationStatus.NEEDS_INFORMATION.value,
        ApplicationStatus.APPROVED.value,
        ApplicationStatus.REJECTED.value,
    },
    ApplicationStatus.NEEDS_INFORMATION.value: {
        ApplicationStatus.IN_REVIEW.value,
    },
}


@app.get(
    "/health",
    tags=["صحة النظام"],
    summary="فحص التطبيق وقاعدة البيانات",
)
def health_check(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="قاعدة البيانات غير متاحة حاليًا.",
        ) from exc

    return {
        "status": "healthy",
        "application": settings.app_name,
        "database": "connected",
        "message": "التطبيق يعمل وقاعدة البيانات متصلة.",
    }


@app.get(
    "/api/v1/kyc/applications",
    tags=["طلبات اعرف عميلك"],
    summary="عرض طلبات العملاء",
)
def list_applications(
    q: str | None = Query(
        default=None,
        description="البحث برقم الطلب أو اسم العميل",
        max_length=150,
    ),
    status: str | None = Query(
        default=None,
        description="تصفية الطلبات حسب الحالة",
        max_length=50,
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(KYCApplication)

    if q and q.strip():
        search_term = q.strip()
        statement = statement.where(
            or_(
                KYCApplication.application_number.contains(search_term),
                KYCApplication.customer_name.contains(search_term),
            )
        )

    if status:
        statement = statement.where(KYCApplication.status == status)

    count_statement = select(func.count()).select_from(
        statement.order_by(None).subquery()
    )
    total = db.scalar(count_statement) or 0

    applications = db.scalars(
        statement.order_by(KYCApplication.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return {
        "message": "تم استرجاع طلبات العملاء بنجاح.",
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            ApplicationSummary.model_validate(item).model_dump(mode="json")
            for item in applications
        ],
    }


@app.get(
    "/api/v1/kyc/applications/{application_id}",
    response_model=ApplicationDetail,
    tags=["طلبات اعرف عميلك"],
    summary="عرض تفاصيل طلب ومستنداته ونتائج التحقق",
)
def get_application(
    application_id: int,
    db: Session = Depends(get_db),
) -> ApplicationDetail:
    statement = (
        select(KYCApplication)
        .where(KYCApplication.id == application_id)
        .options(
            selectinload(KYCApplication.documents),
            selectinload(KYCApplication.validation_results),
            selectinload(KYCApplication.audit_events),
        )
    )

    application = db.scalar(statement)

    if application is None:
        raise HTTPException(
            status_code=404,
            detail="طلب العميل غير موجود.",
        )

    return ApplicationDetail.model_validate(application)


@app.post(
    "/api/v1/kyc/applications/{application_id}/review",
    response_model=ReviewResponse,
    tags=["المراجعة البشرية"],
    summary="تسجيل قرار المراجع البشري",
)
def review_application(
    application_id: int,
    payload: ReviewRequest,
    db: Session = Depends(get_db),
) -> ReviewResponse:
    application = db.scalar(
        select(KYCApplication)
        .where(KYCApplication.id == application_id)
        .with_for_update()
    )

    if application is None:
        raise HTTPException(
            status_code=404,
            detail="طلب العميل غير موجود.",
        )

    previous_status = application.status
    new_status = REVIEW_STATUS_MAP[payload.decision]

    if new_status not in ALLOWED_TRANSITIONS.get(previous_status, set()):
        raise HTTPException(
            status_code=409,
            detail="انتقال حالة الطلب غير مسموح به.",
        )

    if new_status == ApplicationStatus.APPROVED.value:
        documents = db.scalars(
            select(KYCDocument).where(
                KYCDocument.application_id == application_id
            )
        ).all()

        required_document_types = {
            "بطاقة الرقم القومي",
            "إثبات العنوان",
            "استمارة بيانات العميل",
        }

        documents_by_type = {
            document.document_type: document
            for document in documents
        }

        all_documents_valid = all(
            document_type in documents_by_type
            and documents_by_type[document_type].status
            == DocumentStatus.VALID.value
            for document_type in required_document_types
        )

        if not all_documents_valid:
            raise HTTPException(
                status_code=409,
                detail=(
                    "لا يمكن اعتماد الطلب قبل التأكد من استيفاء "
                    "المستندات المطلوبة ومراجعتها بشريًا."
                ),
            )

    try:
        application.status = new_status
        application.updated_at = utc_now()

        audit_event = AuditEvent(
            application_id=application.id,
            action="HUMAN_REVIEW",
            actor_type="human",
            actor_name=payload.reviewer_name.strip(),
            details_ar=payload.notes.strip(),
            previous_status=previous_status,
            new_status=new_status,
        )
        db.add(audit_event)
        db.flush()

        response = ReviewResponse(
            message="تم تسجيل قرار المراجعة بنجاح.",
            application_id=application.id,
            application_number=application.application_number,
            previous_status=previous_status,
            new_status=new_status,
            reviewer_name=payload.reviewer_name.strip(),
            audit_event_id=audit_event.id,
        )

        db.commit()
        return response

    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="تعذر حفظ قرار المراجعة. حاول مرة أخرى.",
        ) from exc
