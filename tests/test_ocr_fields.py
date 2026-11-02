"""Tests for field extraction, ID parsing and TSV parsing (no OCR binary needed)."""

from datetime import date

import pytest

from src.ocr.engine import parse_tsv
from src.ocr.fields import (
    DOC_ADDRESS_PROOF,
    DOC_CUSTOMER_FORM,
    DOC_NATIONAL_ID,
    extract_fields,
    normalize_digits,
    parse_date,
    parse_national_id,
)
from tests.ocr_helpers import ID_CARD_LINES, make_ocr


def test_parse_national_id_valid_male_and_female():
    male = parse_national_id("29801011234577")  # 13th digit odd = male
    assert male.valid and male.birth_date == date(1998, 1, 1) and male.gender == "ذكر"
    female = parse_national_id("30203150212348")
    assert female.valid and female.birth_date == date(2002, 3, 15) and female.gender == "أنثى"


@pytest.mark.parametrize(
    "number",
    ["1980101123456", "298010112345678", "49801011234567", "29813011234567", "29801019934567", "abcdefghijklmn"],
)
def test_parse_national_id_rejects_bad_structure(number):
    info = parse_national_id(number)
    assert info.valid is False and info.reason


def test_digits_and_dates_are_normalized():
    assert normalize_digits("٢٩٨٠١٠١") == "2980101"
    assert normalize_digits("۲۹۸") == "298"
    assert parse_date("تاريخ الانتهاء: ٢٠٣٢/٠٧/١٢") == date(2032, 7, 12)
    assert parse_date("12/07/2032") == date(2032, 7, 12)
    assert parse_date("2032-13-45") is None
    assert parse_date("no date") is None


def test_national_id_card_fields_confidence_source_and_derived():
    result = extract_fields(make_ocr(ID_CARD_LINES), DOC_NATIONAL_ID)

    values = {f.name: f for f in result.fields}
    assert values["full_name"].value == "أحمد محمود علي"
    assert values["national_id"].value == "29801011234567"
    assert values["address"].value == "12 شارع النيل الجيزة"
    assert values["id_expiry_date"].value == "2032-07-12"
    assert result.missing_fields == []
    assert result.derived == {"birth_date": "1998-01-01", "gender": "أنثى"}  # 13th digit even
    assert result.requires_human_review is False

    source = values["national_id"].source
    assert source["page"] == 1 and len(source["bbox"]) == 4 and "الرقم القومي" in source["line_text"]
    assert 0.9 <= values["national_id"].confidence <= 1.0


def test_eastern_arabic_digits_are_read_as_western():
    lines = list(ID_CARD_LINES)
    lines[2] = ("الرقم القومي: ٢٩٨٠١٠١١٢٣٤٥٦٧", 0.95)
    lines[4] = ("تاريخ الانتهاء: ٢٠٣٢/٠٧/١٢", 0.94)

    result = extract_fields(make_ocr(lines), DOC_NATIONAL_ID)

    assert result.get("national_id").value == "29801011234567"
    assert result.get("id_expiry_date").value == "2032-07-12"


def test_low_confidence_fields_are_flagged_for_review():
    lines = list(ID_CARD_LINES)
    lines[2] = ("الرقم القومي: 29801011234567", 0.55)

    result = extract_fields(make_ocr(lines), DOC_NATIONAL_ID, min_confidence=0.8)

    field = result.get("national_id")
    assert field.needs_review and field.review_reason == "ثقة القراءة منخفضة"
    assert result.requires_human_review is True


def test_invalid_id_structure_is_flagged_even_with_high_confidence():
    lines = list(ID_CARD_LINES)
    lines[2] = ("الرقم القومي: 49801011234567", 0.97)

    field = extract_fields(make_ocr(lines), DOC_NATIONAL_ID).get("national_id")

    assert field.value == "49801011234567"
    assert field.needs_review and "القرن" in field.review_reason


def test_unreadable_id_digits_are_reported_not_invented():
    lines = list(ID_CARD_LINES)
    lines[2] = ("الرقم القومي: 2980101", 0.9)

    field = extract_fields(make_ocr(lines), DOC_NATIONAL_ID).get("national_id")

    assert field.needs_review and field.confidence <= 0.4


def test_missing_fields_are_listed():
    result = extract_fields(make_ocr(ID_CARD_LINES[:3]), DOC_NATIONAL_ID)

    assert set(result.missing_fields) == {"address", "id_expiry_date"}
    assert result.requires_human_review is True


def test_latin_garbage_inside_arabic_text_is_flagged():
    lines = list(ID_CARD_LINES)
    lines[3] = ("العنوان: 12 شارع aus الجيزة", 0.9)

    field = extract_fields(make_ocr(lines), DOC_NATIONAL_ID).get("address")

    assert field.needs_review and "لاتينية" in field.review_reason


def test_single_weak_word_triggers_review_even_when_average_is_high():
    result = make_ocr(ID_CARD_LINES)
    # Lower one word of the address line.
    words = list(result.pages[0].words)
    for i, w in enumerate(words):
        if w.text == "النيل":
            words[i] = type(w)(**{**w.__dict__, "confidence": 0.3})
    result.pages[0].words[:] = words

    field = extract_fields(result, DOC_NATIONAL_ID).get("address")

    assert field.needs_review


def test_garbled_name_label_falls_back_but_always_needs_review():
    lines = list(ID_CARD_LINES)
    lines[1] = ("(سم: أحمد محمود علي", 0.6)

    field = extract_fields(make_ocr(lines), DOC_NATIONAL_ID).get("full_name")

    assert field.value == "أحمد محمود علي"
    assert field.needs_review and field.method == "label_fallback"


def test_titles_and_watermarks_are_not_mistaken_for_names():
    lines = [("بطاقة تحقيق الشخصية نموذج اصطناعي", 0.9), ("الرقم القومي: 29801011234567", 0.9)]

    assert extract_fields(make_ocr(lines), DOC_NATIONAL_ID).get("full_name") is None


def test_customer_form_and_address_proof_expectations():
    form = extract_fields(
        make_ocr([
            ("استمارة بيانات العميل", 0.9),
            ("الاسم: منى إبراهيم حسن", 0.9),
            ("الرقم القومي: 29801011234567", 0.9),
            ("رقم الهاتف: 01012345678", 0.9),
            ("العنوان: 5 شارع الهرم", 0.9),
        ]),
        DOC_CUSTOMER_FORM,
    )
    assert form.get("mobile").value == "01012345678"
    assert form.missing_fields == []

    proof = extract_fields(
        make_ocr([("فاتورة مرافق", 0.9), ("الاسم: منى إبراهيم حسن", 0.9), ("العنوان: 5 شارع الهرم", 0.9)]),
        DOC_ADDRESS_PROOF,
    )
    assert [f.name for f in proof.fields] == ["full_name", "address"]


def test_invalid_mobile_is_not_accepted_as_valid():
    form = extract_fields(
        make_ocr([("الاسم: منى إبراهيم حسن", 0.9), ("رقم الهاتف: 0301234567", 0.9)]),
        DOC_CUSTOMER_FORM,
    )

    mobile = form.get("mobile")
    assert mobile.needs_review


def test_unknown_document_type_extracts_nothing():
    result = extract_fields(make_ocr(ID_CARD_LINES), "نوع غير معروف")

    assert result.fields == [] and result.missing_fields == []


def test_parse_tsv_keeps_only_real_words_with_normalized_confidence():
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "5\t1\t1\t1\t1\t1\t10\t20\t30\t40\t96.5\tمرحبا",
        "5\t1\t1\t1\t1\t2\t50\t20\t30\t40\t-1\t",
        "5\t1\t1\t1\t1\t3\t90\t20\t30\t40\t80\t  ",
        "4\t1\t1\t1\t1\t0\t0\t0\t0\t0\t-1\t",
    ]

    words = parse_tsv("\n".join([header, *rows]))

    assert len(words) == 1
    assert words[0].text == "مرحبا" and words[0].confidence == pytest.approx(0.965)
    assert (words[0].left, words[0].top, words[0].width, words[0].height) == (10, 20, 30, 40)
