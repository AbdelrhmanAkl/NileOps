"""Rule-based field extraction from OCR output.

Every field carries a confidence score and a source location (page, line and
bounding box), and anything uncertain is flagged for human review. The module
never decides whether a document is valid; it only reports what was read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from src.ocr.engine import OcrPage, OcrResult, OcrWord

# --- document types ---------------------------------------------------------
DOC_NATIONAL_ID = "بطاقة الرقم القومي"
DOC_ADDRESS_PROOF = "إثبات العنوان"
DOC_CUSTOMER_FORM = "استمارة بيانات العميل"

EXPECTED_FIELDS: dict[str, tuple[str, ...]] = {
    DOC_NATIONAL_ID: ("full_name", "national_id", "address", "id_expiry_date"),
    DOC_ADDRESS_PROOF: ("full_name", "address"),
    DOC_CUSTOMER_FORM: ("full_name", "national_id", "mobile", "address"),
}

LABELS: dict[str, tuple[str, ...]] = {
    "full_name": ("الاسم",),
    "national_id": ("الرقم القومي", "رقم قومي"),
    "address": ("العنوان",),
    "id_expiry_date": ("تاريخ الانتهاء", "صالحة حتى", "تاريخ انتهاء"),
    "mobile": ("رقم الهاتف", "رقم الموبايل", "الهاتف", "الموبايل", "المحمول"),
}

# Valid Egyptian governorate codes used in positions 8-9 of the national ID.
GOVERNORATE_CODES = {
    "01", "02", "03", "04", "11", "12", "13", "14", "15", "16", "17", "18",
    "19", "21", "22", "23", "24", "25", "26", "27", "28", "29", "31", "32",
    "33", "34", "35", "88",
}

_DIGIT_TRANSLATION = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"
)
_MARKS = re.compile(r"[‎‏‪-‮؜ـً-ٟ]")
_DATE_RE = re.compile(r"(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})|(\d{1,2})\D{1,3}(\d{1,2})\D{1,3}(\d{4})")
_MOBILE_RE = re.compile(r"(?<!\d)(01[0125]\d{8})(?!\d)")
_ID_RE = re.compile(r"(?<!\d)(\d{14})(?!\d)")


def normalize_digits(text: str) -> str:
    return text.translate(_DIGIT_TRANSLATION)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", _MARKS.sub("", text)).strip()


@dataclass(frozen=True)
class ExtractedFieldValue:
    name: str
    value: str
    confidence: float
    needs_review: bool
    review_reason: str | None
    source: dict
    method: str


@dataclass
class ExtractionResult:
    document_type: str
    fields: list[ExtractedFieldValue] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    derived: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> ExtractedFieldValue | None:
        return next((f for f in self.fields if f.name == name), None)

    @property
    def requires_human_review(self) -> bool:
        return bool(self.missing_fields) or any(f.needs_review for f in self.fields)


@dataclass(frozen=True)
class NationalIdInfo:
    valid: bool
    birth_date: date | None
    gender: str | None
    reason: str | None


def parse_national_id(number: str) -> NationalIdInfo:
    """Check the structure of a 14-digit Egyptian national ID number."""
    if not re.fullmatch(r"\d{14}", number):
        return NationalIdInfo(False, None, None, "الرقم القومي يجب أن يتكون من 14 رقمًا")
    century = {"2": 1900, "3": 2000}.get(number[0])
    if century is None:
        return NationalIdInfo(False, None, None, "رقم القرن في الرقم القومي غير صالح")
    try:
        birth = date(century + int(number[1:3]), int(number[3:5]), int(number[5:7]))
    except ValueError:
        return NationalIdInfo(False, None, None, "تاريخ الميلاد داخل الرقم القومي غير صالح")
    if number[7:9] not in GOVERNORATE_CODES:
        return NationalIdInfo(False, birth, None, "كود المحافظة داخل الرقم القومي غير صالح")
    gender = "ذكر" if int(number[12]) % 2 == 1 else "أنثى"
    return NationalIdInfo(True, birth, gender, None)


def parse_date(text: str) -> date | None:
    match = _DATE_RE.search(normalize_digits(text))
    if not match:
        return None
    try:
        if match.group(1):
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return date(int(match.group(6)), int(match.group(5)), int(match.group(4)))
    except ValueError:
        return None


# --- helpers over OCR lines ---------------------------------------------------
@dataclass(frozen=True)
class _Line:
    page: int
    index: int
    words: list[OcrWord]

    @property
    def text(self) -> str:
        return clean_text(" ".join(w.text for w in self.words))

    @property
    def confidence(self) -> float:
        if not self.words:
            return 0.0
        return sum(w.confidence for w in self.words) / len(self.words)

    def bbox(self) -> list[int]:
        left = min(w.left for w in self.words)
        top = min(w.top for w in self.words)
        right = max(w.left + w.width for w in self.words)
        bottom = max(w.top + w.height for w in self.words)
        return [left, top, right - left, bottom - top]


def _lines(pages: list[OcrPage]) -> list[_Line]:
    out: list[_Line] = []
    for page in pages:
        # Visual top-to-bottom order across blocks.
        grouped = sorted(page.lines(), key=lambda ws: min(w.top for w in ws))
        for i, words in enumerate(grouped):
            out.append(_Line(page.page, i, words))
    return out


def _value_after_label(text: str, labels: tuple[str, ...]) -> str | None:
    """Return the text after ``label:`` (also tolerates a label read slightly off)."""
    for label in labels:
        pattern = re.compile(re.escape(label) + r"\s*[:：\-]?\s*(.+)$")
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return None


def _confidence_of(line: _Line, value: str) -> float:
    """Average confidence of the words that make up ``value`` (fallback: line)."""
    tokens = {normalize_digits(clean_text(t)) for t in value.split()}
    scores = [
        w.confidence
        for w in line.words
        if normalize_digits(clean_text(w.text)).strip(":") in tokens
    ]
    return sum(scores) / len(scores) if scores else line.confidence


def _min_word_confidence(line: _Line, value: str) -> float:
    tokens = {normalize_digits(clean_text(t)) for t in value.split()}
    scores = [
        w.confidence
        for w in line.words
        if normalize_digits(clean_text(w.text)).strip(":") in tokens
    ]
    return min(scores) if scores else line.confidence


_LATIN_RUN = re.compile(r"[A-Za-z]{2,}")
_NON_NAME_WORDS = ("بطاقة", "إثبات", "اثبات", "استمارة", "نموذج", "اصطناعي", "SYNTHETIC", "فاتورة")
WORD_REVIEW_THRESHOLD = 0.55


def _source(line: _Line, text: str) -> dict:
    return {"page": line.page, "line": line.index, "bbox": line.bbox(), "line_text": text}


def _make(
    name: str,
    value: str,
    confidence: float,
    line: _Line,
    method: str,
    min_conf: float,
    extra_reason: str | None = None,
) -> ExtractedFieldValue:
    reason = extra_reason
    if reason is None and confidence < min_conf:
        reason = "ثقة القراءة منخفضة"
    return ExtractedFieldValue(
        name=name,
        value=value,
        confidence=round(confidence, 3),
        needs_review=reason is not None,
        review_reason=reason,
        source=_source(line, line.text),
        method=method,
    )


def extract_fields(
    ocr: OcrResult,
    document_type: str,
    min_confidence: float = 0.80,
) -> ExtractionResult:
    """Extract the expected fields of ``document_type`` from OCR output."""
    expected = EXPECTED_FIELDS.get(document_type, ())
    lines = _lines(ocr.pages)
    result = ExtractionResult(document_type=document_type)

    found: dict[str, ExtractedFieldValue] = {}

    for name in expected:
        item = _extract_one(name, lines, min_confidence)
        if item:
            found[name] = item

    # Derived values come from the number itself, not from separate OCR text.
    nid = found.get("national_id")
    if nid:
        info = parse_national_id(nid.value)
        if info.valid and info.birth_date:
            result.derived["birth_date"] = info.birth_date.isoformat()
            result.derived["gender"] = info.gender or ""

    result.fields = [found[n] for n in expected if n in found]
    result.missing_fields = [n for n in expected if n not in found]
    return result


def _extract_one(name: str, lines: list[_Line], min_conf: float) -> ExtractedFieldValue | None:
    if name == "national_id":
        return _extract_national_id(lines, min_conf)
    if name == "mobile":
        return _extract_mobile(lines, min_conf)
    if name == "id_expiry_date":
        return _extract_expiry(lines, min_conf)
    return _extract_labeled_text(name, lines, min_conf)


def _extract_labeled_text(name: str, lines: list[_Line], min_conf: float) -> ExtractedFieldValue | None:
    best: tuple[float, _Line, str] | None = None
    for line in lines:
        text = line.text
        value = _value_after_label(text, LABELS[name])
        if not value:
            # Label may be read poorly: accept the last labelled piece for names.
            continue
        score = _confidence_of(line, value)
        if best is None or score > best[0]:
            best = (score, line, value)
    if best is None:
        # OCR can mangle a short label; fall back to an Arabic multi-word line.
        if name == "full_name":
            return _name_fallback(lines, min_conf)
        return None
    score, line, value = best
    raw_value = value
    value = re.sub(r"\s*[|\[\]{}]+\s*", " ", value).strip(" :،,-")
    if name == "address":
        value = normalize_digits(value)
    reason = None
    if _LATIN_RUN.search(value):
        reason = "أحرف لاتينية غير متوقعة داخل نص عربي"
    elif _min_word_confidence(line, raw_value) < WORD_REVIEW_THRESHOLD:
        reason = "إحدى الكلمات مقروءة بثقة منخفضة"
    return _make(name, value, score, line, "label", min_conf, reason)


_LABEL_FRAGMENTS = {"سم", "اسم", "الاسم", "الا", "لاسم"}
_ARABIC_PHRASE = re.compile(r"([\u0600-\u06FF]{2,}(?: [\u0600-\u06FF]{2,}){1,4})$")


def _name_fallback(lines: list[_Line], min_conf: float) -> ExtractedFieldValue | None:
    other_labels = [l for k, v in LABELS.items() if k != "full_name" for l in v]
    best: tuple[float, _Line, str] | None = None
    for line in lines:
        text = line.text
        if any(l in text for l in other_labels) or any(w in text for w in _NON_NAME_WORDS):
            continue
        match = _ARABIC_PHRASE.search(clean_text(re.sub(r"[^\u0600-\u06FF\s]", " ", text)))
        if not match:
            continue
        tokens = match.group(1).split()
        while tokens and tokens[0] in _LABEL_FRAGMENTS:
            tokens.pop(0)  # remains of a garbled "الاسم" label
        if len(tokens) < 2:
            continue
        value = " ".join(tokens)
        if best is None or line.confidence > best[0]:
            best = (line.confidence, line, value)
    if best is None:
        return None
    _, line, value = best
    return _make(
        "full_name", value, min(_confidence_of(line, value), 0.6), line,
        "label_fallback", min_conf, "التسمية غير واضحة؛ راجع القيمة يدويًا",
    )


def _extract_national_id(lines: list[_Line], min_conf: float) -> ExtractedFieldValue | None:
    candidates: list[tuple[int, float, _Line, str]] = []
    for line in lines:
        text = normalize_digits(line.text)
        labelled = any(label in line.text for label in LABELS["national_id"])
        for match in _ID_RE.finditer(text.replace(" ", "")):
            number = match.group(1)
            info = parse_national_id(number)
            rank = (2 if info.valid else 0) + (1 if labelled else 0)
            candidates.append((rank, line.confidence, line, number))
    if not candidates:
        # Labelled line exists but digits are unreadable.
        for line in lines:
            if any(label in line.text for label in LABELS["national_id"]):
                digits = re.sub(r"\D", "", normalize_digits(line.text))
                if digits:
                    return _make(
                        "national_id", digits, min(line.confidence, 0.4), line, "label_partial",
                        min_conf, "الرقم القومي غير مكتمل أو غير مقروء",
                    )
        return None
    rank, conf, line, number = max(candidates, key=lambda c: (c[0], c[1]))
    info = parse_national_id(number)
    reason = None if info.valid else info.reason
    return _make("national_id", number, _confidence_of(line, number), line, "pattern", min_conf, reason)


def _extract_mobile(lines: list[_Line], min_conf: float) -> ExtractedFieldValue | None:
    for line in lines:
        text = normalize_digits(line.text).replace(" ", "")
        match = _MOBILE_RE.search(text)
        if match:
            return _make("mobile", match.group(1), _confidence_of(line, match.group(1)), line, "pattern", min_conf)
    for line in lines:
        if any(label in line.text for label in LABELS["mobile"]):
            digits = re.sub(r"\D", "", normalize_digits(line.text))
            if digits:
                return _make("mobile", digits, min(line.confidence, 0.4), line, "label_partial",
                             min_conf, "رقم الهاتف غير مكتمل أو غير صالح")
    return None


def _extract_expiry(lines: list[_Line], min_conf: float) -> ExtractedFieldValue | None:
    for line in lines:
        if not any(label in line.text for label in LABELS["id_expiry_date"]):
            continue
        parsed = parse_date(line.text)
        if parsed:
            return _make("id_expiry_date", parsed.isoformat(), line.confidence, line, "label", min_conf)
        return _make("id_expiry_date", line.text, min(line.confidence, 0.4), line, "label_partial",
                     min_conf, "تعذر قراءة تاريخ الانتهاء بشكل صحيح")
    return None
