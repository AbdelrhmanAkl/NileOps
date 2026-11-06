import pytest

from src.core.pii import redact_pii


@pytest.mark.parametrize(
    "text, hidden",
    [
        ("الرقم القومي 29901011234567 صحيح", "29901011234567"),
        ("الرقم ٢٩٩٠١٠١١٢٣٤٥٦٧ بالهندي", "29901011234567"),
        ("اتصلت على 01012345678 ولم يرد", "01012345678"),
        ("رقم 0101 234 5678 مفصول", "0101 234 5678"),
        ("بطاقة 4111-1111-1111-1111", "4111-1111-1111-1111"),
    ],
)
def test_long_numbers_are_masked(text, hidden):
    out = redact_pii(text)
    assert hidden not in out and "[رقم محجوب]" in out


def test_short_numbers_and_normal_text_are_kept():
    text = "تم رفع 3 مستندات خلال 14 يوم"
    assert redact_pii(text) == text
