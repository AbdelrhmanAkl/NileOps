"""Unit tests for the explanation output guard."""

import pytest

from src.explain.guard import validate_explanation

ALLOWED = (
    "حالة الطلب الحالية: «قيد المراجعة». المستند «إثبات العنوان» غير مرفق. "
    "القراءة الآلية لـ«بطاقة الرقم القومي» أظهرت حقولًا تحتاج مراجعة: الرقم القومي. "
    "الشكوى عاجلة، والمهلة 4 ساعات. قرار قبول الطلب أو رفضه يتخذه المراجع البشري وحده."
)


def check(text):
    return validate_explanation(text, ALLOWED)


def test_grounded_arabic_explanation_is_accepted():
    ok, reason = check(
        "الطلب قيد المراجعة لأن المستند «إثبات العنوان» غير مرفق، "
        "والقراءة الآلية أظهرت حقولًا تحتاج مراجعة، والقرار للمراجع البشري."
    )
    assert (ok, reason) == (True, "ok")


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("قصير", "length"),
        ("ا" * 1000, "length"),
        ("المهلة 9 ساعات للمستند «إثبات العنوان» غير مرفق وحالة الطلب قيد المراجعة.", "ungrounded_number"),
        ("المهلة ٩ ساعات للمستند «إثبات العنوان» غير مرفق وحالة الطلب قيد المراجعة.", "ungrounded_number"),
        ("راجع الرابط https://evil.example لمعرفة حالة الطلب قيد المراجعة والمستند غير مرفق.", "markup"),
        ("```python\nprint('x')\n``` حالة الطلب قيد المراجعة والمستند غير مرفق", "markup"),
        ("The application is under review because the address proof document is missing.", "language"),
        ("نوصي باعتماد الطلب لأن المستند «إثبات العنوان» غير مرفق وحالة الطلب قيد المراجعة.", "decision_language"),
        ("يجب رفض الطلب لأن المستند «إثبات العنوان» غير مرفق وحالة الطلب قيد المراجعة.", "decision_language"),
        ("تم اعتماد الطلب لأن حالة الطلب قيد المراجعة والمستند «إثبات العنوان» غير مرفق.", "decision_language"),
        ("العميل مشبوه وسبق أن احتال على فروع أخرى وهو معروف لدى الشرطة وعليه قضايا كثيرة جدا.", "ungrounded_text"),
    ],
)
def test_bad_outputs_are_rejected(text, reason):
    assert check(text) == (False, reason)


def test_stating_that_the_decision_is_human_is_not_flagged():
    ok, _ = check("قرار قبول الطلب أو رفضه يتخذه المراجع البشري وحده والمستند «إثبات العنوان» غير مرفق.")
    assert ok
