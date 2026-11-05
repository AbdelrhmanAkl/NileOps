"""Tests for login throttling, security headers and the password policy."""

from collections.abc import Generator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.core.passwords import validate_password
from src.core.security import hash_password
from src.core.throttle import LoginThrottle, login_throttle
from src.db.session import Base, get_db
from src.models.banking import BankingAuditEvent
from src.models.user import User

LOGIN = "/api/v1/auth/token"
PASSWORD = "Correct-Horse-Battery-9!"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# ---------------------------------------------------------- throttle (unit)
def test_pair_lock_after_five_failures_and_expiry():
    clock = Clock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(4):
        assert throttle.register_failure("ali", "1.1.1.1") is False
    assert throttle.retry_after("ali", "1.1.1.1") == 0
    assert throttle.register_failure("ali", "1.1.1.1") is True  # fifth failure locks
    assert 0 < throttle.retry_after("ali", "1.1.1.1") <= 901
    clock.now += 901
    assert throttle.retry_after("ali", "1.1.1.1") == 0


def test_other_address_is_not_locked_out_by_someone_elses_failures():
    throttle = LoginThrottle()
    for _ in range(5):
        throttle.register_failure("ali", "6.6.6.6")
    assert throttle.retry_after("ali", "6.6.6.6") > 0
    assert throttle.retry_after("ali", "1.1.1.1") == 0  # the real employee can still log in


def test_address_lock_after_twenty_failures_across_usernames():
    throttle = LoginThrottle()
    for i in range(20):
        throttle.register_failure(f"user{i}", "9.9.9.9")
    assert throttle.retry_after("someone_new", "9.9.9.9") > 0


def test_success_clears_the_pair_counter():
    throttle = LoginThrottle()
    for _ in range(4):
        throttle.register_failure("ali", "1.1.1.1")
    throttle.register_success("ali", "1.1.1.1")
    assert throttle.register_failure("ali", "1.1.1.1") is False


def test_old_failures_age_out_of_the_window():
    clock = Clock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(4):
        throttle.register_failure("ali", "1.1.1.1")
    clock.now += 1000
    assert throttle.register_failure("ali", "1.1.1.1") is False


def test_usernames_are_case_insensitive():
    throttle = LoginThrottle()
    for _ in range(5):
        throttle.register_failure("Ali", "1.1.1.1")
    assert throttle.retry_after("ALI", "1.1.1.1") > 0


# -------------------------------------------------------------- login (API)
@pytest.fixture
def ctx() -> Generator[SimpleNamespace, None, None]:
    login_throttle.reset()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with factory() as db:
        db.add(User(username="throttled", password_hash=hash_password(PASSWORD), role="viewer", is_active=True))
        db.commit()

    def override_get_db() -> Generator:
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, factory=factory)
    finally:
        login_throttle.reset()
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def attempt(ctx, password, username="throttled"):
    return ctx.client.post(LOGIN, data={"username": username, "password": password})


def test_repeated_wrong_passwords_lock_login_even_for_the_right_password(ctx):
    for _ in range(5):
        assert attempt(ctx, "wrong").status_code == 401
    locked = attempt(ctx, PASSWORD)
    assert locked.status_code == 429 and int(locked.headers["Retry-After"]) > 0


def test_lock_is_audited_with_the_real_account_only(ctx):
    for _ in range(5):
        attempt(ctx, "wrong")
    for _ in range(5):
        attempt(ctx, "wrong", username="<script>attacker-text</script>")
    with ctx.factory() as db:
        events = db.scalars(select(BankingAuditEvent).where(BankingAuditEvent.entity_type == "auth")).all()
    assert [e.action for e in events] == ["LOGIN_LOCKED"]
    assert events[0].actor_username == "throttled"
    assert "attacker" not in " ".join(e.details_ar + e.actor_username for e in events)


def test_successful_login_still_works_and_resets_counters(ctx):
    for _ in range(3):
        attempt(ctx, "wrong")
    assert attempt(ctx, PASSWORD).status_code == 200
    for _ in range(4):
        assert attempt(ctx, "wrong").status_code == 401  # counter restarted


def test_unknown_usernames_get_the_same_answer_and_count_toward_the_address_limit(ctx):
    for i in range(20):
        assert attempt(ctx, "x", username=f"ghost{i}").status_code == 401
    assert attempt(ctx, "x", username="ghost-final").status_code == 429


# ------------------------------------------------------------------ headers
def test_security_headers_on_api_and_pages(ctx):
    health = ctx.client.get("/health")
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert health.headers["referrer-policy"] == "no-referrer"
    api = ctx.client.get("/api/v1/dashboard/summary")
    assert api.headers["cache-control"] == "no-store"
    page = ctx.client.get("/dashboard")
    assert "default-src 'none'" in page.headers["content-security-policy"]  # the page keeps its own stricter policy


# ----------------------------------------------------------------- password
@pytest.mark.parametrize(
    ("password", "username"),
    [("Short1!a", ""), ("alllowercaseletters", ""), ("NoDigitsOrSymbolsHere", ""), ("Maha-Strong-Pass1", "maha"),
     ("aaaaaaaaaaaaA1", ""), ("x" * 129, "")],
)
def test_weak_passwords_are_rejected(password, username):
    with pytest.raises(ValueError):
        validate_password(password, username)


@pytest.mark.parametrize("password", ["Correct-Horse-Battery-9!", "كلمة-مرور-Strong-2026", "Aa1!Aa1!Aa1!Bb"])
def test_good_passwords_are_accepted(password):
    validate_password(password, "someone")
