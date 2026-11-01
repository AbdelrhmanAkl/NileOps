"""API tests for complaints management (classification, routing, workflow)."""

from collections.abc import Generator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.core.security import create_access_token, hash_password
from src.db.session import Base, get_db
from src.models.banking import BankingAuditEvent, Complaint, Customer
from src.models.user import User

BASE = "/api/v1/banking/complaints"
CARD_TEXT = "العميل يواجه مشكلة في استخدام البطاقة منذ أمس"
UNCLEAR_TEXT = "اريد ان اتحدث مع احد بخصوص موضوع مهم جدا"


@pytest.fixture
def ctx() -> Generator[SimpleNamespace, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    Base.metadata.create_all(bind=engine)

    with session_factory() as db:
        users = {}
        for role in ("viewer", "reviewer", "admin"):
            user = User(
                username=f"comp_{role}",
                password_hash=hash_password(f"{role.title()}-Password-123!"),
                role=role,
                is_active=True,
            )
            db.add(user)
            db.flush()
            users[role] = user.id
        customer = Customer(customer_ref="SYN-COMP-CUST-1", full_name_ar="عميل الشكاوى")
        db.add(customer)
        db.commit()
        customer_id = customer.id

    def override_get_db() -> Generator:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    def headers(role: str) -> dict[str, str]:
        token = create_access_token(subject=str(users[role]), role=role)
        return {"Authorization": f"Bearer {token}"}

    def create(text=CARD_TEXT, role="reviewer", **extra):
        return ctx_client.post(
            BASE, headers=headers(role), json={"description": text, **extra}
        )

    def audit_actions(complaint_id: int) -> list[str]:
        with session_factory() as db:
            return [
                e.action
                for e in db.scalars(
                    select(BankingAuditEvent)
                    .where(
                        BankingAuditEvent.entity_type == "complaint",
                        BankingAuditEvent.entity_id == complaint_id,
                    )
                    .order_by(BankingAuditEvent.id)
                )
            ]

    try:
        with TestClient(app) as ctx_client:
            yield SimpleNamespace(
                client=ctx_client,
                headers=headers,
                create=create,
                audit_actions=audit_actions,
                customer_id=customer_id,
                session_factory=session_factory,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def set_status(ctx, complaint_id, status, role="reviewer", notes="تحديث الحالة"):
    return ctx.client.post(
        f"{BASE}/{complaint_id}/status",
        headers=ctx.headers(role),
        json={"status": status, "notes": notes},
    )


def assign(ctx, complaint_id, department="خدمة الحسابات", role="reviewer", **extra):
    return ctx.client.post(
        f"{BASE}/{complaint_id}/assign",
        headers=ctx.headers(role),
        json={"department": department, "notes": "فرز بشري", **extra},
    )


# ------------------------------------------------------------------ creation


def test_confident_complaint_is_auto_routed_and_audited(ctx):
    response = ctx.create(customer_id=ctx.customer_id)

    assert response.status_code == 201
    body = response.json()
    complaint = body["complaint"]
    assert body["needs_human_triage"] is False
    assert body["simulation_only"] is True
    assert complaint["category"] == "بطاقات"
    assert complaint["priority"] == "high"
    assert complaint["status"] == "assigned"
    assert complaint["assigned_department"] == "إدارة البطاقات"
    assert complaint["complaint_number"] == f"SIM-COMP-{complaint['id']:06d}"
    assert ctx.audit_actions(complaint["id"]) == ["COMPLAINT_CREATED", "AUTO_CLASSIFIED"]


def test_unclear_complaint_waits_for_human_triage(ctx):
    body = ctx.create(UNCLEAR_TEXT).json()

    assert body["needs_human_triage"] is True
    assert body["complaint"]["status"] == "classified"
    assert body["complaint"]["assigned_department"] is None


def test_audit_trail_does_not_copy_complaint_text(ctx):
    secret_text = "مشكلة في البطاقة رقم 4111111111111111 للعميل"
    complaint_id = ctx.create(secret_text).json()["complaint"]["id"]

    with ctx.session_factory() as db:
        details = " ".join(
            e.details_ar
            for e in db.scalars(
                select(BankingAuditEvent).where(
                    BankingAuditEvent.entity_id == complaint_id,
                    BankingAuditEvent.entity_type == "complaint",
                )
            )
        )
    assert "4111111111111111" not in details


def test_create_with_category_hint_and_unknown_customer(ctx):
    hinted = ctx.create("استفسار عام عن الخدمات المتاحة", category="حسابات")
    assert hinted.status_code == 201
    assert hinted.json()["complaint"]["assigned_department"] == "خدمة الحسابات"

    missing_customer = ctx.create(customer_id=999999)
    assert missing_customer.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"description": "قصير"},
        {"description": "x" * 2001},
        {"description": CARD_TEXT, "category": "غير موجود"},
        {"description": CARD_TEXT, "status": "closed"},
        {"description": CARD_TEXT, "priority": "urgent"},
    ],
)
def test_create_validates_payload(ctx, payload):
    response = ctx.client.post(BASE, headers=ctx.headers("reviewer"), json=payload)

    assert response.status_code == 422


def test_viewer_cannot_create_and_anonymous_is_rejected(ctx):
    assert ctx.create(role="viewer").status_code == 403
    anonymous = ctx.client.post(BASE, json={"description": CARD_TEXT})
    assert anonymous.status_code == 401


# --------------------------------------------------------------------- reads


def test_list_filters_search_and_pagination(ctx):
    ctx.create("مشكلة في البطاقة", customer_id=ctx.customer_id)
    ctx.create("استفسار عن حالة الحساب")
    ctx.create("استفسار عن تحويل لم يصل")
    ctx.create(UNCLEAR_TEXT)
    headers = ctx.headers("viewer")

    def get(**params):
        return ctx.client.get(BASE, headers=headers, params=params).json()

    assert get()["total"] == 4
    assert get(category="بطاقات")["total"] == 1
    assert get(priority="urgent")["total"] == 1
    assert get(status="classified")["total"] == 1
    assert get(department="خدمة الحسابات")["total"] == 1
    assert get(q="SYN-COMP-CUST-1")["total"] == 1
    page = get(page=2, page_size=3)
    assert page["total"] == 4 and len(page["items"]) == 1

    assert ctx.client.get(BASE, headers=headers, params={"status": "bogus"}).status_code == 422
    assert ctx.client.get(BASE, headers=headers, params={"page_size": 101}).status_code == 422


def test_detail_shows_customer_history_and_draft_reply(ctx):
    complaint_id = ctx.create(customer_id=ctx.customer_id).json()["complaint"]["id"]

    response = ctx.client.get(f"{BASE}/{complaint_id}", headers=ctx.headers("viewer"))

    assert response.status_code == 200
    body = response.json()
    assert body["customer_name"] == "عميل الشكاوى"
    assert body["customer_ref"] == "SYN-COMP-CUST-1"
    assert body["reply_is_draft"] is True
    assert body["needs_human_triage"] is False
    assert [h["action"] for h in body["history"]] == ["COMPLAINT_CREATED", "AUTO_CLASSIFIED"]
    assert body["history"][0]["actor_username"] == "comp_reviewer"
    assert body["history"][1]["actor_username"] == "complaint_router"


def test_detail_without_customer_and_unknown_id(ctx):
    complaint_id = ctx.create().json()["complaint"]["id"]

    body = ctx.client.get(f"{BASE}/{complaint_id}", headers=ctx.headers("viewer")).json()
    assert body["customer_name"] is None

    assert ctx.client.get(f"{BASE}/999999", headers=ctx.headers("viewer")).status_code == 404


def test_summary_counts_for_dashboard(ctx):
    ctx.create("مشكلة في البطاقة")
    ctx.create("استفسار عن حالة الحساب")
    ctx.create(UNCLEAR_TEXT)

    response = ctx.client.get(f"{BASE}/summary", headers=ctx.headers("viewer"))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["awaiting_triage"] == 1
    assert body["by_status"] == {"assigned": 2, "classified": 1}
    assert body["by_category"]["بطاقات"] == 1
    assert body["by_department"]["إدارة البطاقات"] == 1


# ----------------------------------------------------------------- workflow


def test_human_triage_assignment_moves_classified_to_assigned(ctx):
    complaint_id = ctx.create(UNCLEAR_TEXT).json()["complaint"]["id"]

    response = assign(ctx, complaint_id, "عمليات التحويلات", priority="high")

    assert response.status_code == 200
    body = response.json()
    assert body["previous_status"] == "classified"
    assert body["new_status"] == "assigned"
    assert body["assigned_department"] == "عمليات التحويلات"
    assert body["priority"] == "high"
    assert ctx.audit_actions(complaint_id)[-1] == "HUMAN_ASSIGNMENT"

    detail = ctx.client.get(f"{BASE}/{complaint_id}", headers=ctx.headers("viewer")).json()
    assert detail["needs_human_triage"] is False
    assert "comp_reviewer" in detail["routing_reason"]


def test_assigned_complaint_can_be_rerouted_by_a_human(ctx):
    complaint_id = ctx.create().json()["complaint"]["id"]

    response = assign(ctx, complaint_id, "خدمة العملاء", role="admin")

    assert response.status_code == 200
    assert response.json()["assigned_department"] == "خدمة العملاء"


def test_assignment_rejects_unknown_department_and_extra_fields(ctx):
    complaint_id = ctx.create(UNCLEAR_TEXT).json()["complaint"]["id"]

    assert assign(ctx, complaint_id, "قسم غير موجود").status_code == 422
    extra = ctx.client.post(
        f"{BASE}/{complaint_id}/assign",
        headers=ctx.headers("reviewer"),
        json={"department": "خدمة الحسابات", "notes": "ملاحظة", "status": "closed"},
    )
    assert extra.status_code == 422


def test_full_lifecycle_with_audit_and_reopen(ctx):
    complaint_id = ctx.create().json()["complaint"]["id"]

    assert set_status(ctx, complaint_id, "in_progress").status_code == 200
    assert set_status(ctx, complaint_id, "resolved").status_code == 200
    assert set_status(ctx, complaint_id, "in_progress").status_code == 200  # reopen
    assert set_status(ctx, complaint_id, "resolved").status_code == 200
    closed = set_status(ctx, complaint_id, "closed")

    assert closed.status_code == 200
    assert closed.json()["previous_status"] == "resolved"
    assert ctx.audit_actions(complaint_id).count("STATUS_CHANGE") == 5


@pytest.mark.parametrize(
    "start,target",
    [
        ("classified", "in_progress"),
        ("assigned", "resolved"),
        ("assigned", "closed"),
        ("in_progress", "closed"),
        ("closed", "in_progress"),
        ("closed", "resolved"),
    ],
)
def test_invalid_status_transitions_return_409(ctx, start, target):
    complaint_id = ctx.create().json()["complaint"]["id"]
    with ctx.session_factory() as db:
        db.get(Complaint, complaint_id).status = start
        db.commit()

    response = set_status(ctx, complaint_id, target)

    assert response.status_code == 409
    with ctx.session_factory() as db:
        assert db.get(Complaint, complaint_id).status == start


@pytest.mark.parametrize("status", ["in_progress", "resolved", "closed"])
def test_cannot_reassign_after_processing_started(ctx, status):
    complaint_id = ctx.create().json()["complaint"]["id"]
    with ctx.session_factory() as db:
        db.get(Complaint, complaint_id).status = status
        db.commit()

    assert assign(ctx, complaint_id).status_code == 409


def test_status_endpoint_rejects_system_only_statuses(ctx):
    complaint_id = ctx.create().json()["complaint"]["id"]

    for status in ("new", "classified", "assigned"):
        assert set_status(ctx, complaint_id, status).status_code == 422


@pytest.mark.parametrize("action", ["assign", "status"])
def test_viewer_cannot_modify_and_anonymous_is_rejected(ctx, action):
    complaint_id = ctx.create().json()["complaint"]["id"]
    body = (
        {"department": "خدمة الحسابات", "notes": "ملاحظة"}
        if action == "assign"
        else {"status": "in_progress", "notes": "ملاحظة"}
    )
    url = f"{BASE}/{complaint_id}/{action}"

    assert ctx.client.post(url, headers=ctx.headers("viewer"), json=body).status_code == 403
    assert ctx.client.post(url, json=body).status_code == 401


@pytest.mark.parametrize("action", ["assign", "status"])
def test_unknown_complaint_returns_404_for_actions(ctx, action):
    body = (
        {"department": "خدمة الحسابات", "notes": "ملاحظة"}
        if action == "assign"
        else {"status": "in_progress", "notes": "ملاحظة"}
    )

    response = ctx.client.post(
        f"{BASE}/999999/{action}", headers=ctx.headers("reviewer"), json=body
    )

    assert response.status_code == 404
