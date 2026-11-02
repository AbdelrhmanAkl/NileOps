"""Cross-checks between OCR-extracted fields and the KYC application.

These checks only *inform* the human reviewer. A failed or unevaluated check
never changes a document or application status by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.models.kyc import KYCApplication
from src.ocr.fields import ExtractionResult, parse_date, parse_national_id
from src.services.complaint_router import normalize_arabic


@dataclass(frozen=True)
class DocumentCheck:
    code: str
    title_ar: str
    passed: bool | None  # None = could not be evaluated
    severity: str  # info | warning | error
    details_ar: str


def _tokens(name: str) -> list[str]:
    return [t for t in normalize_arabic(name).replace("،", " ").split() if t]


def names_match(application_name: str, extracted_name: str) -> bool:
    """True when every word of the application name appears in the extracted name."""
    wanted = _tokens(application_name)
    have = _tokens(extracted_name)
    return bool(wanted) and all(
        any(h == w or h.startswith(w) or w.startswith(h) for h in have) for w in wanted
    )


def run_checks(
    application: KYCApplication,
    extraction: ExtractionResult,
    today: date | None = None,
) -> list[DocumentCheck]:
    today = today or date.today()
    checks: list[DocumentCheck] = []

    name = extraction.get("full_name")
    if name is None:
        checks.append(DocumentCheck(
            "NAME_MATCH", "مطابقة الاسم", None, "warning",
            "لم يتم استخراج الاسم من المستند.",
        ))
    else:
        ok = names_match(application.customer_name, name.value)
        checks.append(DocumentCheck(
            "NAME_MATCH", "مطابقة الاسم", ok, "info" if ok else "warning",
            "الاسم المستخرج يطابق اسم الطلب." if ok else "الاسم المستخرج لا يطابق اسم الطلب.",
        ))

    nid = extraction.get("national_id")
    if nid is not None:
        info = parse_national_id(nid.value)
        checks.append(DocumentCheck(
            "ID_STRUCTURE", "صحة تركيب الرقم القومي", info.valid,
            "info" if info.valid else "warning",
            "تركيب الرقم القومي صالح." if info.valid else (info.reason or "تركيب الرقم القومي غير صالح."),
        ))
        last_four = application.national_id_last_four
        if last_four:
            ok = nid.value.endswith(last_four)
            checks.append(DocumentCheck(
                "ID_LAST4_MATCH", "مطابقة آخر 4 أرقام من الرقم القومي", ok,
                "info" if ok else "warning",
                "آخر 4 أرقام تطابق بيانات الطلب." if ok else "آخر 4 أرقام لا تطابق بيانات الطلب.",
            ))
    elif "national_id" in extraction.missing_fields:
        checks.append(DocumentCheck(
            "ID_STRUCTURE", "صحة تركيب الرقم القومي", None, "warning",
            "لم يتم استخراج الرقم القومي من المستند.",
        ))

    expiry = extraction.get("id_expiry_date")
    if expiry is not None:
        parsed = parse_date(expiry.value)
        if parsed is None:
            checks.append(DocumentCheck(
                "ID_NOT_EXPIRED", "صلاحية المستند", None, "warning",
                "تعذر قراءة تاريخ الانتهاء.",
            ))
        else:
            ok = parsed >= today
            checks.append(DocumentCheck(
                "ID_NOT_EXPIRED", "صلاحية المستند", ok, "info" if ok else "error",
                "المستند ساري." if ok else "المستند منتهي الصلاحية.",
            ))

    low = [f.name for f in extraction.fields if f.needs_review]
    checks.append(DocumentCheck(
        "OCR_CONFIDENCE", "ثقة القراءة الآلية", not low and not extraction.missing_fields,
        "info" if not low and not extraction.missing_fields else "warning",
        "جميع الحقول المتوقعة مقروءة بثقة كافية." if not low and not extraction.missing_fields
        else "بعض الحقول منخفضة الثقة أو مفقودة وتحتاج مراجعة بشرية.",
    ))
    return checks
