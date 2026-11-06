"""Tests for administration: users, audit-trail viewer and system status."""

from collections.abc import Generator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.core.security import create_access_token, hash_password, verify_password
from src.core.throttle import login_throttle
from src.db.session import Base, get_db
from src.models.banking import BankingAuditEvent
from src.models.kyc import AuditEvent, KYCApplication
from src.models.user import User

BASE = "/api/v1/admin"
GOOD_PASSWORD = "Strong-Passphrase-42!"


@pytest.fixture
def ctx() -> Generator[SimpleNamespace, None, None]:
    login_throttle.reset()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with factory() as db:
        ids = {}
        for role in ("viewer", "reviewer", "admin"):
            user = User(username=f"adm_{role}", password_hash=hash_password(f"{role.title()}-Password-123!"), role=role, is_active=True)
            db.add(user)
            db.flush()
            ids[role] = user.id
        db.commit()

    def override_get_db() -> Generator:
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    def headers(role="admin"):
        return {"Authorization": f"Bearer {create_access_token(subject=str(ids[role]), role=role)}"}

    try:
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, factory=factory, ids=ids, headers=headers)
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def create(ctx, username="new_user", password=GOOD_PASSWORD, role="reviewer"):
    return ctx.client.post(f"{BASE}/users", headers=ctx.headers(), json={"username": username, "password": password, "role": role})


# ------------------------------------------------------------------ access
@pytest.mark.parametrize("role", ["viewer", "reviewer"])
@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/users"), ("post", "/users"), ("patch", "/users/1"), ("post", "/users/1/reset-password"),
     ("get", "/audit-events"), ("get", "/system")],
)
def test_only_admins_can_use_admin_endpoints(ctx, role, method, path):
    assert getattr(ctx.client, method)(f"{BASE}{path}", headers=ctx.headers(role)).status_code == 403


def test_admin_endpoints_require_authentication(ctx):
    assert ctx.client.get(f"{BASE}/users").status_code == 401


# ------------------------------------------------------------------- users
def test_admin_lists_users_without_password_hashes(ctx):
    response = ctx.client.get(f"{BASE}/users", headers=ctx.headers())
    assert [u["username"] for u in response.json()] == ["adm_viewer", "adm_reviewer", "adm_admin"]
    assert "password" not in response.text and "argon2" not in response.text


def test_create_user_then_login_and_audit(ctx):
    response = create(ctx)
    assert response.status_code == 201 and response.json()["role"] == "reviewer" and response.json()["is_active"] is True
    login = ctx.client.post("/api/v1/auth/token", data={"username": "new_user", "password": GOOD_PASSWORD})
    assert login.status_code == 200
    with ctx.factory() as db:
        event = db.scalar(select(BankingAuditEvent).where(BankingAuditEvent.action == "USER_CREATED"))
    assert event.actor_username == "adm_admin" and GOOD_PASSWORD not in event.details_ar


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "ab", "password": GOOD_PASSWORD, "role": "viewer"},  # too short
        {"username": "bad name!", "password": GOOD_PASSWORD, "role": "viewer"},
        {"username": "_leading", "password": GOOD_PASSWORD, "role": "viewer"},
        {"username": "ok_name", "password": "short", "role": "viewer"},
        {"username": "ok_name", "password": "alllowercaseletters", "role": "viewer"},
        {"username": "ok_name", "password": "Ok_name-1234567", "role": "viewer"},  # contains the username
        {"username": "ok_name", "password": "aaaaaaaaaaaaA1", "role": "viewer"},
        {"username": "ok_name", "password": GOOD_PASSWORD, "role": "superuser"},
        {"username": "ok_name", "password": GOOD_PASSWORD, "role": "viewer", "is_active": False},
    ],
)
def test_create_user_rejects_bad_input(ctx, payload):
    assert ctx.client.post(f"{BASE}/users", headers=ctx.headers(), json=payload).status_code == 422


def test_duplicate_username_is_rejected_case_insensitively(ctx):
    assert create(ctx, "Same_Name").status_code == 201
    assert create(ctx, "same_name").status_code == 409


def test_deactivation_blocks_the_existing_token_and_login_immediately(ctx):
    created = create(ctx, "temp_user", role="reviewer").json()
    token = ctx.client.post("/api/v1/auth/token", data={"username": "temp_user", "password": GOOD_PASSWORD}).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert ctx.client.get("/api/v1/dashboard/summary", headers=auth).status_code == 200
    assert ctx.client.patch(f"{BASE}/users/{created['id']}", headers=ctx.headers(), json={"is_active": False}).status_code == 200
    assert ctx.client.get("/api/v1/dashboard/summary", headers=auth).status_code == 401
    assert ctx.client.post("/api/v1/auth/token", data={"username": "temp_user", "password": GOOD_PASSWORD}).status_code == 401


def test_role_change_takes_effect_on_the_next_request(ctx):
    created = create(ctx, "promoted", role="viewer").json()
    token = ctx.client.post("/api/v1/auth/token", data={"username": "promoted", "password": GOOD_PASSWORD}).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert ctx.client.get(f"{BASE}/users", headers=auth).status_code == 403
    ctx.client.patch(f"{BASE}/users/{created['id']}", headers=ctx.headers(), json={"role": "admin"})
    # the token still carries "viewer"; authorization uses the role stored in the database
    assert ctx.client.get(f"{BASE}/users", headers=auth).status_code == 200
    ctx.client.patch(f"{BASE}/users/{created['id']}", headers=ctx.headers(), json={"role": "viewer"})
    assert ctx.client.get(f"{BASE}/users", headers=auth).status_code == 403


def test_admin_cannot_change_or_deactivate_themselves(ctx):
    for body in ({"role": "viewer"}, {"is_active": False}):
        assert ctx.client.patch(f"{BASE}/users/{ctx.ids['admin']}", headers=ctx.headers(), json=body).status_code == 409


def test_two_admins_can_demote_each_other_but_not_themselves(ctx):
    second = create(ctx, "second_admin", role="admin").json()
    h2 = {"Authorization": "Bearer " + create_access_token(subject=str(second["id"]), role="admin")}
    assert ctx.client.patch(f"{BASE}/users/{second['id']}", headers=h2, json={"is_active": False}).status_code == 409
    assert ctx.client.patch(f"{BASE}/users/{ctx.ids['admin']}", headers=h2, json={"role": "viewer"}).status_code == 200


def test_last_admin_guard_blocks_removal_even_if_state_is_inconsistent(ctx, monkeypatch):
    import src.api.admin as admin_module

    second = create(ctx, "second_admin", role="admin").json()
    monkeypatch.setattr(admin_module, "_active_admins", lambda db: 1)
    r = ctx.client.patch(f"{BASE}/users/{second['id']}", headers=ctx.headers(), json={"is_active": False})
    assert r.status_code == 409 and "آخر مدير" in r.json()["detail"]


def test_unknown_user_and_empty_update(ctx):
    assert ctx.client.patch(f"{BASE}/users/9999", headers=ctx.headers(), json={"role": "viewer"}).status_code == 404
    assert ctx.client.patch(f"{BASE}/users/{ctx.ids['viewer']}", headers=ctx.headers(), json={}).status_code == 422


def test_reset_password_changes_login_and_is_audited_without_the_secret(ctx):
    new = "Another-Strong-Pass-7#"
    r = ctx.client.post(f"{BASE}/users/{ctx.ids['viewer']}/reset-password", headers=ctx.headers(), json={"new_password": new})
    assert r.status_code == 204
    assert ctx.client.post("/api/v1/auth/token", data={"username": "adm_viewer", "password": new}).status_code == 200
    assert ctx.client.post("/api/v1/auth/token", data={"username": "adm_viewer", "password": "Viewer-Password-123!"}).status_code == 401
    with ctx.factory() as db:
        events = db.scalars(select(BankingAuditEvent).where(BankingAuditEvent.action == "PASSWORD_RESET")).all()
        stored = db.get(User, ctx.ids["viewer"]).password_hash
    assert len(events) == 1 and new not in events[0].details_ar
    assert verify_password(new, stored)


def test_reset_password_enforces_the_policy(ctx):
    r = ctx.client.post(f"{BASE}/users/{ctx.ids['viewer']}/reset-password", headers=ctx.headers(), json={"new_password": "weak"})
    assert r.status_code == 422


# -------------------------------------------------------------- audit trail
def test_audit_viewer_merges_sources_newest_first_and_filters(ctx):
    create(ctx, "audited")
    with ctx.factory() as db:
        app_row = KYCApplication(application_number="KYC-A-1", customer_name="x")
        db.add(app_row)
        db.flush()
        db.add(AuditEvent(application_id=app_row.id, action="HUMAN_REVIEW", actor_type="human", actor_name="rev", details_ar="n" * 500))
        db.commit()
    body = ctx.client.get(f"{BASE}/audit-events", headers=ctx.headers()).json()
    assert body["total"] == 2 and {r["source"] for r in body["items"]} == {"banking", "kyc"}
    assert [r["at"] for r in body["items"]] == sorted((r["at"] for r in body["items"]), reverse=True)
    kyc = next(r for r in body["items"] if r["source"] == "kyc")
    assert len(kyc["details"]) == 300  # long free text is cut
    only = ctx.client.get(f"{BASE}/audit-events?source=banking&action=USER_CREATED", headers=ctx.headers()).json()
    assert only["total"] == 1 and only["items"][0]["action"] == "USER_CREATED"


def test_audit_viewer_paginates(ctx):
    with ctx.factory() as db:
        for i in range(30):
            db.add(BankingAuditEvent(entity_type="x", entity_id=i, action="A", actor_role="system", actor_username="s", details_ar="d"))
        db.commit()
    first = ctx.client.get(f"{BASE}/audit-events?page_size=25", headers=ctx.headers()).json()
    second = ctx.client.get(f"{BASE}/audit-events?page_size=25&page=2", headers=ctx.headers()).json()
    assert first["total"] == 30 and len(first["items"]) == 25 and len(second["items"]) == 5
    assert ctx.client.get(f"{BASE}/audit-events?page_size=500", headers=ctx.headers()).status_code == 422


# ------------------------------------------------------------------- system
def test_system_status_has_no_secrets(ctx):
    body = ctx.client.get(f"{BASE}/system", headers=ctx.headers()).json()
    assert body["users_by_role"] == {"admin": 1, "reviewer": 1, "viewer": 1, "customer": 0}
    assert body["database"] == "sqlite" and body["llm_provider"] in ("none", "ollama")
    text = str(body).lower()
    assert "secret" not in text and "jwt" not in text and "password" not in text
