"""Customer portal: self-registration, admin-created customers, isolation between customers, staff lock-out."""

import io
from collections.abc import Generator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.api.documents as documents_module
import src.api.portal as portal_module
from src.api.main import app
from src.core.config import get_settings
from src.core.security import create_access_token, hash_password
from src.core.throttle import login_throttle, register_throttle
from src.db.session import Base, get_db
from src.models.banking import BankAccount, BankingAuditEvent, CardIssuanceRequest, Complaint, Customer
from src.models.kyc import ApplicationStatus, KYCApplication
from src.models.portal import CustomerAccount
from src.models.user import User

PW = "Customer-Pass-2026!"
P = "/api/v1/portal"
SECRET_NOTE = "ملاحظة داخلية سرية للمراجع 778899"
LONG_COMPLAINT = "البطاقة الجديدة لم تصلني منذ أسبوعين رغم اعتمادها"


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (220, 140), "white").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def ctx(tmp_path, monkeypatch) -> Generator[SimpleNamespace, None, None]:
    settings = get_settings().model_copy(update={"upload_dir": str(tmp_path), "max_upload_size_mb": 1})
    monkeypatch.setattr(documents_module, "get_settings", lambda: settings)
    login_throttle.reset()
    register_throttle.reset()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    users = {}
    with factory() as db:
        for role in ("viewer", "reviewer", "admin"):
            u = User(username=f"pt_{role}", password_hash=hash_password(f"{role.title()}-Password-123!"), role=role, is_active=True)
            db.add(u)
            db.flush()
            users[role] = u.id
        db.commit()

    def override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override
    client = TestClient(app)

    def staff(role):
        return {"Authorization": f"Bearer {create_access_token(subject=str(users[role]), role=role)}"}

    def register(username="alice", name="أليس محمد علي", password=PW, **extra):
        return client.post(f"{P}/register", json={"full_name": name, "username": username, "password": password, **extra})

    def login(username, password=PW):
        r = client.post("/api/v1/auth/token", data={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def new_customer(username):
        assert register(username).status_code == 201
        return login(username)

    try:
        yield SimpleNamespace(client=client, factory=factory, staff=staff, register=register, login=login,
                              new_customer=new_customer, settings=settings)
    finally:
        client.close()
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


# ------------------------------------------------------------- registration
def test_self_registration_creates_a_customer_who_can_log_in(ctx):
    r = ctx.register()
    assert r.status_code == 201 and r.json()["username"] == "alice"
    headers = ctx.login("alice")
    me = ctx.client.get("/api/v1/auth/me", headers=headers).json()
    assert me == {"username": "alice", "role": "customer"}
    overview = ctx.client.get(f"{P}/overview", headers=headers).json()
    assert overview["customer_name"] == "أليس محمد علي" and overview["customer_ref"].startswith("CUS-")
    assert overview["complaints"] == overview["card_requests"] == overview["kyc_applications"] == []


def test_registration_cannot_choose_a_role(ctx):
    assert ctx.register(role="admin").status_code == 422
    assert ctx.register(username="bob", is_active=True).status_code == 422
    with ctx.factory() as db:
        assert {u.role for u in db.scalars(select(User))} == {"viewer", "reviewer", "admin"}


@pytest.mark.parametrize(
    ("username", "password", "code"),
    [("al", PW, 422), ("bad name!", PW, 422), ("carol", "short1A!", 422), ("carol", "alllowercase123456", 422), ("carol", "carol-Pass-2026!", 422)],
)
def test_registration_validates_input(ctx, username, password, code):
    assert ctx.register(username=username, password=password).status_code == code


def test_duplicate_usernames_are_rejected_case_insensitively(ctx):
    assert ctx.register("dave").status_code == 201
    assert ctx.register("DAVE").status_code == 409
    assert ctx.register("pt_admin").status_code == 409


def test_registration_can_be_switched_off(ctx, monkeypatch):
    monkeypatch.setattr(portal_module, "get_settings", lambda: ctx.settings.model_copy(update={"allow_self_registration": False}))
    assert ctx.register().status_code == 403


def test_registration_is_rate_limited_per_address(ctx):
    for i in range(5):
        assert ctx.register(f"user{i}").status_code == 201
    blocked = ctx.register("user99")
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0


def test_registration_audit_never_contains_the_password(ctx):
    ctx.register()
    with ctx.factory() as db:
        events = db.scalars(select(BankingAuditEvent).where(BankingAuditEvent.action == "CUSTOMER_REGISTERED")).all()
    assert len(events) == 1 and PW not in events[0].details_ar and events[0].actor_role == "customer"


# ------------------------------------------------------- admin-created accounts
def test_admin_can_create_a_customer_account(ctx):
    body = {"username": "erin", "password": PW, "role": "customer", "full_name": "إيرين حسن"}
    assert ctx.client.post("/api/v1/admin/users", headers=ctx.staff("admin"), json=body).status_code == 201
    overview = ctx.client.get(f"{P}/overview", headers=ctx.login("erin")).json()
    assert overview["customer_name"] == "إيرين حسن"


def test_admin_must_give_a_name_for_customer_accounts(ctx):
    body = {"username": "erin", "password": PW, "role": "customer"}
    assert ctx.client.post("/api/v1/admin/users", headers=ctx.staff("admin"), json=body).status_code == 422
    with ctx.factory() as db:
        assert db.scalar(select(User).where(User.username == "erin")) is None


def test_only_admin_creates_users_and_roles_never_cross_customer_boundary(ctx):
    body = {"username": "erin", "password": PW, "role": "customer", "full_name": "إيرين حسن"}
    assert ctx.client.post("/api/v1/admin/users", headers=ctx.staff("reviewer"), json=body).status_code == 403
    ctx.register("frank")
    with ctx.factory() as db:
        customer_id = db.scalar(select(User.id).where(User.username == "frank"))
    h = ctx.staff("admin")
    assert ctx.client.patch(f"/api/v1/admin/users/{customer_id}", headers=h, json={"role": "admin"}).status_code == 409
    viewer_id = ctx.client.get("/api/v1/admin/users", headers=h).json()
    viewer_id = next(u["id"] for u in viewer_id if u["username"] == "pt_viewer")
    assert ctx.client.patch(f"/api/v1/admin/users/{viewer_id}", headers=h, json={"role": "customer"}).status_code == 409
    # deactivating a customer is fine and locks them out
    assert ctx.client.patch(f"/api/v1/admin/users/{customer_id}", headers=h, json={"is_active": False}).status_code == 200
    assert ctx.client.post("/api/v1/auth/token", data={"username": "frank", "password": PW}).status_code == 401


# ------------------------------------------------------------ role separation
STAFF_GET = [
    "/api/v1/customers",
    "/api/v1/customers/1",
    "/api/v1/dashboard/summary", "/api/v1/kyc/applications", "/api/v1/kyc/applications/1", "/api/v1/banking/complaints",
    "/api/v1/banking/complaints/1", "/api/v1/banking/card-issuance", "/api/v1/banking/account-opening",
    "/api/v1/policies", "/api/v1/admin/users", "/api/v1/admin/audit-events", "/api/v1/admin/system",
    "/api/v1/explanations/applications/1", "/api/v1/explanations/complaints/1", "/api/v1/kyc/documents/1/files",
]


@pytest.mark.parametrize("path", STAFF_GET)
def test_customers_are_locked_out_of_every_staff_endpoint(ctx, path):
    headers = ctx.new_customer("gina")
    assert ctx.client.get(path, headers=headers).status_code == 403


def test_customers_cannot_write_to_staff_endpoints(ctx):
    headers = ctx.new_customer("gina")
    calls = [
        ("post", "/api/v1/policies/ask", {"question": "كم مدة تسليم البطاقة؟"}),
        ("post", "/api/v1/policies/reload", None),
        ("post", "/api/v1/banking/complaints", {"description": LONG_COMPLAINT}),
        ("post", "/api/v1/kyc/applications/1/review", {"decision": "APPROVED", "notes": "xxx"}),
        ("post", "/api/v1/banking/card-issuance/1/review", {"decision": "approved", "notes": "xxx"}),
        ("post", "/api/v1/admin/users", {"username": "evil", "password": PW, "role": "admin"}),
    ]
    for method, path, body in calls:
        r = getattr(ctx.client, method)(path, headers=headers, **({"json": body} if body else {}))
        assert r.status_code == 403, (path, r.status_code)


@pytest.mark.parametrize("role", ["viewer", "reviewer", "admin"])
def test_staff_cannot_use_the_customer_portal(ctx, role):
    assert ctx.client.get(f"{P}/overview", headers=ctx.staff(role)).status_code == 403
    assert ctx.client.post(f"{P}/complaints", headers=ctx.staff(role), json={"description": LONG_COMPLAINT}).status_code == 403


def test_portal_requires_authentication(ctx):
    assert ctx.client.get(f"{P}/overview").status_code == 401
    assert ctx.client.post(f"{P}/complaints", json={"description": LONG_COMPLAINT}).status_code == 401


# ---------------------------------------------------------------- requests
def test_customer_creates_requests_and_sees_only_friendly_status(ctx):
    h = ctx.new_customer("hana")
    assert ctx.client.post(f"{P}/account-requests", headers=h, json={"account_type": "savings"}).status_code == 201
    assert ctx.client.post(f"{P}/card-requests", headers=h, json={"card_type": "debit"}).status_code == 422  # needs an account
    assert ctx.client.post(f"{P}/card-requests", headers=h, json={"card_type": "debit", "account_id": make_account(ctx, "hana")}).status_code == 201
    c = ctx.client.post(f"{P}/complaints", headers=h, json={"description": LONG_COMPLAINT})
    assert c.status_code == 201 and c.json()["number"].startswith("SIM-COMP-")
    o = ctx.client.get(f"{P}/overview", headers=h).json()
    assert [x["status"] for x in o["account_requests"]] == ["pending"]
    assert o["account_requests"][0]["status_title"] == "تم استلام طلبك"
    assert len(o["card_requests"]) == 1 and len(o["complaints"]) == 1


@pytest.mark.parametrize("payload", [{"account_type": "gold"}, {}, {"account_type": "savings", "status": "approved"}, {"account_type": "savings", "customer_id": 1}])
def test_account_request_validates_and_ignores_no_extra_fields(ctx, payload):
    assert ctx.client.post(f"{P}/account-requests", headers=ctx.new_customer("ivy"), json=payload).status_code == 422


def test_complaint_text_length_is_validated(ctx):
    h = ctx.new_customer("ivy")
    assert ctx.client.post(f"{P}/complaints", headers=h, json={"description": "قصير"}).status_code == 422
    assert ctx.client.post(f"{P}/complaints", headers=h, json={"description": "س" * 2001}).status_code == 422
    assert ctx.client.post(f"{P}/complaints", headers=h, json={"description": LONG_COMPLAINT, "customer_id": 1}).status_code == 422


def test_open_request_limits_stop_spam(ctx):
    h = ctx.new_customer("jack")
    assert ctx.client.post(f"{P}/account-requests", headers=h, json={"account_type": "current"}).status_code == 201
    assert ctx.client.post(f"{P}/account-requests", headers=h, json={"account_type": "current"}).status_code == 409


# ---------------------------------------------------------------- isolation
def approve_kyc_for(ctx, username):
    """Simulate a human reviewer having approved the customer's KYC (status set directly)."""
    h = ctx.login(username)
    kyc = ctx.client.post(f"{P}/kyc-applications", headers=h).json()
    with ctx.factory() as db:
        app_row = db.get(KYCApplication, kyc["id"])
        app_row.status = ApplicationStatus.APPROVED.value
        db.commit()


def make_account(ctx, username):
    with ctx.factory() as db:
        customer_id = db.scalar(select(CustomerAccount.customer_id).join(User, User.id == CustomerAccount.user_id).where(User.username == username))
        account = BankAccount(account_ref=f"ACC-{username}", customer_id=customer_id, account_type="savings", status="active")
        db.add(account)
        db.commit()
        return account.id


def test_a_customer_never_sees_or_touches_another_customers_items(ctx):
    a, b = ctx.new_customer("anna"), ctx.new_customer("boris")
    complaint_id = ctx.client.post(f"{P}/complaints", headers=a, json={"description": LONG_COMPLAINT}).json()["id"]
    kyc = ctx.client.post(f"{P}/kyc-applications", headers=a).json()
    account_id = make_account(ctx, "anna")
    doc_id = kyc["documents"][0]["id"]

    assert ctx.client.get(f"{P}/overview", headers=b).json()["complaints"] == []
    assert ctx.client.get(f"{P}/complaints/{complaint_id}", headers=b).status_code == 404
    assert ctx.client.get(f"{P}/kyc-applications/{kyc['id']}", headers=b).status_code == 404
    up = ctx.client.post(f"{P}/kyc-applications/{kyc['id']}/documents/{doc_id}/files", headers=b, files={"file": ("x.png", png(), "image/png")})
    assert up.status_code == 404
    assert ctx.client.post(f"{P}/card-requests", headers=b, json={"card_type": "debit", "account_id": account_id}).status_code == 404
    # the owner still can
    assert ctx.client.get(f"{P}/complaints/{complaint_id}", headers=a).status_code == 200
    assert ctx.client.post(f"{P}/card-requests", headers=a, json={"card_type": "debit", "account_id": account_id}).status_code == 201


def test_document_of_my_application_is_not_reachable_through_another_application(ctx):
    a = ctx.new_customer("anna")
    first = ctx.client.post(f"{P}/kyc-applications", headers=a).json()
    ctx.client.post(f"{P}/kyc-applications", headers=ctx.new_customer("boris"))
    with ctx.factory() as db:
        from src.models.kyc import KYCDocument

        foreign_doc = db.scalars(select(KYCDocument).where(KYCDocument.application_id != first["id"])).first().id
    r = ctx.client.post(f"{P}/kyc-applications/{first['id']}/documents/{foreign_doc}/files", headers=a, files={"file": ("x.png", png(), "image/png")})
    assert r.status_code == 404


def test_customers_never_receive_internal_staff_information(ctx):
    h = ctx.new_customer("kim")
    card = ctx.client.post(f"{P}/card-requests", headers=h, json={"card_type": "debit", "account_id": make_account(ctx, "kim")}).json()
    complaint = ctx.client.post(f"{P}/complaints", headers=h, json={"description": LONG_COMPLAINT}).json()
    reviewer = ctx.staff("reviewer")
    ctx.client.post(f"/api/v1/banking/card-issuance/{card['id']}/review", headers=reviewer, json={"decision": "under_review", "notes": SECRET_NOTE})
    ctx.client.post(f"/api/v1/banking/complaints/{complaint['id']}/assign", headers=reviewer, json={"department": "إدارة البطاقات", "notes": SECRET_NOTE})
    text = ctx.client.get(f"{P}/overview", headers=h).text + ctx.client.get(f"{P}/complaints/{complaint['id']}", headers=h).text
    for leaked in (SECRET_NOTE, "778899", "pt_reviewer", "routing", "suggested_reply", "review_notes", "assigned_department", "actor"):
        assert leaked not in text, leaked
    assert "قيد المراجعة" in text  # the customer does see the friendly status


def test_complaint_timeline_follows_staff_progress(ctx):
    h = ctx.new_customer("lena")
    cid = ctx.client.post(f"{P}/complaints", headers=h, json={"description": LONG_COMPLAINT}).json()["id"]
    reviewer = ctx.staff("reviewer")
    ctx.client.post(f"/api/v1/banking/complaints/{cid}/assign", headers=reviewer, json={"department": "إدارة البطاقات", "notes": "توجيه"})
    ctx.client.post(f"/api/v1/banking/complaints/{cid}/status", headers=reviewer, json={"status": "in_progress", "notes": "بدأنا"})
    view = ctx.client.get(f"{P}/complaints/{cid}", headers=h).json()
    assert view["status"] == "in_progress"
    assert [s["done"] for s in view["timeline"]] == [True, True, True, False, False]


def test_staff_see_and_decide_portal_requests_and_customer_sees_the_result(ctx):
    h = ctx.new_customer("mona")
    card = ctx.client.post(f"{P}/card-requests", headers=h, json={"card_type": "prepaid", "account_id": make_account(ctx, "mona")}).json()
    reviewer = ctx.staff("reviewer")
    listed = ctx.client.get("/api/v1/banking/card-issuance", headers=reviewer).json()["items"]
    assert any(x["request_number"] == card["number"] for x in listed)
    for decision in ("under_review", "approved"):
        assert ctx.client.post(f"/api/v1/banking/card-issuance/{card['id']}/review", headers=reviewer, json={"decision": decision, "notes": "قرار"}).status_code == 200
    assert ctx.client.get(f"{P}/overview", headers=h).json()["card_requests"][0]["status_title"] == "تمت الموافقة"


# ------------------------------------------------------------------- KYC
def test_kyc_flow_upload_review_and_lock(ctx):
    h = ctx.new_customer("nora")
    kyc = ctx.client.post(f"{P}/kyc-applications", headers=h).json()
    assert kyc["status"] == "قيد المراجعة" and len(kyc["documents"]) == 3
    assert all(d["status"] == "غير مرفق" and d["can_upload"] for d in kyc["documents"])
    doc = kyc["documents"][0]
    url = f"{P}/kyc-applications/{kyc['id']}/documents/{doc['id']}/files"
    assert ctx.client.post(url, headers=h, files={"file": ("id.png", png(), "image/png")}).status_code == 201
    after = ctx.client.get(f"{P}/kyc-applications/{kyc['id']}", headers=h).json()
    assert after["documents"][0]["status"] == "تم الإرفاق" and after["documents"][0]["status_text"].startswith("تم الرفع")
    # the reviewer sees the application, the file and the customer's upload in the audit trail
    reviewer = ctx.staff("reviewer")
    files = ctx.client.get(f"/api/v1/kyc/documents/{doc['id']}/files", headers=reviewer).json()
    assert files[0]["uploaded_by"] == "nora"
    detail = ctx.client.get(f"/api/v1/kyc/applications/{kyc['id']}", headers=reviewer).json()
    assert any(e["action"] == "DOCUMENT_UPLOADED" and e["actor_type"] == "customer" for e in detail["audit_events"])
    # once the reviewer accepts the document, the customer cannot replace it
    assert ctx.client.post(f"/api/v1/kyc/documents/{doc['id']}/review", headers=reviewer, json={"decision": "VALID", "notes": "مطابق"}).status_code == 200
    again = ctx.client.post(url, headers=h, files={"file": ("id2.png", png(), "image/png")})
    assert again.status_code == 409
    assert ctx.client.get(f"{P}/kyc-applications/{kyc['id']}", headers=h).json()["documents"][0]["can_upload"] is False


def test_unsafe_uploads_are_rejected(ctx):
    h = ctx.new_customer("omar")
    kyc = ctx.client.post(f"{P}/kyc-applications", headers=h).json()
    url = f"{P}/kyc-applications/{kyc['id']}/documents/{kyc['documents'][0]['id']}/files"
    assert ctx.client.post(url, headers=h, files={"file": ("a.png", b"<script>alert(1)</script>", "image/png")}).status_code in (400, 415, 422)
    assert ctx.client.post(url, headers=h, files={"file": ("big.png", b"0" * (2 * 1024 * 1024), "image/png")}).status_code == 413


def test_only_one_or_two_open_kyc_applications_at_a_time(ctx):
    h = ctx.new_customer("pam")
    assert ctx.client.post(f"{P}/kyc-applications", headers=h).status_code == 201
    assert ctx.client.post(f"{P}/kyc-applications", headers=h).status_code == 201
    assert ctx.client.post(f"{P}/kyc-applications", headers=h).status_code == 409


def test_customer_actions_are_audited_with_the_customer_role(ctx):
    h = ctx.new_customer("rita")
    ctx.client.post(f"{P}/account-requests", headers=h, json={"account_type": "savings"})
    ctx.client.post(f"{P}/complaints", headers=h, json={"description": LONG_COMPLAINT})
    with ctx.factory() as db:
        rows = db.scalars(select(BankingAuditEvent).where(BankingAuditEvent.actor_username == "rita")).all()
    assert {r.action for r in rows} >= {"CUSTOMER_REGISTERED", "CUSTOMER_REQUEST_CREATED", "COMPLAINT_CREATED"}
    assert {r.actor_role for r in rows} == {"customer"}
    assert LONG_COMPLAINT not in " ".join(r.details_ar for r in rows)


def test_full_customer_journey_account_then_card(ctx):
    h = ctx.new_customer("sara")
    reviewer = ctx.staff("reviewer")
    req = ctx.client.post(f"{P}/account-requests", headers=h, json={"account_type": "current"}).json()
    assert ctx.client.get(f"{P}/overview", headers=h).json()["accounts"] == []
    approve_kyc_for(ctx, "sara")
    for decision in ("under_review", "approved"):
        r = ctx.client.post(f"/api/v1/banking/account-opening/{req['id']}/review", headers=reviewer, json={"decision": decision, "notes": "قرار المراجع"})
        assert r.status_code == 200, r.text
    accounts = ctx.client.get(f"{P}/overview", headers=h).json()["accounts"]
    assert len(accounts) == 1 and accounts[0]["status"] == "active" and accounts[0]["account_ref"].startswith("SIM-ACC-")
    card = ctx.client.post(f"{P}/card-requests", headers=h, json={"card_type": "debit", "account_id": accounts[0]["id"]}).json()
    for decision in ("under_review", "approved"):
        assert ctx.client.post(f"/api/v1/banking/card-issuance/{card['id']}/review", headers=reviewer, json={"decision": decision, "notes": "قرار"}).status_code == 200
    assert ctx.client.post(f"/api/v1/banking/card-issuance/{card['id']}/issue", headers=reviewer).status_code == 200
    assert ctx.client.get(f"{P}/overview", headers=h).json()["card_requests"][0]["status_title"] == "تم الإصدار"


def test_card_request_needs_an_active_account(ctx):
    h = ctx.new_customer("tala")
    account_id = make_account(ctx, "tala")
    with ctx.factory() as db:
        db.get(BankAccount, account_id).status = "frozen"
        db.commit()
    assert ctx.client.post(f"{P}/card-requests", headers=h, json={"card_type": "debit", "account_id": account_id}).status_code == 409


def test_account_cannot_be_approved_before_the_customers_kyc_is_approved(ctx):
    h = ctx.new_customer("kareem")
    reviewer = ctx.staff("reviewer")
    req = ctx.client.post(f"{P}/account-requests", headers=h, json={"account_type": "savings"}).json()
    review = f"/api/v1/banking/account-opening/{req['id']}/review"
    assert ctx.client.post(review, headers=reviewer, json={"decision": "under_review", "notes": "بدء"}).status_code == 200
    blocked = ctx.client.post(review, headers=reviewer, json={"decision": "approved", "notes": "موافقة"})
    assert blocked.status_code == 409 and "KYC" in blocked.json()["detail"]
    assert ctx.client.get(f"{P}/overview", headers=h).json()["accounts"] == []
    detail = ctx.client.get(f"/api/v1/banking/account-opening/{req['id']}", headers=reviewer).json()
    assert detail["kyc_approved"] is False
    approve_kyc_for(ctx, "kareem")
    assert ctx.client.get(f"/api/v1/banking/account-opening/{req['id']}", headers=reviewer).json()["kyc_approved"] is True
    assert ctx.client.post(review, headers=reviewer, json={"decision": "approved", "notes": "موافقة"}).status_code == 200
    assert len(ctx.client.get(f"{P}/overview", headers=h).json()["accounts"]) == 1


def test_staff_notes_never_store_long_identifier_numbers(ctx):
    h = ctx.new_customer("laila")
    reviewer = ctx.staff("reviewer")
    req = ctx.client.post(f"{P}/account-requests", headers=h, json={"account_type": "savings"}).json()
    note = "العميل رقمه القومي 29901011234567 وموبايله 01012345678"
    r = ctx.client.post(f"/api/v1/banking/account-opening/{req['id']}/review", headers=reviewer, json={"decision": "under_review", "notes": note})
    assert r.status_code == 200
    with ctx.factory() as db:
        events = db.scalars(select(BankingAuditEvent).where(BankingAuditEvent.action == "HUMAN_REVIEW")).all()
        assert events and all("29901011234567" not in e.details_ar and "01012345678" not in e.details_ar for e in events)
    detail = ctx.client.get(f"/api/v1/banking/account-opening/{req['id']}", headers=reviewer).json()
    assert "29901011234567" not in (detail["review_notes"] or "")


def test_customer_cannot_hold_two_open_requests_or_two_accounts_of_the_same_type(ctx):
    h = ctx.new_customer("mariam")
    reviewer = ctx.staff("reviewer")
    first = ctx.client.post(f"{P}/account-requests", headers=h, json={"account_type": "savings"})
    assert first.status_code == 201
    again = ctx.client.post(f"{P}/account-requests", headers=h, json={"account_type": "savings"})
    assert again.status_code == 409
    assert ctx.client.post(f"{P}/account-requests", headers=h, json={"account_type": "current"}).status_code == 201

    approve_kyc_for(ctx, "mariam")
    review = f"/api/v1/banking/account-opening/{first.json()['id']}/review"
    for decision in ("under_review", "approved"):
        assert ctx.client.post(review, headers=reviewer, json={"decision": decision, "notes": "مراجعة"}).status_code == 200
    blocked = ctx.client.post(f"{P}/account-requests", headers=h, json={"account_type": "savings"})
    assert blocked.status_code == 409 and "حساب نشط" in blocked.json()["detail"]



def _give_finances(ctx, username, index):
    from datetime import date

    from scripts.seed_core_banking import ensure_branches, seed_customer_finances
    from src.models.portal import CustomerAccount

    with ctx.factory() as db:
        user_id = db.scalar(select(User.id).where(User.username == username))
        customer = db.get(Customer, db.scalar(select(CustomerAccount.customer_id).where(CustomerAccount.user_id == user_id)))
        ensure_branches(db)
        customer.date_of_birth = date(1990, 1, 1)
        seed_customer_finances(db, customer, index, today=date(2026, 11, 1))
        db.commit()


def test_customer_sees_balances_and_a_statement_for_own_accounts_only(ctx):
    a = ctx.new_customer("nadia")
    b = ctx.new_customer("omar")
    _give_finances(ctx, "nadia", 11)
    _give_finances(ctx, "omar", 12)
    mine = ctx.client.get(f"{P}/overview", headers=a).json()
    theirs = ctx.client.get(f"{P}/overview", headers=b).json()
    account = mine["accounts"][0]
    assert account["account_number"].startswith("EG") and account["balance"] > 0
    rows = ctx.client.get(f"{P}/accounts/{account['id']}/transactions?limit=5", headers=a).json()
    assert len(rows) == 5 and rows[0]["balance_after"] >= 0
    assert rows[0]["posted_at"] >= rows[-1]["posted_at"]
    other = theirs["accounts"][0]["id"]
    assert ctx.client.get(f"{P}/accounts/{other}/transactions", headers=a).status_code == 404
    assert ctx.client.get(f"{P}/accounts/999999/transactions", headers=a).status_code == 404


def test_staff_cannot_use_the_customer_statement_endpoint(ctx):
    for role in ("viewer", "reviewer", "admin"):
        assert ctx.client.get(f"{P}/accounts/1/transactions", headers=ctx.staff(role)).status_code == 403
    assert ctx.client.get(f"{P}/accounts/1/transactions").status_code == 401


def test_staff_see_a_full_customer_profile_and_each_view_is_audited(ctx):
    h = ctx.new_customer("rana")
    _give_finances(ctx, "rana", 21)
    ctx.client.post(f"{P}/complaints", headers=h, json={"description": LONG_COMPLAINT})
    listing = ctx.client.get("/api/v1/customers?q=" + "رنا", headers=ctx.staff("viewer"))
    assert listing.status_code == 200
    customer_id = ctx.client.get(f"{P}/overview", headers=h).json()["customer_ref"]
    with ctx.factory() as db:
        cid = db.scalar(select(Customer.id).where(Customer.customer_ref == customer_id))
    for role in ("viewer", "reviewer", "admin"):
        r = ctx.client.get(f"/api/v1/customers/{cid}", headers=ctx.staff(role))
        assert r.status_code == 200
        body = r.json()
        assert body["accounts"] and body["transactions"] and body["totals_egp"] > 0
        assert body["kyc_state"] == "none"
        assert (body["date_of_birth"] is None) == (role == "viewer")
        assert any(x["kind"] == "complaint" for x in body["requests"])
    with ctx.factory() as db:
        views = db.scalars(select(BankingAuditEvent).where(BankingAuditEvent.action == "CUSTOMER_PROFILE_VIEWED")).all()
        assert {v.actor_role for v in views} == {"viewer", "reviewer", "admin"}
    assert ctx.client.get("/api/v1/customers/999999", headers=ctx.staff("admin")).status_code == 404


def test_customer_list_is_paginated_and_searchable(ctx):
    for name in ("sami", "tala"):
        ctx.new_customer(name)
    page = ctx.client.get("/api/v1/customers?page_size=1", headers=ctx.staff("reviewer")).json()
    assert page["total"] >= 2 and len(page["items"]) == 1
    assert ctx.client.get("/api/v1/customers?q=zzzz-none", headers=ctx.staff("reviewer")).json()["total"] == 0
