"""Tests for the dashboard summary API and the static Arabic UI."""

import re
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.core.security import create_access_token, hash_password
from src.db.session import Base, get_db
from src.models.banking import (
    AccountOpeningRequest,
    BankingAuditEvent,
    CardIssuanceRequest,
    Complaint,
    Customer,
)
from src.models.documents import DocumentFile, ExtractedField
from src.models.kyc import (
    ApplicationStatus,
    AuditEvent,
    DocumentStatus,
    KYCApplication,
    KYCDocument,
)
from src.models.policies import PolicyDocument
from src.models.user import User

SUMMARY = "/api/v1/dashboard/summary"
WEB = Path(__file__).resolve().parents[1] / "src" / "web"
SECRET_NAME = "ميرفت سيد الشاذلي"
SECRET_TEXT = "رقمي القومي 29901011234567 وكلمة السر 998877"
SECRET_NOTE = "ملاحظة سرية للمراجع 556677"


@pytest.fixture
def ctx() -> Generator[SimpleNamespace, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with factory() as db:
        users = {}
        for role in ("viewer", "reviewer", "admin"):
            user = User(
                username=f"dash_{role}",
                password_hash=hash_password(f"{role.title()}-Password-123!"),
                role=role,
                is_active=True,
            )
            db.add(user)
            db.flush()
            users[role] = user.id
        db.commit()

    def override_get_db() -> Generator:
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    def headers(role: str = "viewer") -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(subject=str(users[role]), role=role)}"}

    try:
        with TestClient(app) as client:
            yield SimpleNamespace(
                client=client,
                factory=factory,
                headers=headers,
                summary=lambda role="viewer": client.get(SUMMARY, headers=headers(role)),
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def seed(db):
    customer = Customer(customer_ref="SYN-DASH-1", full_name_ar=SECRET_NAME)
    db.add(customer)
    db.flush()
    complaints = [
        ("C-1", "urgent", "assigned"),
        ("C-2", "low", "assigned"),
        ("C-3", "high", "in_progress"),
        ("C-4", "urgent", "closed"),  # closed: not an open/urgent item
        ("C-5", "medium", "classified"),  # waits for triage
    ]
    for number, priority, status in complaints:
        db.add(
            Complaint(
                complaint_number=number,
                customer_id=customer.id,
                category="بطاقات",
                description=SECRET_TEXT,
                priority=priority,
                status=status,
                assigned_department="إدارة البطاقات",
            )
        )
    for number, status in (("CARD-1", "pending"), ("CARD-2", "under_review"), ("CARD-3", "issued")):
        db.add(
            CardIssuanceRequest(
                request_number=number,
                customer_id=customer.id,
                card_type="debit",
                status=status,
                review_notes=SECRET_NOTE,
            )
        )
    for number, status in (("ACC-1", "pending"), ("ACC-2", "approved"), ("ACC-3", "needs_information")):
        db.add(
            AccountOpeningRequest(
                request_number=number,
                customer_id=customer.id,
                account_type="savings",
                status=status,
                review_notes=SECRET_NOTE,
            )
        )
    app_open = KYCApplication(
        application_number="KYC-D-1", customer_name=SECRET_NAME, status=ApplicationStatus.IN_REVIEW.value, notes=SECRET_NOTE
    )
    app_done = KYCApplication(
        application_number="KYC-D-2", customer_name=SECRET_NAME, status=ApplicationStatus.APPROVED.value
    )
    db.add_all([app_open, app_done])
    db.flush()
    document = KYCDocument(
        application_id=app_open.id,
        document_type="بطاقة الرقم القومي",
        status=DocumentStatus.UPLOADED.value,
        reviewer_notes=SECRET_NOTE,
    )
    db.add(document)
    db.flush()
    file = DocumentFile(
        document_id=document.id,
        stored_name="x" * 32,
        original_name="n.png",
        content_type="image/png",
        size_bytes=10,
        sha256="a" * 64,
        uploaded_by="dash_reviewer",
    )
    db.add(file)
    db.flush()
    for name, review in (("full_name", False), ("national_id", True), ("address", True)):
        db.add(
            ExtractedField(
                document_file_id=file.id, field_name=name, value=SECRET_NAME, confidence=0.5,
                needs_review=review, method="ocr",
            )
        )
    db.add(PolicyDocument(policy_code="POL-TST-001", version=1, title="t", is_active=True, source_sha256="b" * 64))
    db.add(PolicyDocument(policy_code="POL-TST-001", version=0, title="old", is_active=False, source_sha256="c" * 64))
    db.add(
        BankingAuditEvent(
            entity_type="complaint", entity_id=1, action="HUMAN_REVIEW", actor_role="reviewer",
            actor_username="secret_employee_login", details_ar=SECRET_NOTE,
        )
    )
    db.add(
        AuditEvent(
            application_id=app_open.id, action="HUMAN_REVIEW", actor_type="human",
            actor_name="secret_employee_name", details_ar=SECRET_NOTE,
        )
    )
    db.commit()


@pytest.fixture
def seeded(ctx):
    with ctx.factory() as db:
        seed(db)
    return ctx


# --------------------------------------------------------------------- API
def test_summary_requires_authentication(ctx):
    assert ctx.client.get(SUMMARY).status_code == 401
    assert ctx.client.get(SUMMARY, headers={"Authorization": "Bearer nonsense"}).status_code == 401


@pytest.mark.parametrize("role", ["viewer", "reviewer", "admin"])
def test_every_role_can_read_the_summary(ctx, role):
    assert ctx.summary(role).status_code == 200


def test_empty_database_gives_zeros_not_errors(ctx):
    body = ctx.summary().json()
    assert body["kpis"] == {
        "open_reviews": 0, "urgent_open_complaints": 0, "ocr_fields_total": 0,
        "ocr_fields_need_review": 0, "active_policies": 0,
    }
    assert body["applications"] == {"total": 0, "by_status": {}}
    assert body["queues"] == {"applications": [], "complaints": [], "cards": [], "accounts": []}
    assert body["recent_activity"] == []


def test_counts_match_the_database(seeded):
    body = seeded.summary().json()
    assert body["complaints"]["total"] == 5
    assert body["complaints"]["by_status"] == {"assigned": 2, "in_progress": 1, "closed": 1, "classified": 1}
    assert body["complaints"]["by_priority"] == {"urgent": 2, "low": 1, "high": 1, "medium": 1}
    assert body["card_requests"] == {"total": 3, "by_status": {"pending": 1, "under_review": 1, "issued": 1}}
    assert body["account_requests"]["total"] == 3
    assert body["applications"]["total"] == 2
    assert body["documents"]["by_status"] == {DocumentStatus.UPLOADED.value: 1}


def test_kpis_count_only_open_items(seeded):
    kpis = seeded.summary().json()["kpis"]
    # 1 open KYC + 2 open account requests + 2 open card requests + 1 complaint waiting for triage
    assert kpis["open_reviews"] == 1 + 2 + 2 + 1
    assert kpis["urgent_open_complaints"] == 1  # the closed urgent complaint is excluded
    assert kpis["ocr_fields_total"] == 3 and kpis["ocr_fields_need_review"] == 2
    assert kpis["active_policies"] == 1  # superseded versions are not counted


def test_queues_hold_only_open_items_and_urgent_complaints_come_first(seeded):
    queues = seeded.summary().json()["queues"]
    assert [c["number"] for c in queues["complaints"]] == ["C-1", "C-3", "C-5", "C-2"]
    assert queues["complaints"][0]["priority"] == "urgent"
    assert {r["number"] for r in queues["cards"]} == {"CARD-1", "CARD-2"}
    assert {r["number"] for r in queues["accounts"]} == {"ACC-1", "ACC-3"}
    assert [a["number"] for a in queues["applications"]] == ["KYC-D-1"]


def test_queues_are_capped(ctx):
    with ctx.factory() as db:
        customer = Customer(customer_ref="SYN-CAP", full_name_ar="x")
        db.add(customer)
        db.flush()
        for i in range(25):
            db.add(CardIssuanceRequest(request_number=f"CAP-{i}", customer_id=customer.id, card_type="debit", status="pending"))
        db.commit()
    body = ctx.summary().json()
    assert len(body["queues"]["cards"]) == 10
    assert body["card_requests"]["total"] == 25  # counts are complete, only the list is capped


def test_summary_never_contains_personal_data_or_free_text(seeded):
    text = seeded.summary().text
    for secret in (SECRET_NAME, "29901011234567", "998877", "556677", "secret_employee", "dash_reviewer"):
        assert secret not in text, secret
    assert "details" not in text and "description" not in text and "notes" not in text


def test_recent_activity_is_newest_first_without_details(seeded):
    rows = seeded.summary().json()["recent_activity"]
    assert len(rows) == 2
    assert [r["at"] for r in rows] == sorted((r["at"] for r in rows), reverse=True)
    assert set(rows[0]) == {"at", "source", "entity", "action", "actor_role"}
    assert {r["source"] for r in rows} == {"kyc", "banking"}


# ---------------------------------------------------------------- static UI
@pytest.mark.parametrize(
    ("path", "media"),
    [("/dashboard", "text/html"), ("/dashboard/style.css", "text/css")]
    + [(f"/dashboard/{n}.js", "text/javascript") for n in ("app", "util", "v_overview", "v_lists", "v_kyc", "v_policy", "v_admin", "v_explain")],
)
def test_static_files_are_served_with_strict_headers(ctx, path, media):
    response = ctx.client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media)
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_the_page_itself_holds_no_data(ctx):
    html = ctx.client.get("/dashboard").text
    assert 'lang="ar"' in html and 'dir="rtl"' in html
    assert "KYC-" not in html and "SYN-" not in html


def test_only_listed_static_files_exist(ctx):
    for path in ("/dashboard/../main.py", "/dashboard/x.js", "/dashboard/app.js/../config.py", "/dashboard/%2e%2e/main.py"):
        assert ctx.client.get(path).status_code in (404, 405)


SCRIPTS = sorted(p.name for p in WEB.glob("*.js"))


def test_every_script_file_is_served():
    assert len(SCRIPTS) >= 8
    client = TestClient(app)
    for name in SCRIPTS:
        assert client.get(f"/dashboard/{name}").status_code == 200, name


def test_the_page_never_loads_anything_from_other_origins():
    for name in ("dashboard.html", "style.css", *SCRIPTS):
        text = (WEB / name).read_text(encoding="utf-8")
        assert not re.search(r"""(?:src|href|url\(|import|fetch\()\s*=?\s*["']?\s*(?:https?:)?//""", text), name


def test_html_has_no_inline_scripts_styles_or_handlers():
    html = (WEB / "dashboard.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert " style=" not in html and not re.search(r"\son[a-z]+\s*=", html)


def test_script_never_builds_html_from_data():
    for name in SCRIPTS:
        script = (WEB / name).read_text(encoding="utf-8")
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setAttribute(\"style\""):
            assert banned not in script, (name, banned)


@pytest.mark.parametrize("role", ["viewer", "reviewer", "admin"])
def test_me_returns_identity_and_role(ctx, role):
    body = ctx.client.get("/api/v1/auth/me", headers=ctx.headers(role)).json()
    assert body == {"username": f"dash_{role}", "role": role}


def test_me_requires_authentication(ctx):
    assert ctx.client.get("/api/v1/auth/me").status_code == 401
