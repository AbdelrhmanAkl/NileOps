from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from src.core.config import get_settings
from src.db.session import get_db
from src.models.kyc import (
    AuditEvent,
    KYCApplication,
    KYCDocument,
    ValidationResult,
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
