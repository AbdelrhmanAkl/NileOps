"""Integration test against a real Tesseract install (skipped when unavailable)."""

import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont, features

from src.ocr.engine import TesseractEngine
from src.ocr.fields import DOC_NATIONAL_ID, extract_fields

FONTS = [
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]


def _font_path():
    return next((f for f in FONTS if Path(f).exists()), None)


def _has_arabic_model() -> bool:
    import subprocess

    try:
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return "ara" in out.stdout.split()


pytestmark = pytest.mark.skipif(
    shutil.which("tesseract") is None
    or not features.check("raqm")
    or _font_path() is None
    or not _has_arabic_model(),
    reason="needs tesseract with the 'ara' model, Pillow+raqm and an Arabic font",
)


def test_real_ocr_reads_a_synthetic_id_card(tmp_path):
    font = ImageFont.truetype(_font_path(), 40, layout_engine=ImageFont.Layout.RAQM)
    image = Image.new("RGB", (1400, 640), "white")
    draw = ImageDraw.Draw(image)
    rows = [
        "بطاقة تحقيق الشخصية (نموذج اصطناعي)",
        "الاسم: أحمد محمود علي",
        "الرقم القومي: 29801011234567",
        "العنوان: 12 شارع النيل، الجيزة",
        "تاريخ الانتهاء: 2032/07/12",
    ]
    for index, text in enumerate(rows):
        draw.text((1340, 70 + index * 95), text, font=font, fill="black", anchor="ra", direction="rtl", language="ar")
    path = tmp_path / "card.png"
    image.save(path)

    ocr = TesseractEngine().recognize(path, "image/png")
    result = extract_fields(ocr, DOC_NATIONAL_ID)

    # Digit-based fields are the reliable ones; free text must at least be reported.
    assert result.get("national_id").value == "29801011234567"
    assert result.get("id_expiry_date").value == "2032-07-12"
    assert all(0.0 <= f.confidence <= 1.0 for f in result.fields)
    assert all("page" in f.source and "bbox" in f.source for f in result.fields)
