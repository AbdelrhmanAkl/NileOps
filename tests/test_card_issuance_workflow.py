"""Workflow tests for the simulated card-issuance process.

These tests complement tests/test_banking_api.py and cover review decisions,
account-eligibility checks, simulated issuance, audit logging and RBAC.
"""

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
from src.models.banking import (
    BankAccount,
    BankingAuditEvent,
    CardIssuanceRequest,
    Customer,
)
from src.models.user import User

BASE = "/api/v1/banking/card-issuance"


@pytest.fixture
def ctx() -> Generator[SimpleNamespace, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=engine)

    with session_factory() as db:
        users = {}
        for role in ("viewer", "reviewer", "admin"):
            user = User(
                username=f"card_{role}",
                password_hash=hash_password(f"{role.title()}-Password-123!"),
                role=role,
                is_active=True,
            )
            db.add(user)
            db.flush()
            users[role] = user.id

        customer_a = Customer(customer_ref="SYN-CARD-CUST-A", full_name_ar="عميل أ")
        customer_b = Customer(customer_ref="SYN-CARD-CUST-B", full_name_ar="عميل ب")
        db.add_all([customer_a, customer_b])
        db.flush()

        active_a = BankAccount(
            account_ref="SYN-CARD-ACC-A",
            customer_id=customer_a.id,
            account_type="savings",
            status="active",
        )
        closed_a = BankAccount(
            account_ref="SYN-CARD-ACC-A-CLOSED",
            customer_id=customer_a.id,
            account_type="current",
            status="closed",
        )
        active_b = BankAccount(
            account_ref="SYN-CARD-ACC-B",
            customer_id=customer_b.id,
            account_type="savings",
            status="active",
        )
        db.add_all([active_a, closed_a, active_b])
        db.commit()

        ids = SimpleNamespace(
            customer_a=customer_a.id,
            customer_b=customer_b.id,
            active_a=active_a.id,
            closed_a=closed_a.id,
            active_b=active_b.id,
        )

    def override_get_db() -> Generator:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    def headers(role: str) -> dict[str, str]:
        token = create_access_token(subject=str(users[role]), role=role)
        return {"Authorization": f"Bearer {token}"}

    counter = {"n": 0}

    def make_card(
        status: str = "pending",
        *,
        customer_id: int | None = None,
        account_id: int | None | str = "default",
        card_type: str = "debit",
    ) -> int:
        counter["n"] += 1
        with session_factory() as db:
            card = CardIssuanceRequest(
                request_number=f"SYN-CARD-WF-{counter['n']:04d}",
                customer_id=customer_id or ids.customer_a,
                account_id=ids.active_a if account_id == "default" else account_id,
                card_type=card_type,
                status=status,
                simulation_only=True,
            )
            db.add(card)
            db.commit()
            return card.id

    def card_status(card_id: int) -> str:
        with session_factory() as db:
            return db.get(CardIssuanceRequest, card_id).status

    def audit_for(card_id: int) -> list[BankingAuditEvent]:
        with session_factory() as db:
            return list(
                db.scalars(
                    select(BankingAuditEvent)
                    .where(
                        BankingAuditEvent.entity_type == "card_issuance_request",
                        BankingAuditEvent.entity_id == card_id,
                    )
                    .order_by(BankingAuditEvent.id)
                )
            )

    try:
        with TestClient(app) as client:
            yield SimpleNamespace(
                client=client,
                ids=ids,
                headers=headers,
                make_card=make_card,
                card_status=card_status,
                audit_for=audit_for,
                session_factory=session_factory,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def review(ctx, card_id, decision, role="reviewer", notes="ملاحظات المراجعة"):
    return ctx.client.post(
        f"{BASE}/{card_id}/review",
        headers=ctx.headers(role),
        json={"decision": decision, "notes": notes},
    )


def issue(ctx, card_id, role="reviewer"):
    return ctx.client.post(f"{BASE}/{card_id}/issue", headers=ctx.headers(role))


# ---------------------------------------------------------------- review flow


def test_pending_card_moves_to_under_review_with_audit(ctx):
    card_id = ctx.make_card()

    response = review(ctx, card_id, "under_review")

    assert response.status_code == 200
    body = response.json()
    assert body["previous_status"] == "pending"
    assert body["new_status"] == "under_review"
    assert body["actor_username"] == "card_reviewer"
    assert body["simulation_only"] is True
    assert ctx.card_status(card_id) == "under_review"

    events = ctx.audit_for(card_id)
    assert [e.action for e in events] == ["HUMAN_REVIEW"]
    assert events[0].actor_role == "reviewer"
    assert events[0].actor_username == "card_reviewer"


def test_under_review_card_with_active_own_account_can_be_approved(ctx):
    card_id = ctx.make_card("under_review")

    response = review(ctx, card_id, "approved", role="admin")

    assert response.status_code == 200
    assert response.json()["new_status"] == "approved"
    assert ctx.card_status(card_id) == "approved"


def test_under_review_card_can_be_rejected_without_account_check(ctx):
    card_id = ctx.make_card("under_review", account_id=None)

    response = review(ctx, card_id, "rejected")

    assert response.status_code == 200
    assert ctx.card_status(card_id) == "rejected"


def test_pending_card_cannot_jump_to_approved(ctx):
    card_id = ctx.make_card("pending")

    response = review(ctx, card_id, "approved")

    assert response.status_code == 409
    assert ctx.card_status(card_id) == "pending"
    assert ctx.audit_for(card_id) == []


@pytest.mark.parametrize("terminal", ["rejected", "issued", "cancelled"])
@pytest.mark.parametrize("decision", ["under_review", "approved", "rejected", "cancelled"])
def test_terminal_states_reject_every_decision(ctx, terminal, decision):
    card_id = ctx.make_card(terminal)

    response = review(ctx, card_id, decision)

    assert response.status_code == 409
    assert ctx.card_status(card_id) == terminal


def test_card_can_be_cancelled_from_pending(ctx):
    card_id = ctx.make_card("pending")

    assert review(ctx, card_id, "cancelled").status_code == 200
    assert ctx.card_status(card_id) == "cancelled"


# ------------------------------------------------------ eligibility checks


def test_approval_blocked_when_card_has_no_account(ctx):
    card_id = ctx.make_card("under_review", account_id=None)

    response = review(ctx, card_id, "approved")

    assert response.status_code == 409
    assert ctx.card_status(card_id) == "under_review"
    assert ctx.audit_for(card_id) == []


def test_approval_blocked_when_account_is_not_active(ctx):
    card_id = ctx.make_card("under_review", account_id=ctx.ids.closed_a)

    response = review(ctx, card_id, "approved")

    assert response.status_code == 409
    assert ctx.card_status(card_id) == "under_review"


def test_approval_blocked_when_account_belongs_to_another_customer(ctx):
    card_id = ctx.make_card(
        "under_review",
        customer_id=ctx.ids.customer_a,
        account_id=ctx.ids.active_b,
    )

    response = review(ctx, card_id, "approved")

    assert response.status_code == 409
    assert ctx.card_status(card_id) == "under_review"


# ---------------------------------------------------------- simulated issuance


def test_approved_card_can_be_issued_and_is_audited(ctx):
    card_id = ctx.make_card("approved")

    response = issue(ctx, card_id)

    assert response.status_code == 200
    body = response.json()
    assert body["previous_status"] == "approved"
    assert body["new_status"] == "issued"
    assert body["simulation_only"] is True
    assert ctx.card_status(card_id) == "issued"

    events = ctx.audit_for(card_id)
    assert [e.action for e in events] == ["SIMULATED_CARD_ISSUANCE"]


@pytest.mark.parametrize("status", ["pending", "under_review", "rejected", "cancelled"])
def test_issue_requires_prior_approval(ctx, status):
    card_id = ctx.make_card(status)

    response = issue(ctx, card_id)

    assert response.status_code == 409
    assert ctx.card_status(card_id) == status
    assert ctx.audit_for(card_id) == []


def test_card_cannot_be_issued_twice(ctx):
    card_id = ctx.make_card("approved")

    assert issue(ctx, card_id).status_code == 200
    assert issue(ctx, card_id).status_code == 409
    assert len(ctx.audit_for(card_id)) == 1


def test_issue_blocked_when_account_became_inactive_after_approval(ctx):
    card_id = ctx.make_card("approved")
    with ctx.session_factory() as db:
        account = db.get(BankAccount, ctx.ids.active_a)
        account.status = "closed"
        db.commit()

    response = issue(ctx, card_id)

    assert response.status_code == 409
    assert ctx.card_status(card_id) == "approved"


def test_issue_blocked_when_account_missing(ctx):
    card_id = ctx.make_card("approved", account_id=None)

    assert issue(ctx, card_id).status_code == 409
    assert ctx.card_status(card_id) == "approved"


def test_issue_does_not_reuse_notes_from_client(ctx):
    card_id = ctx.make_card("approved")

    response = ctx.client.post(
        f"{BASE}/{card_id}/issue",
        headers=ctx.headers("reviewer"),
        json={"notes": "client supplied text must be ignored"},
    )

    assert response.status_code == 200
    with ctx.session_factory() as db:
        card = db.get(CardIssuanceRequest, card_id)
        assert "client supplied" not in (card.review_notes or "")


# ----------------------------------------------------------------- security


@pytest.mark.parametrize("path_suffix", ["review", "issue"])
def test_viewer_cannot_change_cards(ctx, path_suffix):
    card_id = ctx.make_card("approved")
    kwargs = {"json": {"decision": "under_review", "notes": "viewer attempt"}}

    response = ctx.client.post(
        f"{BASE}/{card_id}/{path_suffix}",
        headers=ctx.headers("viewer"),
        **(kwargs if path_suffix == "review" else {}),
    )

    assert response.status_code == 403
    assert ctx.card_status(card_id) == "approved"


@pytest.mark.parametrize("path_suffix", ["review", "issue"])
def test_card_actions_require_authentication(ctx, path_suffix):
    card_id = ctx.make_card("approved")
    kwargs = {"json": {"decision": "under_review", "notes": "no token"}}

    response = ctx.client.post(
        f"{BASE}/{card_id}/{path_suffix}",
        **(kwargs if path_suffix == "review" else {}),
    )

    assert response.status_code == 401


def test_review_rejects_short_notes_and_extra_fields(ctx):
    card_id = ctx.make_card("pending")

    short = ctx.client.post(
        f"{BASE}/{card_id}/review",
        headers=ctx.headers("reviewer"),
        json={"decision": "under_review", "notes": "x"},
    )
    extra = ctx.client.post(
        f"{BASE}/{card_id}/review",
        headers=ctx.headers("reviewer"),
        json={
            "decision": "under_review",
            "notes": "valid notes",
            "reviewer_name": "spoofed",
        },
    )

    assert short.status_code == 422
    assert extra.status_code == 422
    assert ctx.card_status(card_id) == "pending"


def test_review_rejects_decision_that_is_not_allowed_by_schema(ctx):
    card_id = ctx.make_card("approved")

    response = review(ctx, card_id, "issued")

    assert response.status_code == 422


@pytest.mark.parametrize(
    "method,suffix",
    [("get", ""), ("post", "/review"), ("post", "/issue")],
)
def test_unknown_card_returns_404(ctx, method, suffix):
    kwargs = {"headers": ctx.headers("reviewer")}
    if suffix == "/review":
        kwargs["json"] = {"decision": "under_review", "notes": "unknown card"}

    response = getattr(ctx.client, method)(f"{BASE}/999999{suffix}", **kwargs)

    assert response.status_code == 404


# ------------------------------------------------------------ list & search


def test_list_filters_by_status_search_and_pagination(ctx):
    for status in ("pending", "pending", "approved", "issued"):
        ctx.make_card(status)
    ctx.make_card("pending", customer_id=ctx.ids.customer_b, account_id=ctx.ids.active_b)

    headers = ctx.headers("viewer")

    by_status = ctx.client.get(BASE, params={"status": "pending"}, headers=headers).json()
    assert by_status["total"] == 3

    by_search = ctx.client.get(BASE, params={"q": "SYN-CARD-CUST-B"}, headers=headers).json()
    assert by_search["total"] == 1

    page = ctx.client.get(BASE, params={"page": 2, "page_size": 2}, headers=headers).json()
    assert page["total"] == 5
    assert len(page["items"]) == 2
    assert page["page"] == 2

    too_big = ctx.client.get(BASE, params={"page_size": 101}, headers=headers)
    assert too_big.status_code == 422
