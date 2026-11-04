"""Validate a language model's explanation before an employee ever sees it."""

from __future__ import annotations

import re

from src.rag.retrieval import tokenize
from src.services.complaint_router import normalize_arabic

MAX_LENGTH = 900
MIN_LENGTH = 20
MAX_NOVEL_RATIO = 0.4
MIN_ARABIC_SHARE = 0.7

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_MARKUP = re.compile(r"https?://|www\.|```|<[^>]+>|[{}]|\[[^\]]*\]\(")
_DECISION = re.compile(
    r"(نوصي|اوصي|يوصي|انصح|ينصح)"
    r"|(يجب|ينبغي|عليك|لازم)\s+(ان\s+)?(اعتماد|رفض|قبول|الموافقه|تعتمد|ترفض|توافق|تقبل)"
    r"|(تم|تمت)\s+(اعتماد|رفض|قبول|الموافقه)"
)
_LETTER = re.compile(r"[A-Za-z؀-ۿ]")
_ARABIC_LETTER = re.compile(r"[؀-ۿ]")


def _numbers(text: str) -> set[str]:
    return {n.rstrip(".") for n in _NUMBER.findall(text.translate(_ARABIC_DIGITS).replace(",", ""))}


def validate_explanation(output: str, allowed_text: str) -> tuple[bool, str]:
    """Return (accepted, reason). ``allowed_text`` is everything the model was shown."""
    text = output.strip()
    if not MIN_LENGTH <= len(text) <= MAX_LENGTH:
        return False, "length"
    if _MARKUP.search(text):
        return False, "markup"
    letters = _LETTER.findall(text)
    if not letters or len(_ARABIC_LETTER.findall(text)) / len(letters) < MIN_ARABIC_SHARE:
        return False, "language"
    if _DECISION.search(normalize_arabic(text)):
        return False, "decision_language"
    if not _numbers(text) <= _numbers(allowed_text):
        return False, "ungrounded_number"
    terms = [t for t in tokenize(text) if not t.isdigit()]
    known = set(tokenize(allowed_text))
    if terms and sum(t not in known for t in terms) / len(terms) > MAX_NOVEL_RATIO:
        return False, "ungrounded_text"
    return True, "ok"
