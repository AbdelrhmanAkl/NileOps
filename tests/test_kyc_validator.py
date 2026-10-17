
from types import SimpleNamespace

import pytest

from src.models.kyc import DocumentStatus, ValidationSeverity
from src.services.kyc_validator import (
    REQUIRED_DOCUMENTS,
    validate_application,
)


def make_application(statuses: dict[str, str]) -> SimpleNamespace:
    """Build an in-memory application without database access."""
    documents = [
        SimpleNamespace(
            document_type=document_type,
            status=statuses.get(
                document_type,
                DocumentStatus.MISSING.value,
            ),
        )
        for document_type in REQUIRED_DOCUMENTS
    ]

    return SimpleNamespace(documents=documents)


@pytest.mark.parametrize(
    ("document_status", "expected_outcome", "expected_passed"),
    [
        (
            DocumentStatus.MISSING.value,
            "MISSING",
            False,
        ),
        (
            DocumentStatus.UPLOADED.value,
            "PRESENT_UNVERIFIED",
            False,
        ),
        (
            DocumentStatus.UNDER_REVIEW.value,
            "PENDING_REVIEW",
            False,
        ),
        (
            DocumentStatus.INVALID.value,
            "REQUIRES_HUMAN_REVIEW",
            False,
        ),
        (
            DocumentStatus.EXPIRED.value,
            "REQUIRES_HUMAN_REVIEW",
            False,
        ),
        (
            DocumentStatus.VALID.value,
            "PRELIMINARILY_VALID",
            True,
        ),
    ],
)
def test_document_status_mapping(
    document_status: str,
    expected_outcome: str,
    expected_passed: bool,
) -> None:
    document_type = REQUIRED_DOCUMENTS[0]
    application = make_application(
        {document_type: document_status}
    )

    findings = validate_application(application)

    assert len(findings) == len(REQUIRED_DOCUMENTS)

    finding = next(
        item
        for item in findings
        if item.document_type == document_type
    )

    assert finding.outcome == expected_outcome
    assert finding.passed is expected_passed


def test_missing_document_is_reported() -> None:
    application = make_application({})

    findings = validate_application(application)

    assert len(findings) == len(REQUIRED_DOCUMENTS)
    assert all(
        finding.outcome == "MISSING"
        for finding in findings
    )
    assert all(
        finding.severity == ValidationSeverity.WARNING.value
        for finding in findings
    )


def test_uploaded_document_is_not_treated_as_verified() -> None:
    document_type = REQUIRED_DOCUMENTS[0]
    application = make_application(
        {document_type: DocumentStatus.UPLOADED.value}
    )

    findings = validate_application(application)
    finding = next(
        item
        for item in findings
        if item.document_type == document_type
    )

    assert finding.outcome == "PRESENT_UNVERIFIED"
    assert finding.passed is False
    assert "لم يتم التحقق" in finding.details_ar


def test_unknown_document_status_is_flagged() -> None:
    document_type = REQUIRED_DOCUMENTS[0]
    application = make_application(
        {document_type: "حالة غير معروفة"}
    )

    findings = validate_application(application)
    finding = next(
        item
        for item in findings
        if item.document_type == document_type
    )

    assert finding.outcome == "UNKNOWN_STATUS"
    assert finding.passed is False
