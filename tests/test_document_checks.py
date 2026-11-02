"""Tests for cross-checks between OCR fields and the KYC application."""

from datetime import date
from types import SimpleNamespace

from src.ocr.fields import DOC_NATIONAL_ID, extract_fields
from src.services.document_checks import names_match, run_checks
from tests.ocr_helpers import ID_CARD_LINES, make_ocr


def application(name="أحمد محمود", last_four="4567"):
    return SimpleNamespace(customer_name=name, national_id_last_four=last_four)


def by_code(checks):
    return {c.code: c for c in checks}


def test_names_match_handles_short_application_names_and_hamza():
    assert names_match("أحمد محمود", "احمد محمود علي")
    assert names_match("منى إبراهيم", "منى ابراهيم حسن")
    assert not names_match("أحمد محمود", "خالد سعيد علي")
    assert not names_match("", "أحمد")


def test_all_checks_pass_for_a_consistent_document():
    extraction = extract_fields(make_ocr(ID_CARD_LINES), DOC_NATIONAL_ID)

    checks = by_code(run_checks(application(), extraction, today=date(2026, 11, 1)))

    assert all(c.passed for c in checks.values())
    assert set(checks) == {"NAME_MATCH", "ID_STRUCTURE", "ID_LAST4_MATCH", "ID_NOT_EXPIRED", "OCR_CONFIDENCE"}


def test_mismatching_name_and_id_are_reported():
    extraction = extract_fields(make_ocr(ID_CARD_LINES), DOC_NATIONAL_ID)

    checks = by_code(run_checks(application("سارة محمد", "0000"), extraction, today=date(2026, 11, 1)))

    assert checks["NAME_MATCH"].passed is False
    assert checks["ID_LAST4_MATCH"].passed is False
    assert checks["ID_STRUCTURE"].passed is True


def test_expired_document_is_an_error():
    extraction = extract_fields(make_ocr(ID_CARD_LINES), DOC_NATIONAL_ID)

    checks = by_code(run_checks(application(), extraction, today=date(2033, 1, 1)))

    assert checks["ID_NOT_EXPIRED"].passed is False and checks["ID_NOT_EXPIRED"].severity == "error"


def test_missing_fields_are_not_evaluated_and_lower_confidence_check():
    extraction = extract_fields(make_ocr(ID_CARD_LINES[:1]), DOC_NATIONAL_ID)

    checks = by_code(run_checks(application(), extraction, today=date(2026, 11, 1)))

    assert checks["NAME_MATCH"].passed is None
    assert checks["ID_STRUCTURE"].passed is None
    assert checks["OCR_CONFIDENCE"].passed is False
    assert "ID_NOT_EXPIRED" not in checks
