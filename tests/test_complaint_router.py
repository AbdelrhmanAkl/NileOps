"""Unit tests for the deterministic complaint router."""

import pytest

from src.services.complaint_router import (
    CATEGORY_ACCOUNTS,
    CATEGORY_CARDS,
    CATEGORY_FRAUD,
    CATEGORY_SERVICE,
    CATEGORY_TRANSFERS,
    DEPARTMENT_BY_CATEGORY,
    classify_complaint,
    normalize_arabic,
)


@pytest.mark.parametrize(
    "description,category,priority",
    [
        ("العميل يواجه مشكلة في استخدام البطاقة.", CATEGORY_CARDS, "high"),
        ("العميل يستفسر عن حالة حسابه.", CATEGORY_ACCOUNTS, "medium"),
        ("العميل يستفسر عن تحويل مالي لم يظهر في الحساب.", CATEGORY_TRANSFERS, "urgent"),
        ("العميل يرغب في تقديم ملاحظة حول الخدمة.", CATEGORY_SERVICE, "low"),
    ],
)
def test_seed_scenarios_are_classified_like_the_seed_data(description, category, priority):
    result = classify_complaint(description)

    assert result.category == category
    assert result.priority == priority
    assert result.department == DEPARTMENT_BY_CATEGORY[category]
    assert result.needs_human_triage is False
    assert result.matched_keywords


@pytest.mark.parametrize(
    "description",
    [
        "تم سرقة بطاقتي من المحفظة",
        "حدث اختراق لحسابي وعمليات لم اقم بها",
        "وصلتني رسالة مشبوهة تطلب بيانات الحساب",
    ],
)
def test_security_keywords_route_to_fraud_team_as_urgent(description):
    result = classify_complaint(description)

    assert result.category == CATEGORY_FRAUD
    assert result.priority == "urgent"
    assert result.department == DEPARTMENT_BY_CATEGORY[CATEGORY_FRAUD]
    assert result.needs_human_triage is False


def test_urgency_words_raise_priority_one_level():
    normal = classify_complaint("استفسار عن رصيد الحساب")
    urgent = classify_complaint("استفسار عاجل عن رصيد الحساب")

    assert normal.priority == "medium"
    assert urgent.priority == "high"


def test_unclear_text_goes_to_human_triage_without_department():
    result = classify_complaint("اريد ان اتحدث مع احد بخصوص موضوع مهم جدا")

    assert result.needs_human_triage is True
    assert result.department is None


def test_tie_between_categories_goes_to_human_triage():
    result = classify_complaint("مشكلة في البطاقة وايضا في تحويل")

    assert result.needs_human_triage is True
    assert result.department is None


def test_category_hint_overrides_keywords_but_security_still_escalates():
    hinted = classify_complaint("استفسار عام", category_hint=CATEGORY_ACCOUNTS)
    assert hinted.category == CATEGORY_ACCOUNTS
    assert hinted.needs_human_triage is False

    escalated = classify_complaint("تم اختراق حسابي", category_hint=CATEGORY_ACCOUNTS)
    assert escalated.category == CATEGORY_ACCOUNTS
    assert escalated.priority == "urgent"


def test_arabic_prefixes_and_diacritics_are_handled():
    assert classify_complaint("البطاقَة لا تعمل").category == CATEGORY_CARDS
    assert classify_complaint("وبالحساب مشكلة").category == CATEGORY_ACCOUNTS


def test_feature_word_is_not_mistaken_for_meeza_card():
    result = classify_complaint("اقترح اضافة ميزة جديدة للتطبيق")

    assert result.category != CATEGORY_CARDS


def test_normalize_arabic():
    assert normalize_arabic("إِلْغَاءُ البِطَاقَةِ") == "الغاء البطاقه"


def test_suggested_reply_is_always_present_and_is_draft_text():
    for text in ("مشكلة في البطاقة", "نص غير واضح تماما هنا"):
        assert classify_complaint(text).suggested_reply


@pytest.mark.parametrize(
    "description",
    ["بطاقتي لا تعمل في الصراف", "مشكلة في بطاقاتي الاثنتين", "كارتي ابتلعه الجهاز"],
)
def test_possessive_and_plural_card_words_match(description):
    assert classify_complaint(description).category == CATEGORY_CARDS


def test_double_charge_is_urgent_but_not_fraud():
    result = classify_complaint("تم خصم مبلغ مرتين من بطاقتي")

    assert result.category == CATEGORY_CARDS
    assert result.priority == "urgent"
    assert result.department == DEPARTMENT_BY_CATEGORY[CATEGORY_CARDS]
