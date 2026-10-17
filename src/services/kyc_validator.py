from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select

from src.db.session import SessionLocal
from src.models.kyc import (
    DocumentStatus,
    KYCApplication,
    ValidationResult,
    ValidationSeverity,
)


REQUIRED_DOCUMENTS = (
    "بطاقة الرقم القومي",
    "إثبات العنوان",
    "استمارة بيانات العميل",
)



@dataclass(frozen=True)
class ValidationFinding:
    rule_code: str
    title_ar: str
    details_ar: str
    severity: str
    passed: bool
    document_type: str
    outcome: str




def validate_application(
    application: KYCApplication,
) -> list[ValidationFinding]:
    """Evaluate required KYC document presence and review status."""
    documents_by_type = {
        document.document_type: document
        for document in application.documents
    }
    findings: list[ValidationFinding] = []

    for document_type in REQUIRED_DOCUMENTS:
        document = documents_by_type.get(document_type)

        if document is None or document.status == DocumentStatus.MISSING.value:
            findings.append(
                ValidationFinding(
                    rule_code="KYC_DOCUMENT_MISSING",
                    title_ar="مستند مطلوب غير مرفق",
                    details_ar=f"المستند المطلوب «{document_type}» غير مرفق.",
                    severity=ValidationSeverity.WARNING.value,
                    passed=False,
                    document_type=document_type,
                    outcome="MISSING",
                )
            )
        elif document.status in (
            DocumentStatus.INVALID.value,
            DocumentStatus.EXPIRED.value,
        ):
            findings.append(
                ValidationFinding(
                    rule_code="KYC_DOCUMENT_REVIEW_REQUIRED",
                    title_ar="المستند يحتاج إلى مراجعة",
                    details_ar=(
                        f"حالة «{document_type}» هي «{document.status}». "
                        "يلزم فحص بشري قبل استكمال الطلب."
                    ),
                    severity=ValidationSeverity.WARNING.value,
                    passed=False,
                    document_type=document_type,
                    outcome="REQUIRES_HUMAN_REVIEW",
                )
            )
        elif document.status == DocumentStatus.UNDER_REVIEW.value:
            findings.append(
                ValidationFinding(
                    rule_code="KYC_DOCUMENT_PENDING_REVIEW",
                    title_ar="المستند قيد الفحص",
                    details_ar=(
                        f"لم يكتمل فحص «{document_type}» بعد. "
                        "لا يمكن اعتبار التحقق ناجحًا أو فاشلًا قبل اكتمال الفحص."
                    ),
                    severity=ValidationSeverity.INFO.value,
                    passed=False,
                    document_type=document_type,
                    outcome="PENDING_REVIEW",
                )
            )
        elif document.status == DocumentStatus.UPLOADED.value:
            findings.append(
                ValidationFinding(
                    rule_code="KYC_DOCUMENT_PRESENT",
                    title_ar="تم استلام المستند",
                    details_ar=(
                        f"تم تسجيل استلام «{document_type}»، "
                        "لكن لم يتم التحقق من صحة محتواه."
                    ),
                    severity=ValidationSeverity.INFO.value,
                    passed=False,
                    document_type=document_type,
                    outcome="PRESENT_UNVERIFIED",
                )
            )
        elif document.status == DocumentStatus.VALID.value:
            findings.append(
                ValidationFinding(
                    rule_code="KYC_DOCUMENT_PRESENT",
                    title_ar="المستند مستوفٍ مبدئيًا",
                    details_ar=(
                        f"حالة «{document_type}» مسجلة كمستوفٍ مبدئيًا. "
                        "هذه الحالة لا تثبت وحدها اكتمال جميع ضوابط التحقق."
                    ),
                    severity=ValidationSeverity.INFO.value,
                    passed=True,
                    document_type=document_type,
                    outcome="PRELIMINARILY_VALID",
                )
            )
        else:
            findings.append(
                ValidationFinding(
                    rule_code="KYC_DOCUMENT_UNKNOWN_STATUS",
                    title_ar="حالة مستند غير معروفة",
                    details_ar=(
                        f"حالة «{document_type}» غير معروفة للنظام: "
                        f"«{document.status}»."
                    ),
                    severity=ValidationSeverity.WARNING.value,
                    passed=False,
                    document_type=document_type,
                    outcome="UNKNOWN_STATUS",
                )
            )

    return findings




def save_findings(
    application: KYCApplication,
    findings: list[ValidationFinding],
) -> tuple[int, int]:
    """Insert or update findings using their logical identity."""
    inserted = 0
    updated = 0

    for finding in findings:
        existing = next(
            (
                result
                for result in application.validation_results
                if result.rule_code == finding.rule_code
                and (result.evidence or {}).get("document_type")
                == finding.document_type
            ),
            None,
        )

        evidence = {
            "مصدر البيانات": "اصطناعي",
            "document_type": finding.document_type,
            "rule_version": "1.1",
            "outcome": finding.outcome,
        }

        if existing is None:
            existing = ValidationResult(
                application_id=application.id,
                rule_code=finding.rule_code,
                title_ar=finding.title_ar,
                details_ar=finding.details_ar,
                severity=finding.severity,
                passed=finding.passed,
                evidence=evidence,
            )
            application.validation_results.append(existing)
            inserted += 1
        else:
            existing.title_ar = finding.title_ar
            existing.details_ar = finding.details_ar
            existing.severity = finding.severity
            existing.passed = finding.passed
            existing.evidence = evidence
            updated += 1

    return inserted, updated



def main() -> None:
    with SessionLocal() as session:
        applications = session.scalars(
            select(KYCApplication)
            .where(KYCApplication.application_number.like("KYC-2026-%"))
            .order_by(KYCApplication.application_number)
        ).all()

        severity_counts: Counter[str] = Counter()
        rule_counts: Counter[str] = Counter()
        total_findings = 0
        inserted_total = 0
        updated_total = 0

        print("=== محرك التحقق العربي ===")

        for application in applications:
            # Remove obsolete seed findings that lack document-level evidence.
            for old_result in list(application.validation_results):
                evidence = old_result.evidence or {}
                if "document_type" not in evidence:
                    session.delete(old_result)

            findings = validate_application(application)
            inserted, updated = save_findings(application, findings)

            inserted_total += inserted
            updated_total += updated
            total_findings += len(findings)

            for finding in findings:
                severity_counts[finding.severity] += 1
                rule_counts[finding.rule_code] += 1

            if application.application_number in (
                "KYC-2026-00001",
                "KYC-2026-00002",
                "KYC-2026-00004",
            ):
                print(
                    f"\n{application.application_number} - "
                    f"{application.customer_name}"
                )
                for finding in findings:
                    print(
                        f"- [{finding.severity}] {finding.title_ar}: "
                        f"{finding.details_ar}"
                    )

        session.commit()

        print("\n=== ملخص الحفظ ===")
        print(f"الطلبات المفحوصة: {len(applications)}")
        print(f"نتائج القواعد الحالية: {total_findings}")
        print(f"نتائج جديدة أُضيفت: {inserted_total}")
        print(f"نتائج سابقة حُدّثت: {updated_total}")

        print("\nتوزيع نتائج الفحص:")
        for rule_code, count in sorted(rule_counts.items()):
            print(f"- {rule_code}: {count}")

        print("\nتوزيع الخطورة:")
        for severity, count in sorted(severity_counts.items()):
            print(f"- {severity}: {count}")

        assert len(applications) == 100, (
            f"Expected 100 applications, found {len(applications)}"
        )
        assert total_findings == 300, (
            f"Expected 300 findings, found {total_findings}"
        )

        print("\nSUCCESS: تم حفظ نتائج التحقق في قاعدة البيانات.")


if __name__ == "__main__":
    main()
