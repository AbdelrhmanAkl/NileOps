"""Operations dashboard: aggregated, PII-free summary plus the static Arabic UI.

The summary deliberately contains only counts, reference numbers, status codes
and timestamps. Customer names, free-text notes, complaint descriptions, OCR
values and audit "details" strings are never included.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.core.auth import require_roles
from src.db.session import get_db
from src.models.banking import (
    AccountOpeningRequest,
    BankingAuditEvent,
    CardIssuanceRequest,
    Complaint,
)
from src.models.documents import ExtractedField
from src.models.kyc import ApplicationStatus, AuditEvent, KYCApplication, KYCDocument, utc_now
from src.models.policies import PolicyDocument
from src.models.user import User

router = APIRouter(prefix="/api/v1/dashboard", tags=["لوحة المتابعة"])
web_router = APIRouter(include_in_schema=False)

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
QUEUE_LIMIT = 10
ACTIVITY_LIMIT = 10

OPEN_APPLICATION = (
    ApplicationStatus.PENDING.value,
    ApplicationStatus.IN_REVIEW.value,
    ApplicationStatus.NEEDS_INFORMATION.value,
)
OPEN_ACCOUNT = ("pending", "under_review", "needs_information")
OPEN_CARD = ("pending", "under_review")
OPEN_COMPLAINT = ("new", "classified", "assigned", "in_progress")
_PRIORITY_RANK = case(
    {"urgent": 0, "high": 1, "medium": 2, "low": 3}, value=Complaint.priority, else_=4
)


class Counts(BaseModel):
    total: int
    by_status: dict[str, int]


class ComplaintCounts(Counts):
    by_priority: dict[str, int]
    by_category: dict[str, int]


class QueueRow(BaseModel):
    id: int
    number: str
    status: str
    kind: str  # type / category / card type, a fixed vocabulary value
    priority: str | None = None


class ActivityRow(BaseModel):
    at: datetime
    source: str  # kyc | banking
    entity: str
    action: str
    actor_role: str


class Kpis(BaseModel):
    open_reviews: int
    urgent_open_complaints: int
    ocr_fields_total: int
    ocr_fields_need_review: int
    active_policies: int


class DashboardSummary(BaseModel):
    generated_at: datetime
    kpis: Kpis
    applications: Counts
    documents: Counts
    account_requests: Counts
    card_requests: Counts
    complaints: ComplaintCounts
    queues: dict[str, list[QueueRow]]
    recent_activity: list[ActivityRow]
    advisory: str = "أرقام للمتابعة فقط؛ القرارات تُتخذ بشريًا من شاشات المراجعة."


def _by(db: Session, column) -> dict[str, int]:
    rows = db.execute(select(column, func.count()).group_by(column)).all()
    return {str(key if key is not None else "غير محدد"): count for key, count in rows}


def _counts(db: Session, column) -> Counts:
    by_status = _by(db, column)
    return Counts(total=sum(by_status.values()), by_status=by_status)


def _count(db: Session, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where)) or 0


@router.get("/summary", response_model=DashboardSummary, summary="ملخص لوحة المتابعة")
def dashboard_summary(
    current_user: User = Depends(require_roles("viewer", "reviewer", "admin")),
    db: Session = Depends(get_db),
) -> DashboardSummary:
    complaints_by_status = _by(db, Complaint.status)
    complaints = ComplaintCounts(
        total=sum(complaints_by_status.values()),
        by_status=complaints_by_status,
        by_priority=_by(db, Complaint.priority),
        by_category=_by(db, Complaint.category),
    )
    open_reviews = (
        _count(db, KYCApplication, KYCApplication.status.in_(OPEN_APPLICATION))
        + _count(db, AccountOpeningRequest, AccountOpeningRequest.status.in_(OPEN_ACCOUNT))
        + _count(db, CardIssuanceRequest, CardIssuanceRequest.status.in_(OPEN_CARD))
        + complaints_by_status.get("classified", 0)
    )

    queues = {
        "applications": [
            QueueRow(id=a.id, number=a.application_number, status=a.status, kind=a.application_type)
            for a in db.scalars(
                select(KYCApplication)
                .where(KYCApplication.status.in_(OPEN_APPLICATION))
                .order_by(KYCApplication.id)
                .limit(QUEUE_LIMIT)
            )
        ],
        "complaints": [
            QueueRow(id=c.id, number=c.complaint_number, status=c.status, kind=c.category, priority=c.priority)
            for c in db.scalars(
                select(Complaint)
                .where(Complaint.status.in_(OPEN_COMPLAINT))
                .order_by(_PRIORITY_RANK, Complaint.id)
                .limit(QUEUE_LIMIT)
            )
        ],
        "cards": [
            QueueRow(id=r.id, number=r.request_number, status=r.status, kind=r.card_type)
            for r in db.scalars(
                select(CardIssuanceRequest)
                .where(CardIssuanceRequest.status.in_(OPEN_CARD))
                .order_by(CardIssuanceRequest.id)
                .limit(QUEUE_LIMIT)
            )
        ],
        "accounts": [
            QueueRow(id=r.id, number=r.request_number, status=r.status, kind=r.account_type)
            for r in db.scalars(
                select(AccountOpeningRequest)
                .where(AccountOpeningRequest.status.in_(OPEN_ACCOUNT))
                .order_by(AccountOpeningRequest.id)
                .limit(QUEUE_LIMIT)
            )
        ],
    }

    activity = [
        ActivityRow(at=e.created_at, source="banking", entity=e.entity_type, action=e.action, actor_role=e.actor_role)
        for e in db.scalars(select(BankingAuditEvent).order_by(BankingAuditEvent.id.desc()).limit(ACTIVITY_LIMIT))
    ] + [
        ActivityRow(at=e.created_at, source="kyc", entity="kyc_application", action=e.action, actor_role=e.actor_type)
        for e in db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(ACTIVITY_LIMIT))
    ]
    activity.sort(key=lambda row: row.at, reverse=True)

    return DashboardSummary(
        generated_at=utc_now(),
        kpis=Kpis(
            open_reviews=open_reviews,
            urgent_open_complaints=_count(
                db, Complaint, Complaint.priority == "urgent", Complaint.status.in_(OPEN_COMPLAINT)
            ),
            ocr_fields_total=_count(db, ExtractedField),
            ocr_fields_need_review=_count(db, ExtractedField, ExtractedField.needs_review.is_(True)),
            active_policies=_count(db, PolicyDocument, PolicyDocument.is_active.is_(True)),
        ),
        applications=_counts(db, KYCApplication.status),
        documents=_counts(db, KYCDocument.status),
        account_requests=_counts(db, AccountOpeningRequest.status),
        card_requests=_counts(db, CardIssuanceRequest.status),
        complaints=complaints,
        queues=queues,
        recent_activity=activity[:ACTIVITY_LIMIT],
    )


# ----------------------------------------------------------------- static UI
# The page is public (it holds no data); every number comes from the API above
# with a bearer token. Fixed file names only: no path parameter, no traversal.
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; font-src 'self'; "
    "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_ASSET = re.compile(r"^[a-z0-9_-]{1,40}\.(js|css|woff)$")
_ASSET_TYPES = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "woff": "font/woff"}
_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _serve(name: str, media_type: str) -> FileResponse:
    file = WEB_DIR / name
    if not file.is_file():
        raise HTTPException(status_code=404, detail="الصفحة غير متاحة.")
    return FileResponse(file, media_type=media_type, headers=_SECURITY_HEADERS)


@web_router.get("/dashboard")
def dashboard_page() -> FileResponse:
    return _serve("dashboard.html", "text/html; charset=utf-8")


@web_router.get("/dashboard/{name}")
def dashboard_asset(name: str) -> FileResponse:
    # Only flat, lower-case *.js / *.css names that exist in src/web: no traversal is possible.
    if not _ASSET.match(name):
        raise HTTPException(status_code=404, detail="الصفحة غير متاحة.")
    return _serve(name, _ASSET_TYPES[name.rsplit(".", 1)[1]])
