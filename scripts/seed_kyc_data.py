from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.session import Base, SessionLocal, engine
from src.models.kyc import (
    ApplicationStatus,
    AuditEvent,
    DocumentStatus,
    KYCApplication,
    KYCDocument,
    ValidationResult,
    ValidationSeverity,
)


TARGET_COUNT = 100

CUSTOMER_NAMES = [
    "أحمد محمود", "منى إبراهيم", "محمود حسن", "سارة محمد",
    "عمر مصطفى", "نور أحمد", "مريم خالد", "يوسف علي",
    "فاطمة محمود", "مصطفى عبد الرحمن", "آية محمد", "كريم سمير",
    "ملك إبراهيم", "عبد الله حسن", "دينا أشرف", "محمد عادل",
    "بسمة جمال", "ياسمين طارق", "حسن السيد", "ندى شريف",
]

BRANCHES = [
    "فرع القاهرة", "فرع مدينة نصر", "فرع الجيزة",
    "فرع الإسكندرية", "فرع طنطا", "فرع المنصورة",
    "فرع أسيوط", "فرع الإسماعيلية",
]

DOCUMENT_TYPES = [
    "بطاقة الرقم القومي",
    "إثبات العنوان",
    "استمارة بيانات العميل",
]

SCENARIOS = [
    {
        "status": ApplicationStatus.PENDING.value,
        "documents": [
            DocumentStatus.UPLOADED.value,
            DocumentStatus.UPLOADED.value,
            DocumentStatus.UPLOADED.value,
        ],
        "rule": "DOC-ALL-UPLOADED",
        "title": "تم إرفاق المستندات المطلوبة",
        "details": "المستندات المطلوبة مرفقة مبدئيًا وتحتاج إلى استكمال الفحص.",
        "severity": ValidationSeverity.INFO.value,
        "passed": True,
        "note": "المستندات مرفقة وتنتظر المراجعة.",
    },
    {
        "status": ApplicationStatus.NEEDS_INFORMATION.value,
        "documents": [
            DocumentStatus.UPLOADED.value,
            DocumentStatus.MISSING.value,
            DocumentStatus.UPLOADED.value,
        ],
        "rule": "DOC-MISSING",
        "title": "مستند مطلوب غير مرفق",
        "details": "إثبات العنوان غير مرفق، ويلزم استكمال المستند قبل متابعة المراجعة.",
        "severity": ValidationSeverity.WARNING.value,
        "passed": False,
        "note": "يرجى استكمال مستند إثبات العنوان.",
    },
    {
        "status": ApplicationStatus.IN_REVIEW.value,
        "documents": [
            DocumentStatus.UNDER_REVIEW.value,
            DocumentStatus.UPLOADED.value,
            DocumentStatus.UPLOADED.value,
        ],
        "rule": "DOC-UNDER-REVIEW",
        "title": "مستند قيد الفحص",
        "details": "بطاقة الرقم القومي قيد المراجعة ولم تُعتمد نتيجتها بعد.",
        "severity": ValidationSeverity.INFO.value,
        "passed": True,
        "note": "المراجعة البشرية لم تكتمل بعد.",
    },
    {
        "status": ApplicationStatus.NEEDS_INFORMATION.value,
        "documents": [
            DocumentStatus.INVALID.value,
            DocumentStatus.UPLOADED.value,
            DocumentStatus.UPLOADED.value,
        ],
        "rule": "DOC-NEEDS-REVIEW",
        "title": "مستند يحتاج إلى مراجعة",
        "details": "بيانات المستند التجريبي تشير إلى ضرورة المراجعة البشرية.",
        "severity": ValidationSeverity.WARNING.value,
        "passed": False,
        "note": "يلزم فحص المستند يدويًا قبل اتخاذ أي قرار.",
    },
    {
        "status": ApplicationStatus.PENDING.value,
        "documents": [
            DocumentStatus.UPLOADED.value,
            DocumentStatus.UPLOADED.value,
            DocumentStatus.MISSING.value,
        ],
        "rule": "FORM-MISSING",
        "title": "استمارة بيانات العميل غير مرفقة",
        "details": "استمارة بيانات العميل غير مرفقة في هذا السيناريو التجريبي.",
        "severity": ValidationSeverity.WARNING.value,
        "passed": False,
        "note": "استكمال استمارة بيانات العميل مطلوب.",
    },
]


def seed_data() -> None:
    Base.metadata.create_all(bind=engine)

    created_applications = 0
    created_documents = 0
    created_results = 0
    created_events = 0

    with SessionLocal() as session:
        for index in range(1, TARGET_COUNT + 1):
            application_number = f"KYC-2026-{index:05d}"

            existing = session.scalar(
                select(KYCApplication).where(
                    KYCApplication.application_number == application_number
                )
            )
            if existing is not None:
                continue

            scenario = SCENARIOS[(index - 1) % len(SCENARIOS)]
            customer_name = CUSTOMER_NAMES[(index - 1) % len(CUSTOMER_NAMES)]

            application = KYCApplication(
                application_number=application_number,
                customer_name=customer_name,
                national_id_last_four=f"{(index * 37) % 10000:04d}",
                application_type="فتح حساب جديد",
                status=scenario["status"],
                branch_name=BRANCHES[(index - 1) % len(BRANCHES)],
                notes="بيانات اصطناعية لأغراض الاختبار فقط، وليست بيانات عميل حقيقي.",
            )
            session.add(application)
            session.flush()
            created_applications += 1

            for document_type, status in zip(
                DOCUMENT_TYPES, scenario["documents"], strict=True
            ):
                document = KYCDocument(
                    application_id=application.id,
                    document_type=document_type,
                    status=status,
                    file_name=None,
                    reviewer_notes="سجل تجريبي؛ لا يحتوي على مستند حقيقي.",
                )
                session.add(document)
                created_documents += 1

            result = ValidationResult(
                application_id=application.id,
                rule_code=scenario["rule"],
                title_ar=scenario["title"],
                details_ar=scenario["details"],
                severity=scenario["severity"],
                passed=scenario["passed"],
                evidence={
                    "مصدر البيانات": "اصطناعي",
                    "رقم السيناريو": (index - 1) % len(SCENARIOS) + 1,
                },
            )
            session.add(result)
            created_results += 1

            event = AuditEvent(
                application_id=application.id,
                action="إنشاء طلب تجريبي",
                actor_type="system",
                actor_name="مولّد البيانات التجريبية",
                details_ar="تم إنشاء الطلب ببيانات اصطناعية لاختبار النظام.",
                new_status=application.status,
            )
            session.add(event)
            created_events += 1

        session.commit()

        applications = session.scalars(
            select(KYCApplication).where(
                KYCApplication.application_number.like("KYC-2026-%")
            )
        ).all()

        status_counts = Counter(application.status for application in applications)

        document_count = session.scalar(
            select(__import__("sqlalchemy").func.count()).select_from(KYCDocument)
        )
        result_count = session.scalar(
            select(__import__("sqlalchemy").func.count()).select_from(ValidationResult)
        )
        event_count = session.scalar(
            select(__import__("sqlalchemy").func.count()).select_from(AuditEvent)
        )

        print("=== ملخص توليد البيانات التجريبية ===")
        print(f"طلبات أُنشئت في هذه المرة: {created_applications}")
        print(f"مستندات أُنشئت في هذه المرة: {created_documents}")
        print(f"نتائج تحقق أُنشئت في هذه المرة: {created_results}")
        print(f"أحداث تدقيق أُنشئت في هذه المرة: {created_events}")
        print(f"إجمالي الطلبات التجريبية: {len(applications)}")
        print(f"إجمالي سجلات المستندات: {document_count}")
        print(f"إجمالي نتائج التحقق: {result_count}")
        print(f"إجمالي أحداث التدقيق: {event_count}")
        print("\nتوزيع حالات الطلبات:")
        for status, count in sorted(status_counts.items()):
            print(f"- {status}: {count}")

        assert len(applications) == TARGET_COUNT, (
            f"Expected {TARGET_COUNT} applications, found {len(applications)}"
        )
        assert document_count >= TARGET_COUNT * len(DOCUMENT_TYPES)
        assert result_count >= TARGET_COUNT
        assert event_count >= TARGET_COUNT

        print("\nSUCCESS: تم التحقق من بيانات KYC التجريبية.")


if __name__ == "__main__":
    seed_data()
