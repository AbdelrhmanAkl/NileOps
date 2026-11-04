"""Explanations for employees: why is this application / complaint in its current state?

The explanation is built from system facts (never from free text typed by people),
cites the approved policies, and may be reworded by a *local* model whose output is
verified. It never decides anything: decisions stay with the human reviewer.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from src.api.policies import CitationItem
from src.core.auth import require_roles
from src.db.session import get_db
from src.explain.facts import ExplainFacts, build_application_facts, build_complaint_facts
from src.explain.service import Explanation, build_explanation
from src.models.banking import BankingAuditEvent, Complaint
from src.models.kyc import KYCApplication
from src.models.user import User
from src.rag.llm import get_llm_client
from src.rag.store import build_index

router = APIRouter(prefix="/api/v1/explanations", tags=["الشرح بالذكاء الاصطناعي"])


class ExplanationResponse(BaseModel):
    subject_type: Literal["application", "complaint"]
    subject_id: int
    reference: str
    mode: Literal["template", "llm"]
    summary: str
    points: list[str]
    next_steps: list[str]
    citations: list[CitationItem]
    note: str | None
    advisory: str = "شرح استرشادي للمساعدة فقط؛ القرار النهائي للموظف المختص."


def _respond(
    db: Session, user: User, facts: ExplainFacts, explanation: Explanation, model: str | None
) -> ExplanationResponse:
    policies = ", ".join(sorted({f"{c.policy_code} v{c.version}" for c in explanation.citations})) or "-"
    try:
        # Audit: which mode/model/policy versions produced it. No explanation text is stored.
        db.add(
            BankingAuditEvent(
                entity_type=f"explanation_{facts.subject_type}",
                entity_id=facts.subject_id,
                action="EXPLANATION_GENERATED",
                actor_role=user.role,
                actor_username=user.username,
                details_ar=f"الأسلوب={explanation.mode}، النموذج={model or '-'}، السياسات={policies}",
            )
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()  # logging must not block the explanation
    return ExplanationResponse(
        subject_type=facts.subject_type,
        subject_id=facts.subject_id,
        reference=facts.label,
        mode=explanation.mode,
        summary=explanation.summary,
        points=explanation.points,
        next_steps=explanation.next_steps,
        citations=[CitationItem(**c.__dict__) for c in explanation.citations],
        note=explanation.note,
    )


def _llm():
    try:
        return get_llm_client()
    except ValueError as exc:  # e.g. LLM_BASE_URL outside the bank network
        raise HTTPException(status_code=503, detail="إعداد نموذج اللغة غير صالح (راجع LLM_BASE_URL).") from exc


@router.get("/applications/{application_id}", response_model=ExplanationResponse, summary="اشرح حالة طلب")
def explain_application(
    application_id: int,
    current_user: User = Depends(require_roles("viewer", "reviewer", "admin")),
    db: Session = Depends(get_db),
) -> ExplanationResponse:
    application = db.scalar(
        select(KYCApplication)
        .where(KYCApplication.id == application_id)
        .options(selectinload(KYCApplication.documents), selectinload(KYCApplication.audit_events))
    )
    if application is None:
        raise HTTPException(status_code=404, detail="طلب العميل غير موجود.")
    llm = _llm()
    facts = build_application_facts(db, application)
    explanation = build_explanation(facts, build_index(db), llm)
    return _respond(db, current_user, facts, explanation, getattr(llm, "model", None))


@router.get("/complaints/{complaint_id}", response_model=ExplanationResponse, summary="اشرح تصنيف شكوى وتوجيهها")
def explain_complaint(
    complaint_id: int,
    current_user: User = Depends(require_roles("viewer", "reviewer", "admin")),
    db: Session = Depends(get_db),
) -> ExplanationResponse:
    complaint = db.get(Complaint, complaint_id)
    if complaint is None:
        raise HTTPException(status_code=404, detail="الشكوى غير موجودة.")
    llm = _llm()
    facts = build_complaint_facts(complaint)
    explanation = build_explanation(facts, build_index(db), llm)
    return _respond(db, current_user, facts, explanation, getattr(llm, "model", None))
