from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.core.security import create_access_token, hash_password
from src.db.session import Base, get_db
from src.models.kyc import (
    ApplicationStatus,
    AuditEvent,
    DocumentStatus,
    KYCApplication,
    KYCDocument,
    ValidationResult,
    ValidationSeverity,
)
from src.models.user import User


@pytest.fixture
def api_client() -> Generator:
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
            username="viewer_test",
            password_hash=hash_password("Test-Password-123!"),
            role="viewer",
            is_active=True,
        )
        db.add(viewer)

        application = KYCApplication(
            application_number="KYC-2026-00001",
            customer_name="Test Customer",
            national_id_last_four="1234",
            status=ApplicationStatus.PENDING.value,
        )
        db.add(application)
        db.flush()

        for document_type in (
            "National ID",
            "Proof of Address",
            "Customer Information Form",
        ):
            db.add(
                KYCDocument(
                    application_id=application.id,
                    document_type=document_type,
                    status=DocumentStatus.MISSING.value,
                )
            )

        for index in range(3):
            db.add(
                ValidationResult(
                    application_id=application.id,
                    rule_code=f"TEST_RULE_{index + 1}",
                    title_ar=f"Test validation {index + 1}",
                    details_ar="Synthetic test result.",
                    severity=ValidationSeverity.INFO.value,
                    passed=True,
                )
            )

        db.add(
            AuditEvent(
                application_id=application.id,
                action="APPLICATION_CREATED",
                actor_type="system",
                actor_name="test_setup",
                details_ar="Synthetic test application created.",
                previous_status=None,
                new_status=ApplicationStatus.PENDING.value,
            )
        )

        db.commit()
        viewer_id = viewer.id
        application_id = application.id

    def override_get_db() -> Generator:
        with testing_session_local() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    token = create_access_token(
        subject=str(viewer_id),
        role="viewer",
    )
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        yield client, headers, application_id

    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_health_check(api_client):
    client, _, _ = api_client

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["database"] == "connected"


def test_list_applications(api_client):
    client, headers, _ = api_client

    response = client.get(
        "/api/v1/kyc/applications",
        headers=headers,
        params={"page": 1, "page_size": 20},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert isinstance(body["items"], list)
    assert len(body["items"]) == 1


def test_search_by_application_number(api_client):
    client, headers, _ = api_client

    response = client.get(
        "/api/v1/kyc/applications",
        headers=headers,
        params={"q": "KYC-2026-00001"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["application_number"] == "KYC-2026-00001"


def test_search_with_no_matches_returns_empty_list(api_client):
    client, headers, _ = api_client

    response = client.get(
        "/api/v1/kyc/applications",
        headers=headers,
        params={"q": "NO-SUCH-APPLICATION"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_pagination(api_client):
    client, headers, _ = api_client

    response = client.get(
        "/api/v1/kyc/applications",
        headers=headers,
        params={"page": 2, "page_size": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 2
    assert body["page_size"] == 10
    assert body["items"] == []


def test_application_detail(api_client):
    client, headers, application_id = api_client

    response = client.get(
        f"/api/v1/kyc/applications/{application_id}",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == application_id
    assert body["application_number"] == "KYC-2026-00001"
    assert len(body["documents"]) == 3
    assert len(body["validation_results"]) == 3
    assert len(body["audit_events"]) == 1
    assert body["national_id_last_four"] == "1234"


def test_unknown_application_returns_404(api_client):
    client, headers, _ = api_client

    response = client.get(
        "/api/v1/kyc/applications/999999999",
        headers=headers,
    )

    assert response.status_code == 404


def test_invalid_page_returns_422(api_client):
    client, headers, _ = api_client

    response = client.get(
        "/api/v1/kyc/applications",
        headers=headers,
        params={"page": 0},
    )

    assert response.status_code == 422


def test_invalid_page_size_returns_422(api_client):
    client, headers, _ = api_client

    response = client.get(
        "/api/v1/kyc/applications",
        headers=headers,
        params={"page_size": 101},
    )

    assert response.status_code == 422


def test_application_detail_does_not_expose_full_national_id(api_client):
    client, headers, application_id = api_client

    response = client.get(
        f"/api/v1/kyc/applications/{application_id}",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert "national_id" not in body
    assert body["national_id_last_four"] == "1234"


def test_application_list_requires_authentication(api_client):
    client, _, _ = api_client

    response = client.get(
        "/api/v1/kyc/applications",
        params={"page": 1, "page_size": 20},
    )

    assert response.status_code == 401


def test_application_detail_requires_authentication(api_client):
    client, _, application_id = api_client

    response = client.get(
        f"/api/v1/kyc/applications/{application_id}",
    )

    assert response.status_code == 401


def test_invalid_bearer_token_is_rejected(api_client):
    client, _, _ = api_client

    response = client.get(
        "/api/v1/kyc/applications",
        headers={"Authorization": "Bearer invalid-token"},
        params={"page": 1, "page_size": 20},
    )

    assert response.status_code == 401


def test_viewer_cannot_submit_human_review(api_client):
    client, headers, application_id = api_client

    response = client.post(
        f"/api/v1/kyc/applications/{application_id}/review",
        headers=headers,
        json={
            "decision": "IN_REVIEW",
            "notes": "Viewer permission test.",
        },
    )

    assert response.status_code == 403
