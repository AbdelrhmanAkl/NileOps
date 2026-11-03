"""Policy knowledge base API: ask questions with citations, browse versions.

Answers are advisory. The question text is NOT stored in the audit trail (it can
contain customer details); only counts and the outcome are recorded.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.core.auth import require_roles
from src.core.config import get_settings
from src.db.session import get_db
from src.models.banking import BankingAuditEvent
from src.models.policies import PolicyChunk, PolicyDocument
from src.models.user import User
from src.rag.answer import answer_question
from src.rag.chunking import PolicyFormatError, parse_policy, read_policy_files
from src.rag.llm import get_llm_client
from src.rag.store import build_index, load_policies, policy_label

router = APIRouter(prefix="/api/v1/policies", tags=["قاعدة معرفة السياسات"])


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # "query" is accepted too, to match docs/api_documentaion.md.
    question: str = Field(
        min_length=5, max_length=500, validation_alias=AliasChoices("question", "query")
    )


class CitationItem(BaseModel):
    number: int
    chunk_id: int
    policy_code: str
    version: int
    title: str
    heading: str
    excerpt: str
    score: float


class AskResponse(BaseModel):
    status: Literal["answered", "abstained"]
    mode: Literal["llm", "extractive", "none"]
    answer: str
    coverage: float
    citations: list[CitationItem]
    note: str | None
    advisory: str = "إجابة استرشادية من سياسات البنك؛ القرار النهائي للموظف المختص."


class PolicyItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    policy_code: str
    version: int
    title: str
    department: str | None
    effective_date: date | None
    is_active: bool


class PolicyDetail(PolicyItem):
    sections: list[dict[str, str]]


class ReloadResponse(BaseModel):
    added: list[str]
    unchanged: list[str]
    conflicts: list[str]


def _audit(db: Session, user: User, action: str, entity_id: int, details: str) -> None:
    db.add(
        BankingAuditEvent(
            entity_type="policy_query",
            entity_id=entity_id,
            action=action,
            actor_role=user.role,
            actor_username=user.username,
            details_ar=details,
        )
    )


@router.post("/ask", response_model=AskResponse, summary="اسأل عن سياسة (مع المصادر)")
def ask(
    payload: AskRequest,
    current_user: User = Depends(require_roles("viewer", "reviewer", "admin")),
    db: Session = Depends(get_db),
) -> AskResponse:
    settings = get_settings()
    try:
        llm = get_llm_client()
    except ValueError as exc:  # e.g. LLM_BASE_URL points outside the bank network
        raise HTTPException(
            status_code=503, detail="إعداد نموذج اللغة غير صالح (راجع LLM_BASE_URL)."
        ) from exc
    index = build_index(db)
    result = answer_question(
        payload.question.strip(),
        index,
        top_k=settings.rag_top_k,
        min_coverage=settings.rag_min_coverage,
        llm=llm,
        llm_min_coverage=settings.rag_llm_min_coverage,
    )
    try:
        # entity_id is the number of cited passages; the question text is never stored.
        _audit(
            db,
            current_user,
            "POLICY_QUESTION",
            len(result.citations),
            f"سؤال عن السياسات: النتيجة={result.status}، الأسلوب={result.mode}، "
            f"عدد المصادر={len(result.citations)}",
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()  # logging must not block the answer
    return AskResponse(
        status=result.status,
        mode=result.mode,
        answer=result.answer,
        coverage=result.coverage,
        citations=[CitationItem(**c.__dict__) for c in result.citations],
        note=result.note,
    )


@router.get("", response_model=list[PolicyItem], summary="عرض السياسات وإصداراتها")
def list_policies(
    include_superseded: bool = False,
    current_user: User = Depends(require_roles("viewer", "reviewer", "admin")),
    db: Session = Depends(get_db),
) -> list[PolicyDocument]:
    statement = select(PolicyDocument).order_by(PolicyDocument.policy_code, PolicyDocument.version.desc())
    if not include_superseded:
        statement = statement.where(PolicyDocument.is_active.is_(True))
    return list(db.scalars(statement))


@router.get("/{policy_code}", response_model=PolicyDetail, summary="نص سياسة (آخر إصدار أو إصدار محدد)")
def get_policy(
    policy_code: str,
    version: int | None = None,
    current_user: User = Depends(require_roles("viewer", "reviewer", "admin")),
    db: Session = Depends(get_db),
) -> PolicyDetail:
    statement = select(PolicyDocument).where(PolicyDocument.policy_code == policy_code)
    if version is None:
        statement = statement.where(PolicyDocument.is_active.is_(True))
    else:
        statement = statement.where(PolicyDocument.version == version)
    document = db.scalar(statement)
    if document is None:
        raise HTTPException(status_code=404, detail="السياسة غير موجودة.")
    chunks = db.scalars(
        select(PolicyChunk).where(PolicyChunk.policy_id == document.id).order_by(PolicyChunk.position)
    ).all()
    return PolicyDetail(
        **PolicyItem.model_validate(document).model_dump(),
        sections=[{"heading": c.heading, "text": c.text} for c in chunks],
    )


@router.post("/reload", response_model=ReloadResponse, summary="تحميل ملفات السياسات من المجلد (للمدير)")
def reload_policies(
    current_user: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> ReloadResponse:
    try:
        parsed = read_policy_files(get_settings().resolved_policies_dir)
    except PolicyFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        report = load_policies(db, parsed)
        _audit(
            db,
            current_user,
            "POLICY_RELOAD",
            len(report.added),
            f"تحميل السياسات: أضيف {len(report.added)}، دون تغيير {len(report.unchanged)}، "
            f"تعارض {len(report.conflicts)}",
        )
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="تعذر حفظ السياسات.") from exc
    return ReloadResponse(**report.__dict__)
