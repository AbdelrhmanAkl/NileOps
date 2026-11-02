
"""Generate deterministic synthetic banking data for NileOps."""

import random

from sqlalchemy import func, select

from src.db.session import SessionLocal
from src.models.banking import (
    AccountOpeningRequest,
    BankAccount,
    BankingAuditEvent,
    CardIssuanceRequest,
    Complaint,
    Customer,
)


RNG = random.Random(42)

TARGETS = {
    "customers": 30,
    "account_requests": 20,
    "accounts": 8,
    "card_requests": 15,
    "complaints": 20,
}

CUSTOMER_NAMES = [
    "أحمد محمد علي",
    "محمد أحمد حسن",
    "محمود إبراهيم السيد",
    "مصطفى خالد محمود",
    "عمر أحمد عبد الله",
    "يوسف محمد إبراهيم",
    "عبد الرحمن حسن علي",
    "سارة محمود أحمد",
    "مريم أحمد محمد",
    "نور خالد إبراهيم",
    "منى محمد السيد",
    "آية مصطفى حسن",
    "دينا أحمد علي",
    "ملك إبراهيم محمود",
    "هبة خالد عبد الرحمن",
]

BRANCHES = [
    "فرع القاهرة",
    "فرع مدينة نصر",
    "فرع المعادي",
    "فرع الجيزة",
    "فرع الإسكندرية",
    "فرع طنطا",
]

ACCOUNT_TYPES = ["savings", "current"]

ACCOUNT_STATUSES = [
    "pending",
    "under_review",
    "needs_information",
    "approved",
    "rejected",
]

CARD_TYPES = ["debit", "prepaid"]

CARD_STATUSES = [
    "pending",
    "under_review",
    "approved",
    "rejected",
    "issued",
    "cancelled",
]

COMPLAINT_SCENARIOS = [
    {
        "category": "بطاقات",
        "description": "العميل يواجه مشكلة في استخدام البطاقة.",
        "priority": "high",
        "department": "إدارة البطاقات",
        "reason": "تم توجيه الشكوى بناءً على تصنيف مشكلة البطاقة.",
        "reply": "تم استلام شكواك وسيقوم فريق البطاقات بمراجعتها.",
    },
    {
        "category": "حسابات",
        "description": "العميل يستفسر عن حالة حسابه.",
        "priority": "medium",
        "department": "خدمة الحسابات",
        "reason": "تم توجيه الشكوى إلى فريق خدمة الحسابات.",
        "reply": "تم تسجيل طلبك وسيتم مراجعته من فريق الحسابات.",
    },
    {
        "category": "تحويلات",
        "description": "العميل يستفسر عن تحويل مالي لم يظهر في الحساب.",
        "priority": "urgent",
        "department": "عمليات التحويلات",
        "reason": "تم تصنيف الشكوى كحالة عاجلة متعلقة بالتحويلات.",
        "reply": "تم تصعيد الشكوى إلى فريق التحويلات لمراجعتها.",
    },
    {
        "category": "خدمة العملاء",
        "description": "العميل يرغب في تقديم ملاحظة حول الخدمة.",
        "priority": "low",
        "department": "خدمة العملاء",
        "reason": "تم توجيه الملاحظة إلى فريق خدمة العملاء.",
        "reply": "شكرًا لملاحظتك، تم تسجيلها لمراجعتها.",
    },
]

AUDIT_ACTOR = "synthetic_data_generator"


def add_audit_event(
    session,
    entity_type: str,
    entity_id: int,
    action: str,
    details_ar: str,
) -> None:
    session.add(
        BankingAuditEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor_role="system",
            actor_username=AUDIT_ACTOR,
            details_ar=details_ar,
        )
    )


def get_count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def seed_customers(session) -> dict[int, Customer]:
    customers_by_index = {}

    for index in range(1, TARGETS["customers"] + 1):
        customer_ref = f"SYN-CUST-{index:04d}"

        customer = session.scalar(
            select(Customer).where(Customer.customer_ref == customer_ref)
        )

        if customer is None:
            customer = Customer(
                customer_ref=customer_ref,
                full_name_ar=CUSTOMER_NAMES[(index - 1) % len(CUSTOMER_NAMES)],
                email=f"customer{index:04d}@example.invalid",
                phone_last_four=f"{(index * 137) % 10000:04d}",
                branch_name=BRANCHES[(index - 1) % len(BRANCHES)],
            )
            session.add(customer)
            session.flush()

            add_audit_event(
                session,
                "customer",
                customer.id,
                "synthetic_customer_created",
                "تم إنشاء سجل عميل اصطناعي لأغراض العرض والاختبار.",
            )

        customers_by_index[index] = customer

    return customers_by_index


def seed_account_requests(session, customers_by_index) -> None:
    for index in range(1, TARGETS["account_requests"] + 1):
        request_number = f"SYN-ACC-REQ-{index:04d}"

        existing = session.scalar(
            select(AccountOpeningRequest).where(
                AccountOpeningRequest.request_number == request_number
            )
        )

        if existing is not None:
            continue

        customer = customers_by_index[index]
        status = ACCOUNT_STATUSES[(index - 1) % len(ACCOUNT_STATUSES)]

        request = AccountOpeningRequest(
            request_number=request_number,
            customer_id=customer.id,
            account_type=ACCOUNT_TYPES[(index - 1) % len(ACCOUNT_TYPES)],
            status=status,
            branch_name=customer.branch_name,
            review_notes="طلب اصطناعي لأغراض المحاكاة فقط.",
            simulation_only=True,
        )

        session.add(request)
        session.flush()

        add_audit_event(
            session,
            "account_opening_request",
            request.id,
            "synthetic_request_created",
            "تم إنشاء طلب فتح حساب تجريبي.",
        )


def seed_accounts(session, customers_by_index) -> dict[int, BankAccount]:
    accounts_by_customer = {}

    for index in range(1, TARGETS["accounts"] + 1):
        account_ref = f"SYN-ACCOUNT-{index:04d}"

        account = session.scalar(
            select(BankAccount).where(BankAccount.account_ref == account_ref)
        )

        customer = customers_by_index[index]

        if account is None:
            account = BankAccount(
                account_ref=account_ref,
                customer_id=customer.id,
                account_type=ACCOUNT_TYPES[(index - 1) % len(ACCOUNT_TYPES)],
                status="active",
                currency="EGP",
                simulation_only=True,
            )

            session.add(account)
            session.flush()

            add_audit_event(
                session,
                "bank_account",
                account.id,
                "synthetic_account_created",
                "تم إنشاء حساب محاكى، وليس حسابًا مصرفيًا حقيقيًا.",
            )

        accounts_by_customer[customer.id] = account

    return accounts_by_customer


def seed_card_requests(
    session,
    customers_by_index,
    accounts_by_customer,
) -> None:
    for index in range(1, TARGETS["card_requests"] + 1):
        request_number = f"SYN-CARD-REQ-{index:04d}"

        existing = session.scalar(
            select(CardIssuanceRequest).where(
                CardIssuanceRequest.request_number == request_number
            )
        )

        if existing is not None:
            continue

        customer = customers_by_index[index]
        account = accounts_by_customer.get(customer.id)

        request = CardIssuanceRequest(
            request_number=request_number,
            customer_id=customer.id,
            account_id=account.id if account else None,
            card_type=CARD_TYPES[(index - 1) % len(CARD_TYPES)],
            status=CARD_STATUSES[(index - 1) % len(CARD_STATUSES)],
            review_notes="طلب إصدار بطاقة اصطناعي لأغراض المحاكاة.",
            simulation_only=True,
        )

        session.add(request)
        session.flush()

        add_audit_event(
            session,
            "card_issuance_request",
            request.id,
            "synthetic_request_created",
            "تم إنشاء طلب إصدار بطاقة تجريبي دون إصدار بطاقة فعلية.",
        )


def seed_complaints(session, customers_by_index) -> None:
    for index in range(1, TARGETS["complaints"] + 1):
        complaint_number = f"SYN-COMP-{index:04d}"

        existing = session.scalar(
            select(Complaint).where(
                Complaint.complaint_number == complaint_number
            )
        )

        if existing is not None:
            continue

        customer = customers_by_index[((index - 1) % TARGETS["customers"]) + 1]
        scenario = COMPLAINT_SCENARIOS[(index - 1) % len(COMPLAINT_SCENARIOS)]

        complaint = Complaint(
            complaint_number=complaint_number,
            customer_id=customer.id,
            category=scenario["category"],
            description=scenario["description"],
            priority=scenario["priority"],
            status="assigned",
            assigned_department=scenario["department"],
            routing_reason=scenario["reason"],
            suggested_reply=scenario["reply"],
        )

        session.add(complaint)
        session.flush()

        add_audit_event(
            session,
            "complaint",
            complaint.id,
            "synthetic_complaint_created",
            "تم إنشاء شكوى اصطناعية وتوجيهها باستخدام قواعد تصنيف ثابتة.",
        )


def seed_data() -> None:
    with SessionLocal() as session:
        try:
            customers = seed_customers(session)

            seed_account_requests(session, customers)

            accounts = seed_accounts(session, customers)

            seed_card_requests(session, customers, accounts)

            seed_complaints(session, customers)

            session.commit()

        except Exception:
            session.rollback()
            raise

        models = {
            "customers": Customer,
            "account_opening_requests": AccountOpeningRequest,
            "bank_accounts": BankAccount,
            "card_issuance_requests": CardIssuanceRequest,
            "complaints": Complaint,
            "banking_audit_events": BankingAuditEvent,
        }

        print("=== NileOps Synthetic Banking Data ===")

        for table_name, model in models.items():
            print(f"{table_name}: {get_count(session, model)}")

        print("\nTarget counts:")
        for name, target in TARGETS.items():
            print(f"{name}: {target}")

        print("\nSUCCESS: Synthetic banking data generated.")
        print("All generated operations are simulation-only.")


if __name__ == "__main__":
    seed_data()
