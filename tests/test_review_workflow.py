
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.session import Base, get_db
from src.models.kyc import (
    ApplicationStatus,
    AuditEvent,
    KYCApplication,
    KYCDocument,
    DocumentStatus,
)


@pytest.fixture
def review_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as db:
        application = KYCApplication(
            application_number="KYC-TEST-00001",
            customer_name="عميل تجريبي",
            status=ApplicationStatus.PENDING.value,
        )
        db.add(application)
        db.flush()

        for document_type in (
            "بطاقة الرقم القومي",
            "إثبات العنوان",
            "استمارة بيانات العميل",
        ):
            db.add(
                KYCDocument(
                    application_id=application.id,
                    document_type=document_type,
                    status=DocumentStatus.MISSING.value,
                )
            )

        db.commit()
        application_id = application.id

    def override_get_db():
        with TestingSessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, TestingSessionLocal, application_id

    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_human_review_updates_status_and_audit(review_client):
    client, session_factory, application_id = review_client

    response = client.post(
        f"/api/v1/kyc/applications/{application_id}/review",
        json={
            "decision": "IN_REVIEW",
            "reviewer_name": "Reviewer Test",
            "notes": "بدأت مراجعة المستندات.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["new_status"] == ApplicationStatus.IN_REVIEW.value
    assert body["reviewer_name"] == "Reviewer Test"

    with session_factory() as db:
        application = db.get(KYCApplication, application_id)
        events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.application_id == application_id
            )
        ).all()

        assert application.status == ApplicationStatus.IN_REVIEW.value
        assert len(events) == 1
        assert events[0].actor_type == "human"
        assert events[0].action == "HUMAN_REVIEW"


def test_invalid_transition_is_rejected(review_client):
    client, session_factory, application_id = review_client

    response = client.post(
        f"/api/v1/kyc/applications/{application_id}/review",
        json={
            "decision": "APPROVED",
            "reviewer_name": "Reviewer Test",
            "notes": "محاولة اعتماد قبل بدء المراجعة.",
        },
    )

    assert response.status_code == 409

    with session_factory() as db:
        application = db.get(KYCApplication, application_id)
        events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.application_id == application_id
            )
        ).all()

        assert application.status == ApplicationStatus.PENDING.value
        assert events == []


def test_approval_requires_all_documents_to_be_valid(review_client):
    client, session_factory, application_id = review_client

    with session_factory() as db:
        application = db.get(KYCApplication, application_id)
        application.status = ApplicationStatus.IN_REVIEW.value
        db.commit()

    response = client.post(
        f"/api/v1/kyc/applications/{application_id}/review",
        json={
            "decision": "APPROVED",
            "reviewer_name": "Reviewer Test",
            "notes": "تمت مراجعة الطلب.",
        },
    )

    assert response.status_code == 409

    with session_factory() as db:
        application = db.get(KYCApplication, application_id)
        assert application.status == ApplicationStatus.IN_REVIEW.value


def test_review_unknown_application_returns_404(review_client):
    client, _, _ = review_client

    response = client.post(
        "/api/v1/kyc/applications/999999/review",
        json={
            "decision": "IN_REVIEW",
            "reviewer_name": "Reviewer Test",
            "notes": "مراجعة تجريبية.",
        },
    )

    assert response.status_code == 404



def test_approval_succeeds_when_all_required_documents_are_valid(
    review_client,
):
    client, session_factory, application_id = review_client

    with session_factory() as db:
        application = db.get(KYCApplication, application_id)
        application.status = ApplicationStatus.IN_REVIEW.value

        documents = db.scalars(
            select(KYCDocument).where(
                KYCDocument.application_id == application_id
            )
        ).all()

        for document in documents:
            document.status = DocumentStatus.VALID.value

        db.commit()

    response = client.post(
        f"/api/v1/kyc/applications/{application_id}/review",
        json={
            "decision": "APPROVED",
            "reviewer_name": "Reviewer Test",
            "notes": "تمت مراجعة المستندات المطلوبة.",
        },
    )

    assert response.status_code == 200
    assert response.json()["new_status"] == ApplicationStatus.APPROVED.value

    with session_factory() as db:
        application = db.get(KYCApplication, application_id)
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.application_id == application_id,
                AuditEvent.action == "HUMAN_REVIEW",
            )
        )

        assert application.status == ApplicationStatus.APPROVED.value
        assert event is not None
        assert event.previous_status == ApplicationStatus.IN_REVIEW.value
        assert event.new_status == ApplicationStatus.APPROVED.value
