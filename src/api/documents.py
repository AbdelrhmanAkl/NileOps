"""Secure document upload, OCR extraction and human document review.

OCR output and cross-checks assist the reviewer only. A document becomes
``VALID`` only through an explicit human decision recorded in the audit trail.
"""

from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.core.auth import require_roles
from src.core.config import get_settings
from src.db.session import get_db
from src.models.documents import DocumentFile, ExtractedField
from src.models.kyc import AuditEvent, DocumentStatus, KYCApplication, KYCDocument
from src.models.user import User
from src.ocr.engine import OcrEngine, OcrFailed, OcrUnavailable, TesseractEngine
from src.ocr.fields import EXPECTED_FIELDS, extract_fields
from src.services.document_checks import run_checks
from src.services.file_storage import (
    UploadRejected,
    delete_file,
    read_limited,
    store_file,
    validate_upload,
)

router = APIRouter(prefix="/api/v1/kyc/documents", tags=["مستندات اعرف عميلك"])

# Statuses from which a new upload is still allowed (a reviewed document is locked).
UPLOADABLE_STATUSES = {
    DocumentStatus.MISSING.value,
    DocumentStatus.UPLOADED.value,
    DocumentStatus.UNDER_REVIEW.value,
    DocumentStatus.INVALID.value,
}


@lru_cache(maxsize=1)
def get_ocr_engine() -> OcrEngine:
    settings = get_settings()
    return TesseractEngine(
        command=settings.tesseract_cmd,
        languages=settings.ocr_languages,
        timeout_seconds=settings.ocr_timeout_seconds,
        max_pdf_pages=settings.ocr_max_pdf_pages,
    )


# ------------------------------------------------------------------- schemas
class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    original_name: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_by: str
    ocr_status: str
    ocr_engine: str | None
    created_at: datetime


class UploadResponse(BaseModel):
    message: str
    file: FileInfo
    document_status: str


class FieldInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    field_name: str
    value: str
    confidence: float
    needs_review: bool
    review_reason: str | None
    source: dict | None
    method: str


class CheckInfo(BaseModel):
    code: str
    title_ar: str
    passed: bool | None
    severity: str
    details_ar: str


class ExtractionResponse(BaseModel):
    message: str
    file_id: int
    document_type: str
    engine: str
    fields: list[FieldInfo]
    missing_fields: list[str]
    derived: dict[str, str]
    checks: list[CheckInfo]
    requires_human_review: bool = True
    ai_note: str = "نتائج القراءة الآلية مساعدة فقط؛ القرار النهائي للموظف المختص."


class DocumentReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["VALID", "INVALID", "EXPIRED", "UNDER_REVIEW"]
    notes: str = Field(min_length=3, max_length=2000)


class DocumentReviewResponse(BaseModel):
    message: str
    document_id: int
    previous_status: str
    new_status: str
    reviewer_name: str
    audit_event_id: int


DECISION_STATUS = {
    "VALID": DocumentStatus.VALID.value,
    "INVALID": DocumentStatus.INVALID.value,
    "EXPIRED": DocumentStatus.EXPIRED.value,
    "UNDER_REVIEW": DocumentStatus.UNDER_REVIEW.value,
}


def _get_document(db: Session, document_id: int, for_update: bool = False) -> KYCDocument:
    statement = select(KYCDocument).where(KYCDocument.id == document_id)
    if for_update:
        statement = statement.with_for_update()
    document = db.scalar(statement)
    if document is None:
        raise HTTPException(status_code=404, detail="المستند غير موجود.")
    return document


def _get_file(db: Session, document_id: int, file_id: int) -> DocumentFile:
    file = db.scalar(
        select(DocumentFile).where(
            DocumentFile.id == file_id, DocumentFile.document_id == document_id
        )
    )
    if file is None:
        raise HTTPException(status_code=404, detail="ملف المستند غير موجود.")
    return file


# ------------------------------------------------------------------ endpoints
async def save_document_upload(
    db: Session, document: KYCDocument, file: UploadFile, username: str, actor_type: str = "human"
) -> UploadResponse:
    """Validate, store and record an uploaded file for a document (shared by staff and customer portal)."""
    settings = get_settings()
    if document.status not in UPLOADABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="لا يمكن رفع ملف جديد بعد اعتماد المستند أو انتهاء صلاحيته.",
        )

    try:
        data = read_limited(file.file, settings.max_upload_size_mb * 1024 * 1024)
        kind = validate_upload(data)
        stored = store_file(
            data,
            kind,
            file.filename,
            settings.resolved_upload_dir,
            f"documents/{document.application_id}",
        )
    except UploadRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    finally:
        await file.close()

    previous_status = document.status
    try:
        record = DocumentFile(
            document_id=document.id,
            stored_name=stored.stored_name,
            original_name=stored.original_name,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            uploaded_by=username,
        )
        db.add(record)
        document.file_name = stored.original_name
        document.uploaded_at = datetime.now(timezone.utc)
        if document.status == DocumentStatus.MISSING.value:
            document.status = DocumentStatus.UPLOADED.value
        db.add(
            AuditEvent(
                application_id=document.application_id,
                action="DOCUMENT_UPLOADED",
                actor_type=actor_type,
                actor_name=username,
                details_ar=f"تم رفع ملف للمستند «{document.document_type}» (رقم المستند {document.id}).",
                previous_status=previous_status,
                new_status=document.status,
            )
        )
        db.flush()
        response = UploadResponse(
            message="تم رفع الملف بنجاح.",
            file=FileInfo.model_validate(record),
            document_status=document.status,
        )
        db.commit()
        return response
    except IntegrityError:
        db.rollback()
        delete_file(stored.path)
        raise HTTPException(status_code=409, detail="هذا الملف مرفوع مسبقًا لنفس المستند.") from None
    except SQLAlchemyError as exc:
        db.rollback()
        delete_file(stored.path)
        raise HTTPException(status_code=500, detail="تعذر حفظ بيانات الملف.") from exc


@router.post(
    "/{document_id}/files",
    response_model=UploadResponse,
    status_code=201,
    summary="رفع ملف مستند (PNG / JPEG / PDF) بشكل آمن",
)
async def upload_document_file(
    document_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles("reviewer", "admin")),
    db: Session = Depends(get_db),
) -> UploadResponse:
    document = _get_document(db, document_id, for_update=True)
    return await save_document_upload(db, document, file, current_user.username)


@router.get(
    "/{document_id}/files",
    response_model=list[FileInfo],
    summary="عرض الملفات المرفوعة لمستند",
)
def list_document_files(
    document_id: int,
    current_user: User = Depends(require_roles("viewer", "reviewer", "admin")),
    db: Session = Depends(get_db),
) -> list[FileInfo]:
    _get_document(db, document_id)
    files = db.scalars(
        select(DocumentFile)
        .where(DocumentFile.document_id == document_id)
        .order_by(DocumentFile.id)
    ).all()
    return [FileInfo.model_validate(f) for f in files]


MASKED_FIELDS = {"national_id", "mobile"}


def _mask_for_viewer(response: ExtractionResponse) -> ExtractionResponse:
    """Read-only users never need the full ID or phone: show the last 4 characters (POL-DPP-001)."""
    for item in response.fields:
        if item.field_name in MASKED_FIELDS:
            item.value = "*" * max(len(item.value) - 4, 0) + item.value[-4:]
            # the recognised line of text would reveal the full number again
            item.source = {k: v for k, v in (item.source or {}).items() if k != "line_text"}
    response.derived = {}  # birth date and gender are derived from the national ID
    return response


def _fields_response(
    db: Session, file: DocumentFile, document: KYCDocument, application: KYCApplication, message: str, engine_name: str
) -> ExtractionResponse:
    rows = db.scalars(
        select(ExtractedField)
        .where(ExtractedField.document_file_id == file.id)
        .order_by(ExtractedField.id)
    ).all()
    from src.ocr.fields import ExtractedFieldValue, ExtractionResult

    result = ExtractionResult(document_type=document.document_type)
    result.fields = [
        ExtractedFieldValue(
            name=r.field_name, value=r.value, confidence=r.confidence,
            needs_review=r.needs_review, review_reason=r.review_reason,
            source=r.source or {}, method=r.method,
        )
        for r in rows
    ]
    expected = EXPECTED_FIELDS.get(document.document_type, ())
    result.missing_fields = [n for n in expected if n not in {r.field_name for r in rows}]
    nid = result.get("national_id")
    if nid:
        from src.ocr.fields import parse_national_id

        info = parse_national_id(nid.value)
        if info.valid and info.birth_date:
            result.derived = {"birth_date": info.birth_date.isoformat(), "gender": info.gender or ""}
    checks = run_checks(application, result)
    return ExtractionResponse(
        message=message,
        file_id=file.id,
        document_type=document.document_type,
        engine=engine_name,
        fields=[FieldInfo.model_validate(r) for r in rows],
        missing_fields=result.missing_fields,
        derived=result.derived,
        checks=[CheckInfo(**c.__dict__) for c in checks],
        requires_human_review=True,
    )


@router.post(
    "/{document_id}/files/{file_id}/extract",
    response_model=ExtractionResponse,
    summary="قراءة المستند بالـ OCR واستخراج الحقول مع الثقة والمصدر",
)
def extract_document_fields(
    document_id: int,
    file_id: int,
    current_user: User = Depends(require_roles("reviewer", "admin")),
    db: Session = Depends(get_db),
    engine: OcrEngine = Depends(get_ocr_engine),
) -> ExtractionResponse:
    settings = get_settings()
    document = _get_document(db, document_id)
    file = _get_file(db, document_id, file_id)
    application = db.get(KYCApplication, document.application_id)

    path = settings.resolved_upload_dir / f"documents/{document.application_id}" / file.stored_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="ملف المستند غير موجود على وحدة التخزين.")

    try:
        ocr = engine.recognize(path, file.content_type)
    except OcrUnavailable as exc:
        file.ocr_status = "unavailable"
        db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except OcrFailed as exc:
        file.ocr_status = "failed"
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from None

    extraction = extract_fields(ocr, document.document_type, settings.ocr_min_confidence)

    try:
        for old in db.scalars(select(ExtractedField).where(ExtractedField.document_file_id == file.id)):
            db.delete(old)
        db.flush()
        for item in extraction.fields:
            db.add(
                ExtractedField(
                    document_file_id=file.id,
                    field_name=item.name,
                    value=item.value,
                    confidence=item.confidence,
                    needs_review=item.needs_review,
                    review_reason=item.review_reason,
                    source=item.source,
                    method=item.method,
                )
            )
        file.ocr_status = "needs_review" if extraction.requires_human_review else "completed"
        file.ocr_engine = ocr.engine
        document.extracted_text = ocr.text
        if document.status in {DocumentStatus.UPLOADED.value, DocumentStatus.MISSING.value}:
            document.status = DocumentStatus.UNDER_REVIEW.value
        # Audit text deliberately contains no extracted values (no PII).
        db.add(
            AuditEvent(
                application_id=document.application_id,
                action="DOCUMENT_OCR_EXTRACTED",
                actor_type="human",
                actor_name=current_user.username,
                details_ar=(
                    f"تم تشغيل القراءة الآلية للمستند رقم {document.id}: "
                    f"{len(extraction.fields)} حقل مستخرج و{len(extraction.missing_fields)} حقل مفقود."
                ),
                previous_status=None,
                new_status=document.status,
            )
        )
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="تعذر حفظ نتائج القراءة.") from exc

    return _fields_response(db, file, document, application, "تمت القراءة الآلية للمستند.", ocr.engine)


@router.get(
    "/{document_id}/files/{file_id}/fields",
    response_model=ExtractionResponse,
    summary="عرض الحقول المستخرجة وفحوصات المطابقة",
)
def get_extracted_fields(
    document_id: int,
    file_id: int,
    current_user: User = Depends(require_roles("viewer", "reviewer", "admin")),
    db: Session = Depends(get_db),
) -> ExtractionResponse:
    document = _get_document(db, document_id)
    file = _get_file(db, document_id, file_id)
    if file.ocr_status in {"not_run", "unavailable", "failed"}:
        raise HTTPException(status_code=409, detail="لم تتم قراءة هذا الملف بنجاح بعد.")
    application = db.get(KYCApplication, document.application_id)
    response = _fields_response(
        db, file, document, application, "تم استرجاع الحقول المستخرجة.", file.ocr_engine or "unknown"
    )
    return _mask_for_viewer(response) if current_user.role == "viewer" else response


@router.post(
    "/{document_id}/review",
    response_model=DocumentReviewResponse,
    summary="قرار المراجع البشري على المستند",
)
def review_document(
    document_id: int,
    payload: DocumentReviewRequest,
    current_user: User = Depends(require_roles("reviewer", "admin")),
    db: Session = Depends(get_db),
) -> DocumentReviewResponse:
    document = _get_document(db, document_id, for_update=True)

    has_file = db.scalar(
        select(DocumentFile.id).where(DocumentFile.document_id == document.id).limit(1)
    )
    if payload.decision == "VALID" and has_file is None:
        raise HTTPException(
            status_code=409,
            detail="لا يمكن اعتماد مستند دون ملف مرفوع.",
        )
    if document.status == DocumentStatus.MISSING.value:
        raise HTTPException(status_code=409, detail="المستند غير مرفق بعد.")

    previous_status = document.status
    new_status = DECISION_STATUS[payload.decision]
    try:
        document.status = new_status
        document.reviewer_notes = payload.notes.strip()
        event = AuditEvent(
            application_id=document.application_id,
            action="DOCUMENT_HUMAN_REVIEW",
            actor_type="human",
            actor_name=current_user.username,
            details_ar=f"قرار مراجعة المستند «{document.document_type}»: {payload.notes.strip()}",
            previous_status=previous_status,
            new_status=new_status,
        )
        db.add(event)
        db.flush()
        response = DocumentReviewResponse(
            message="تم تسجيل قرار مراجعة المستند.",
            document_id=document.id,
            previous_status=previous_status,
            new_status=new_status,
            reviewer_name=current_user.username,
            audit_event_id=event.id,
        )
        db.commit()
        return response
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="تعذر حفظ قرار المراجعة.") from exc
