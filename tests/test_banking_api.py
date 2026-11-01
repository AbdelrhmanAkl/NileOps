
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.core.security import create_access_token, hash_password
from src.db.session import Base, get_db
from src.models.banking import (
    AccountOpeningRequest,
    BankAccount,
    BankingAuditEvent,
    CardIssuanceRequest,
    Customer,
)
from src.models.user import User


@pytest.fixture
def banking_api_client() -> Generator:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    Base.metadata.create_all(bind=engine)

    with testing_session_local() as db:
        viewer = User(
            username="banking_viewer",
            password_hash=hash_password("Viewer-Test-Password-123!"),
            role="viewer",
            is_active=True,
        )
        reviewer = User(
            username="banking_reviewer",
            password_hash=hash_password("Reviewer-Test-Password-123!"),
            role="reviewer",
            is_active=True,
        )

        db.add_all([viewer, reviewer])
        db.flush()

        customer = Customer(
            customer_ref="SYN-TEST-CUSTOMER-001",
            full_name_ar="عميل اختبار",
            email="banking-test@example.invalid",
            phone_last_four="1234",
            branch_name="فرع الاختبار",
        )
        db.add(customer)
        db.flush()

        account_request = AccountOpeningRequest(
            request_number="SYN-TEST-ACCOUNT-001",
            customer_id=customer.id,
            account_type="savings",
            status="pending",
            branch_name="فرع الاختبار",
            simulation_only=True,
        )
        db.add(account_request)
        db.flush()

        bank_account = BankAccount(
            account_ref="SYN-TEST-ACCOUNT-REF-001",
            customer_id=customer.id,
            account_type="savings",
            status="active",
            currency="EGP",
            simulation_only=True,
        )
        db.add(bank_account)
        db.flush()

        card_request = CardIssuanceRequest(
            request_number="SYN-TEST-CARD-001",
            customer_id=customer.id,
            account_id=bank_account.id,
            card_type="debit",
            status="pending",
            simulation_only=True,
        )
        db.add(card_request)
        db.commit()

        viewer_id = viewer.id
        reviewer_id = reviewer.id
        request_id = account_request.id

    def override_get_db() -> Generator:
        with testing_session_local() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    viewer_token = create_access_token(
        subject=str(viewer_id),
        role="viewer",
    )
    reviewer_token = create_access_token(
        subject=str(reviewer_id),
        role="reviewer",
    )

    viewer_headers = {
        "Authorization": f"Bearer {viewer_token}"
    }
    reviewer_headers = {
        "Authorization": f"Bearer {reviewer_token}"
    }

    try:
        with TestClient(app) as client:
            yield (
                client,
                viewer_headers,
                reviewer_headers,
                request_id,
                testing_session_local,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_list_account_opening_requests(banking_api_client):
    client, viewer_headers, _, _, _ = banking_api_client

    response = client.get(
        "/api/v1/banking/account-opening",
        headers=viewer_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["request_number"] == (
        "SYN-TEST-ACCOUNT-001"
    )
    assert body["items"][0]["simulation_only"] is True


def test_get_account_opening_request(banking_api_client):
    client, viewer_headers, _, request_id, _ = banking_api_client

    response = client.get(
        f"/api/v1/banking/account-opening/{request_id}",
        headers=viewer_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_number"] == "SYN-TEST-ACCOUNT-001"
    assert body["customer_ref"] == "SYN-TEST-CUSTOMER-001"
    assert body["customer_name"] == "عميل اختبار"


def test_unknown_account_opening_request_returns_404(
    banking_api_client,
):
    client, viewer_headers, _, _, _ = banking_api_client

    response = client.get(
        "/api/v1/banking/account-opening/999999",
        headers=viewer_headers,
    )

    assert response.status_code == 404


def test_viewer_cannot_review_account_opening_request(
    banking_api_client,
):
    client, viewer_headers, _, request_id, _ = banking_api_client

    response = client.post(
        f"/api/v1/banking/account-opening/{request_id}/review",
        headers=viewer_headers,
        json={
            "decision": "under_review",
            "notes": "Permission test for viewer.",
        },
    )

    assert response.status_code == 403


def test_reviewer_can_move_request_to_under_review(
    banking_api_client,
):
    (
        client,
        _,
        reviewer_headers,
        request_id,
        testing_session_local,
    ) = banking_api_client

    response = client.post(
        f"/api/v1/banking/account-opening/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "under_review",
            "notes": "تم بدء المراجعة البشرية للاختبار.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["previous_status"] == "pending"
    assert body["new_status"] == "under_review"
    assert body["simulation_only"] is True
    assert body["reviewer_name"] == "banking_reviewer"

    with testing_session_local() as db:
        audit_event = db.scalar(
            select(BankingAuditEvent).where(
                BankingAuditEvent.id == body["audit_event_id"]
            )
        )

        assert audit_event is not None
        assert audit_event.action == "HUMAN_REVIEW"
        assert audit_event.actor_role == "reviewer"
        assert audit_event.actor_username == "banking_reviewer"


def test_account_opening_review_requires_authentication(
    banking_api_client,
):
    client, _, _, request_id, _ = banking_api_client

    response = client.post(
        f"/api/v1/banking/account-opening/{request_id}/review",
        json={
            "decision": "under_review",
            "notes": "Authentication test.",
        },
    )

    assert response.status_code == 401


def test_invalid_review_decision_returns_422(banking_api_client):
    client, _, reviewer_headers, request_id, _ = banking_api_client

    response = client.post(
        f"/api/v1/banking/account-opening/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "invalid_decision",
            "notes": "Invalid decision test.",
        },
    )

    assert response.status_code == 422


def test_invalid_status_transition_returns_409(banking_api_client):
    client, _, reviewer_headers, request_id, _ = banking_api_client

    response = client.post(
        f"/api/v1/banking/account-opening/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approved",
            "notes": "Approval from pending status is not allowed.",
        },
    )

    assert response.status_code == 409


def test_list_card_issuance_requests(banking_api_client):
    client, viewer_headers, _, _, _ = banking_api_client

    response = client.get(
        "/api/v1/banking/card-issuance",
        headers=viewer_headers,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["request_number"] == (
        "SYN-TEST-CARD-001"
    )
    assert body["items"][0]["status"] == "pending"
    assert body["items"][0]["simulation_only"] is True


def test_get_card_issuance_request(banking_api_client):
    client, viewer_headers, _, _, testing_session_local = (
        banking_api_client
    )

    with testing_session_local() as db:
        card_request = db.scalar(
            select(CardIssuanceRequest).where(
                CardIssuanceRequest.request_number
                == "SYN-TEST-CARD-001"
            )
        )
        card_request_id = card_request.id

    response = client.get(
        f"/api/v1/banking/card-issuance/{card_request_id}",
        headers=viewer_headers,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["request_number"] == "SYN-TEST-CARD-001"
    assert body["customer_name"] == "عميل اختبار"
    assert body["customer_ref"] == "SYN-TEST-CUSTOMER-001"
    assert body["account_ref"] == "SYN-TEST-ACCOUNT-REF-001"
    assert body["account_status"] == "active"
    assert body["simulation_only"] is True


def test_viewer_cannot_review_card_issuance_request(
    banking_api_client,
):
    (
        client,
        viewer_headers,
        _,
        _,
        testing_session_local,
    ) = banking_api_client

    with testing_session_local() as db:
        card_request = db.scalar(
            select(CardIssuanceRequest).where(
                CardIssuanceRequest.request_number
                == "SYN-TEST-CARD-001"
            )
        )
        card_request_id = card_request.id

    response = client.post(
        f"/api/v1/banking/card-issuance/{card_request_id}/review",
        headers=viewer_headers,
        json={
            "decision": "under_review",
            "notes": "Viewer permission test.",
        },
    )

    assert response.status_code == 403