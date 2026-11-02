"""Shared helpers for OCR-related tests (no OCR binary needed)."""

from src.ocr.engine import OcrPage, OcrResult, OcrWord


def make_ocr(lines: list[tuple[str, float]], engine: str = "fake") -> OcrResult:
    """Build an OcrResult from ``(text, confidence)`` lines, top to bottom.

    Words are placed right-to-left so ``OcrPage.lines()`` restores reading order.
    """
    words: list[OcrWord] = []
    for line_index, (text, confidence) in enumerate(lines):
        tokens = text.split()
        for token_index, token in enumerate(tokens):
            words.append(
                OcrWord(
                    text=token,
                    confidence=confidence,
                    left=1000 - token_index * 100,
                    top=100 + line_index * 80,
                    width=90,
                    height=40,
                    line_id=line_index + 1,
                )
            )
    return OcrResult(engine=engine, languages="ara+eng", pages=[OcrPage(page=1, words=words)])


ID_CARD_LINES = [
    ("بطاقة تحقيق الشخصية (نموذج اصطناعي)", 0.92),
    ("الاسم: أحمد محمود علي", 0.93),
    ("الرقم القومي: 29801011234567", 0.95),
    ("العنوان: 12 شارع النيل الجيزة", 0.90),
    ("تاريخ الانتهاء: 2032/07/12", 0.94),
]
