"""Build the facts an explanation is allowed to talk about.

Security rule: facts are composed *by this code* from fixed vocabularies
(enums, known document types, categories, departments). Free text written by
people or read from documents (complaint descriptions, reviewer notes, customer
names, OCR values, stored "details" strings) is never copied into a fact. So
nothing a customer or employee typed can reach the language model's prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.banking import Complaint
from src.models.documents import DocumentFile, ExtractedField
from src.models.kyc import ApplicationStatus, DocumentStatus, KYCApplication
from src.services.complaint_router import (
    BASE_PRIORITY,
    CATEGORIES,
    DEPARTMENTS,
    PRIORITY_ORDER,
    REASON_BY_CATEGORY,
    classify_complaint,
)
from src.services.kyc_validator import REQUIRED_DOCUMENTS

UNKNOWN = "غير معروفة"

FIELD_LABELS = {
    "full_name": "الاسم",
    "national_id": "الرقم القومي",
    "address": "العنوان",
    "id_expiry_date": "تاريخ انتهاء البطاقة",
    "mobile": "رقم الموبايل",
}
PRIORITY_AR = {"low": "منخفضة", "medium": "متوسطة", "high": "عالية", "urgent": "عاجلة"}
COMPLAINT_STATUS_AR = {
    "new": "جديدة",
    "classified": "مُصنَّفة",
    "assigned": "مُسندة لقسم",
    "in_progress": "قيد المعالجة",
    "resolved": "تم حلها",
    "closed": "مغلقة",
}
_APPLICATION_STATUSES = {s.value for s in ApplicationStatus}
_DOCUMENT_STATUSES = {s.value for s in DocumentStatus}


@dataclass(frozen=True)
class Fact:
    code: str
    text: str


@dataclass
class ExplainFacts:
    subject_type: str  # "application" | "complaint"
    subject_id: int
    label: str
    facts: list[Fact] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    policy_queries: list[str] = field(default_factory=list)
    sla_priority: str | None = None


def _known(value: str | None, allowed) -> str:
    return value if value in allowed else UNKNOWN


def build_application_facts(db: Session, application: KYCApplication) -> ExplainFacts:
    out = ExplainFacts("application", application.id, application.application_number)
    status = _known(application.status, _APPLICATION_STATUSES)
    out.facts.append(Fact("APP_STATUS", f"حالة الطلب الحالية: «{status}»."))

    by_type = {d.document_type: d for d in application.documents}
    missing_or_unverified = flagged_ocr = False

    for doc_type in REQUIRED_DOCUMENTS:
        document = by_type.get(doc_type)
        doc_status = document.status if document else DocumentStatus.MISSING.value
        doc_status = _known(doc_status, _DOCUMENT_STATUSES)

        if doc_status == DocumentStatus.MISSING.value:
            out.facts.append(Fact("DOC_MISSING", f"المستند «{doc_type}» غير مرفق."))
            out.next_steps.append(f"اطلب من العميل استكمال «{doc_type}».")
            missing_or_unverified = True
        elif doc_status in (DocumentStatus.INVALID.value, DocumentStatus.EXPIRED.value):
            out.facts.append(Fact("DOC_REVIEW", f"المستند «{doc_type}» حالته «{doc_status}» ويحتاج إلى فحص بشري."))
            out.next_steps.append(f"افحص «{doc_type}» بنفسك قبل أي قرار.")
            missing_or_unverified = True
        elif doc_status == DocumentStatus.UNDER_REVIEW.value:
            out.facts.append(Fact("DOC_PENDING", f"فحص المستند «{doc_type}» لم يكتمل بعد."))
            missing_or_unverified = True
        elif doc_status == DocumentStatus.UPLOADED.value:
            out.facts.append(Fact("DOC_UNVERIFIED", f"المستند «{doc_type}» مرفق لكن لم يُتحقق من محتواه بعد."))
            missing_or_unverified = True
        elif doc_status == DocumentStatus.VALID.value:
            out.facts.append(Fact("DOC_VALID", f"المستند «{doc_type}» مستوفٍ مبدئيًا، وهذا لا يثبت اكتمال كل الضوابط."))
        else:
            out.facts.append(Fact("DOC_UNKNOWN", f"حالة المستند «{doc_type}» غير معروفة للنظام."))
            missing_or_unverified = True

        if document is not None:
            flagged_ocr |= _add_ocr_facts(db, out, doc_type, document.id)

    reviews = [e for e in application.audit_events if e.action == "HUMAN_REVIEW"]
    if reviews:
        last = max(reviews, key=lambda e: e.id)
        decision = _known(last.new_status, _APPLICATION_STATUSES)
        out.facts.append(Fact("HUMAN_REVIEW", f"سُجّل قرار بشري سابق على الطلب: «{decision}»."))

    out.facts.append(Fact("HUMAN_DECISION", "قرار قبول الطلب أو رفضه يتخذه المراجع البشري وحده."))
    if not missing_or_unverified and not flagged_ocr:
        out.next_steps.append("المستندات مستوفاة مبدئيًا؛ أكمل مراجعتك البشرية قبل القرار.")

    if missing_or_unverified:
        out.policy_queries.append("المستندات المطلوبة لفتح الحساب")
    if flagged_ocr:
        out.policy_queries.append("القراءة الآلية للمستندات حقل منخفض الثقة يراجعه الموظف")
    out.policy_queries.append("قرار قبول طلب فتح الحساب أو رفضه قرار بشري")
    return out


def _add_ocr_facts(db: Session, out: ExplainFacts, doc_type: str, document_id: int) -> bool:
    file = db.scalar(
        select(DocumentFile).where(DocumentFile.document_id == document_id).order_by(DocumentFile.id.desc())
    )
    if file is None:
        return False
    if file.ocr_status == "needs_review":
        names = db.scalars(
            select(ExtractedField.field_name).where(
                ExtractedField.document_file_id == file.id, ExtractedField.needs_review.is_(True)
            )
        ).all()
        labels = sorted({FIELD_LABELS[n] for n in names if n in FIELD_LABELS})
        listed = "، ".join(labels) if labels else "حقول غير محددة"
        out.facts.append(
            Fact("OCR_REVIEW", f"القراءة الآلية لـ«{doc_type}» أظهرت حقولًا تحتاج مراجعة: {listed}.")
        )
        out.next_steps.append(f"راجع حقول «{doc_type}» المعلَّمة ({listed}) على المستند الأصلي.")
        return True
    if file.ocr_status == "completed":
        out.facts.append(Fact("OCR_OK", f"القراءة الآلية لـ«{doc_type}» اكتملت دون حقول معلَّمة، والقرار للمراجع."))
    elif file.ocr_status in {"failed", "unavailable"}:
        out.facts.append(Fact("OCR_FAILED", f"تعذرت القراءة الآلية لـ«{doc_type}»؛ المراجعة يدوية بالكامل."))
        out.next_steps.append(f"اقرأ «{doc_type}» يدويًا.")
        return True
    return False


def build_complaint_facts(complaint: Complaint) -> ExplainFacts:
    out = ExplainFacts("complaint", complaint.id, complaint.complaint_number)
    category = _known(complaint.category, CATEGORIES)
    priority = complaint.priority if complaint.priority in PRIORITY_AR else None
    status = COMPLAINT_STATUS_AR.get(complaint.status, UNKNOWN)

    out.facts.append(Fact("CATEGORY", f"تصنيف الشكوى: «{category}»."))
    triage = complaint.assigned_department is None and complaint.status in {"new", "classified"}
    if category == "خدمة العملاء" and triage:
        out.facts.append(Fact("TRIAGE", "لم يستطع النظام تحديد التصنيف بثقة كافية، لذلك تنتظر الفرز البشري."))
        out.next_steps.append("حدّد القسم المناسب يدويًا بعد قراءة الشكوى.")
        out.policy_queries.append("إذا لم يكن التصنيف واضحا يقوم موظف بالفرز يدويا")
    elif category != UNKNOWN:
        out.facts.append(Fact("REASON", REASON_BY_CATEGORY[category]))
    if complaint.assigned_department in DEPARTMENTS:
        out.facts.append(Fact("DEPARTMENT", f"الشكوى مُسندة إلى «{complaint.assigned_department}»."))

    if priority:
        out.facts.append(Fact("PRIORITY", f"أولوية الشكوى: «{PRIORITY_AR[priority]}»."))
        out.sla_priority = priority
        base = BASE_PRIORITY.get(category)
        if base and PRIORITY_ORDER.index(priority) > PRIORITY_ORDER.index(base):
            out.facts.append(
                Fact("ESCALATED", "رُفعت الأولوية عن المعتاد لهذا التصنيف بسبب كلمات تدل على عجلة أو احتيال أو خصم مكرر.")
            )
        if category != UNKNOWN:
            # Re-classify to recover the system keywords that matched; only our own
            # vocabulary is reported, never the complaint text itself.
            again = classify_complaint(complaint.description)
            if again.category == category and again.matched_keywords:
                words = "، ".join(dict.fromkeys(again.matched_keywords))
                out.facts.append(Fact("KEYWORDS", f"كلمات دلّت على التصنيف: {words}."))

    out.facts.append(Fact("STATUS", f"حالة المعالجة الحالية: {status}."))
    out.facts.append(Fact("DRAFT_ONLY", "الرد المقترح للعميل مسودة، ولا يُرسل دون موافقة موظف."))
    out.next_steps.append("راجع نص الشكوى بنفسك وتأكد أن التصنيف والأولوية مناسبان.")

    out.policy_queries.append("مهل الرد على الشكوى حسب الأولوية")
    if category == "احتيال وأمان":
        out.policy_queries.append("بلاغ العميل عن عملية مشبوهة احتيال تجمد البطاقة الإجراء الفوري")
    return out
