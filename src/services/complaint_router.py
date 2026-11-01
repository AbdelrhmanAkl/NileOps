"""Deterministic complaint classification and routing for NileOps.

The router uses explicit, auditable keyword rules. It never makes a final
decision: low-confidence complaints are flagged for human triage and every
suggested reply is only a draft for an employee to review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CATEGORY_CARDS = "بطاقات"
CATEGORY_ACCOUNTS = "حسابات"
CATEGORY_TRANSFERS = "تحويلات"
CATEGORY_SERVICE = "خدمة العملاء"
CATEGORY_FRAUD = "احتيال وأمان"

CATEGORIES = (
    CATEGORY_CARDS,
    CATEGORY_ACCOUNTS,
    CATEGORY_TRANSFERS,
    CATEGORY_SERVICE,
    CATEGORY_FRAUD,
)

DEPARTMENT_BY_CATEGORY = {
    CATEGORY_CARDS: "إدارة البطاقات",
    CATEGORY_ACCOUNTS: "خدمة الحسابات",
    CATEGORY_TRANSFERS: "عمليات التحويلات",
    CATEGORY_SERVICE: "خدمة العملاء",
    CATEGORY_FRAUD: "إدارة مكافحة الاحتيال",
}

DEPARTMENTS = tuple(DEPARTMENT_BY_CATEGORY.values())

BASE_PRIORITY = {
    CATEGORY_CARDS: "high",
    CATEGORY_ACCOUNTS: "medium",
    CATEGORY_TRANSFERS: "urgent",
    CATEGORY_SERVICE: "low",
    CATEGORY_FRAUD: "urgent",
}

PRIORITY_ORDER = ("low", "medium", "high", "urgent")

REASON_BY_CATEGORY = {
    CATEGORY_CARDS: "تم توجيه الشكوى بناءً على تصنيف مشكلة البطاقة.",
    CATEGORY_ACCOUNTS: "تم توجيه الشكوى إلى فريق خدمة الحسابات.",
    CATEGORY_TRANSFERS: "تم تصنيف الشكوى كحالة عاجلة متعلقة بالتحويلات.",
    CATEGORY_SERVICE: "تم توجيه الملاحظة إلى فريق خدمة العملاء.",
    CATEGORY_FRAUD: (
        "تم رصد كلمات مرتبطة بالأمان أو الاشتباه بالاحتيال، "
        "وتم تصعيد الشكوى لمراجعة بشرية عاجلة."
    ),
}

REPLY_BY_CATEGORY = {
    CATEGORY_CARDS: "تم استلام شكواك وسيقوم فريق البطاقات بمراجعتها.",
    CATEGORY_ACCOUNTS: "تم تسجيل طلبك وسيتم مراجعته من فريق الحسابات.",
    CATEGORY_TRANSFERS: "تم تصعيد الشكوى إلى فريق التحويلات لمراجعتها.",
    CATEGORY_SERVICE: "شكرًا لملاحظتك، تم تسجيلها لمراجعتها.",
    CATEGORY_FRAUD: (
        "تم استلام بلاغك وتصعيده فورًا إلى الفريق المختص. "
        "لا تشارك بيانات بطاقتك أو رموز التحقق مع أي جهة."
    ),
}

TRIAGE_REASON = (
    "تعذّر تحديد تصنيف واضح تلقائيًا؛ الشكوى بانتظار فرز موظف مختص."
)
TRIAGE_REPLY = "تم استلام شكواك وسيتم تحويلها للقسم المناسب بعد المراجعة."

# Keywords are written in normalized form (see ``normalize_arabic``).
_KEYWORDS: dict[str, tuple[str, ...]] = {
    CATEGORY_CARDS: (
        "بطاق", "كارت", "فيزا", "ماستركارد", "meeza", "atm", "صراف",
        "الرقم السري", "بن كود", "pin", "كارد",
    ),
    CATEGORY_ACCOUNTS: (
        "حساب", "رصيد", "كشف", "فتح الحساب", "اغلاق", "تجميد", "حسابي",
    ),
    CATEGORY_TRANSFERS: (
        "تحويل", "حواله", "انستاباي", "instapay", "ايداع", "سويفت", "swift",
        "تحويلات",
    ),
    CATEGORY_SERVICE: (
        "ملاحظه", "موظف", "خدمه", "فرع", "انتظار", "تعامل", "شكر", "اقتراح",
    ),
}

# Generic words appear in many complaints (e.g. "the account" inside a transfer
# complaint), so they count for less than category-specific words.
_GENERIC_WEIGHT = 0.5
_GENERIC_KEYWORDS = {"حساب", "حسابي", "رصيد", "فرع", "خدمه", "كشف"}

_FRAUD_KEYWORDS = (
    "احتيال", "نصب", "سرق", "مسروق", "اختراق", "مخترق", "غير معروف",
    "مش انا", "لم اقم", "غير مصرح", "رساله مشبوهه", "مشبوه", "fraud",
    "ضاعت", "فقدت", "فقد بطاقه",
)

# Billing problems that should be handled urgently without being fraud.
_ESCALATION_COMBOS = (("خصم", "مرتين"), ("خصم", "مكرر"), ("خصم", "مرتين"))

_URGENCY_WORDS = ("عاجل", "فورا", "حالا", "ضروري جدا", "urgent")

_DIACRITICS = re.compile(r"[ـً-ٰٟ]")
_TOKEN = re.compile(r"[؀-ۿa-zA-Z0-9]+")
_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال", "و", "ب", "ل", "ف")


def normalize_arabic(text: str) -> str:
    """Lower-case and normalize Arabic letters for rule matching."""
    text = _DIACRITICS.sub("", text.lower())
    for src, dst in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه")):
        text = text.replace(src, dst)
    return text


def _token_variants(token: str) -> set[str]:
    """Return the token plus versions with up to two leading prefixes removed."""
    variants = {token}
    frontier = {token}
    for _ in range(2):
        next_frontier: set[str] = set()
        for current in frontier:
            for prefix in _PREFIXES:
                if current.startswith(prefix) and len(current) - len(prefix) >= 3:
                    next_frontier.add(current[len(prefix):])
        variants |= next_frontier
        frontier = next_frontier
    return variants


def _matches(keyword: str, text: str, token_variants: list[set[str]]) -> bool:
    if " " in keyword:
        return keyword in text
    return any(
        variant.startswith(keyword)
        for variants in token_variants
        for variant in variants
    )


def _bump(priority: str) -> str:
    index = PRIORITY_ORDER.index(priority)
    return PRIORITY_ORDER[min(index + 1, len(PRIORITY_ORDER) - 1)]


@dataclass(frozen=True)
class ComplaintClassification:
    category: str
    priority: str
    department: str | None
    routing_reason: str
    suggested_reply: str
    needs_human_triage: bool
    matched_keywords: tuple[str, ...] = field(default_factory=tuple)


def classify_complaint(
    description: str,
    category_hint: str | None = None,
) -> ComplaintClassification:
    """Classify a complaint with fixed rules.

    ``category_hint`` (chosen by an employee) overrides keyword scoring but
    security keywords can still escalate priority.
    """
    text = normalize_arabic(description)
    token_variants = [_token_variants(t) for t in _TOKEN.findall(text)]

    fraud_hits = tuple(
        kw for kw in _FRAUD_KEYWORDS if _matches(normalize_arabic(kw), text, token_variants)
    )
    urgency_hit = any(
        normalize_arabic(w) in text for w in _URGENCY_WORDS
    )

    escalation_hit = any(
        all(_matches(part, text, token_variants) for part in combo)
        for combo in _ESCALATION_COMBOS
    )

    scores: dict[str, list[str]] = {}
    for category, keywords in _KEYWORDS.items():
        hits = [kw for kw in keywords if _matches(kw, text, token_variants)]
        if hits:
            scores[category] = hits

    def score(hits: list[str]) -> float:
        return sum(_GENERIC_WEIGHT if kw in _GENERIC_KEYWORDS else 1.0 for kw in hits)

    if category_hint:
        category = category_hint
        confident = True
        matched = tuple(scores.get(category, ()))
    elif fraud_hits:
        category = CATEGORY_FRAUD
        confident = True
        matched = fraud_hits
    else:
        ranked = sorted(scores.items(), key=lambda item: score(item[1]), reverse=True)
        if not ranked:
            category, confident, matched = CATEGORY_SERVICE, False, ()
        elif len(ranked) > 1 and score(ranked[0][1]) == score(ranked[1][1]):
            category, confident, matched = CATEGORY_SERVICE, False, tuple(
                kw for _, hits in ranked for kw in hits
            )
        else:
            category, confident, matched = ranked[0][0], True, tuple(ranked[0][1])

    priority = BASE_PRIORITY.get(category, "medium")
    if fraud_hits or escalation_hit:
        priority = "urgent"
    elif urgency_hit:
        priority = _bump(priority)

    if not confident:
        return ComplaintClassification(
            category=category,
            priority=priority,
            department=None,
            routing_reason=TRIAGE_REASON,
            suggested_reply=TRIAGE_REPLY,
            needs_human_triage=True,
            matched_keywords=matched,
        )

    department = DEPARTMENT_BY_CATEGORY.get(category)
    return ComplaintClassification(
        category=category,
        priority=priority,
        department=department,
        routing_reason=REASON_BY_CATEGORY.get(category, TRIAGE_REASON),
        suggested_reply=REPLY_BY_CATEGORY.get(category, TRIAGE_REPLY),
        needs_human_triage=department is None,
        matched_keywords=matched,
    )
