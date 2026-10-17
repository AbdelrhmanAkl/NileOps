
from fastapi.testclient import TestClient

from src.api.main import app


client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["database"] == "connected"


def test_list_applications() -> None:
    response = client.get(
        "/api/v1/kyc/applications",
        params={"page": 1, "page_size": 20},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 0
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert isinstance(body["items"], list)
    assert len(body["items"]) <= 20


def test_search_by_application_number() -> None:
    response = client.get(
        "/api/v1/kyc/applications",
        params={"q": "KYC-2026-00001"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["application_number"] == "KYC-2026-00001"


def test_pagination() -> None:
    response = client.get(
        "/api/v1/kyc/applications",
        params={"page": 2, "page_size": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 2
    assert body["page_size"] == 10
    assert len(body["items"]) <= 10


def test_application_detail() -> None:
    response = client.get("/api/v1/kyc/applications/1")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1
    assert body["application_number"] == "KYC-2026-00001"
    assert len(body["documents"]) == 3
    assert len(body["validation_results"]) == 3
    assert len(body["audit_events"]) >= 1
    assert "national_id_last_four" in body


def test_unknown_application_returns_404() -> None:
    response = client.get("/api/v1/kyc/applications/999999999")

    assert response.status_code == 404
    assert response.json()["detail"] == "طلب العميل غير موجود."


def test_invalid_page_returns_422() -> None:
    response = client.get(
        "/api/v1/kyc/applications",
        params={"page": 0},
    )

    assert response.status_code == 422


def test_invalid_page_size_returns_422() -> None:
    response = client.get(
        "/api/v1/kyc/applications",
        params={"page_size": 101},
    )

    assert response.status_code == 422


def test_application_detail_does_not_expose_full_national_id() -> None:
    response = client.get("/api/v1/kyc/applications/1")

    assert response.status_code == 200
    body = response.json()
    assert "national_id" not in body
    assert "national_id_last_four" in body
