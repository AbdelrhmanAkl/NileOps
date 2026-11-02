"""API tests for secure upload, OCR extraction and human document review."""

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
from src.api.main import app
from src.core.config import get_settings
from src.core.security import create_access_token, hash_password
from src.db.session import Base, get_db
from src.models.documents import DocumentFile, ExtractedField
from src.models.kyc import AuditEvent, DocumentStatus, KYCApplication, KYCDocument
from src.models.user import User
from src.ocr.engine import OcrFailed, OcrUnavailable
from tests.ocr_helpers import ID_CARD_LINES, make_ocr

BASE = "/api/v1/kyc/documents"


def png_bytes(color="white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (220, 140), color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeEngine:
    name = "fake"

    def __init__(self, lines=ID_CARD_LINES, error: Exception | None = None):
        self.lines = lines
        self.error = error
        self.calls = 0

    def recognize(self, path, content_type):
        self.calls += 1
        assert path.is_file()
        if self.error:
            raise self.error
        return make_ocr(self.lines)


@pytest.fixture
def ctx(tmp_path, monkeypatch) -> Generator[SimpleNamespace, None, None]:
    settings = get_settings().model_copy(update={"upload_dir": str(tmp_path), "max_upload_size_mb": 1})
    monkeypatch.setattr(documents_module, "get_settings", lambda: settings)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)

    with session_factory() as db:
        users = {}
        for role in ("viewer", "reviewer", "admin"):
            user = User(
                username=f"doc_{role}",
                password_hash=hash_password(f"{role.title()}-Password-123!"),
                role=role,
                is_active=True,
            )
            db.add(user)
            db.flush()
            users[role] = user.id
        application = KYCApplication(
            application_number="KYC-TEST-0001",
            customer_name="أحمد محمود",
            national_id_last_four="4567",
        )
        db.add(application)
        db.flush()
        doc = KYCDocument(
            application_id=application.id,
            document_type="بطاقة الرقم القومي",
            status=DocumentStatus.MISSING.value,
        )
        db.add(doc)
        db.commit()
        document_id, application_id = doc.id, application.id

    fake = FakeEngine()
    app.dependency_overrides[get_db] = lambda: (yield from _session(session_factory))
    app.dependency_overrides[documents_module.get_ocr_engine] = lambda: fake

    def headers(role):
        token = create_access_token(subject=str(users[role]), role=role)
        return {"Authorization": f"Bearer {token}"}

    def upload(data=None, role="reviewer", name="id.png", doc_id=None):
        return client.post(
            f"{BASE}/{doc_id or document_id}/files",
            headers=headers(role) if role else {},
            files={"file": (name, data if data is not None else png_bytes(), "image/png")},
        )

    def doc_status():
        with session_factory() as db:
            return db.get(KYCDocument, document_id).status

    def audit_rows():
        with session_factory() as db:
            return list(db.scalars(select(AuditEvent).order_by(AuditEvent.id)))

    try:
        with TestClient(app) as client:
            yield SimpleNamespace(
                client=client, headers=headers, upload=upload, doc_status=doc_status,
                audit_rows=audit_rows, document_id=document_id, application_id=application_id,
                engine=fake, tmp=tmp_path, session_factory=session_factory,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(documents_module.get_ocr_engine, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _session(session_factory):
    with session_factory() as db:
        yield db


def extract(ctx, file_id, role="reviewer"):
    return ctx.client.post(f"{BASE}/{ctx.document_id}/files/{file_id}/extract", headers=ctx.headers(role))


def review(ctx, decision, role="reviewer", notes="تمت المراجعة البشرية"):
    return ctx.client.post(
        f"{BASE}/{ctx.document_id}/review", headers=ctx.headers(role), json={"decision": decision, "notes": notes}
    )


# --------------------------------------------------------------------- upload
def test_upload_stores_file_updates_status_and_audits(ctx):
    response = ctx.upload(name="../../my id.png")

    assert response.status_code == 201
    body = response.json()
    assert body["document_status"] == DocumentStatus.UPLOADED.value
    assert body["file"]["original_name"] == "my id.png"
    assert body["file"]["uploaded_by"] == "doc_reviewer"
    assert body["file"]["ocr_status"] == "not_run"
    stored = list((ctx.tmp / "documents" / str(ctx.application_id)).glob("*.png"))
    assert len(stored) == 1 and "id" not in stored[0].name
    assert ctx.doc_status() == DocumentStatus.UPLOADED.value
    assert [a.action for a in ctx.audit_rows()] == ["DOCUMENT_UPLOADED"]


def test_upload_rejects_wrong_content_even_with_image_name(ctx):
    response = ctx.upload(b"<html><script>alert(1)</script></html>", name="photo.png")

    assert response.status_code == 415
    assert ctx.doc_status() == DocumentStatus.MISSING.value
    assert not list(ctx.tmp.rglob("*.png"))


def test_upload_rejects_oversized_empty_and_corrupt_files(ctx):
    assert ctx.upload(b"\x89PNG\r\n\x1a\n" + b"0" * (1024 * 1024 + 10)).status_code == 413
    assert ctx.upload(b"").status_code == 422
    assert ctx.upload(png_bytes()[:60]).status_code == 422
    assert ctx.doc_status() == DocumentStatus.MISSING.value
    assert not list(ctx.tmp.rglob("*.png"))


def test_duplicate_file_is_rejected_and_not_left_on_disk(ctx):
    data = png_bytes()
    assert ctx.upload(data).status_code == 201

    duplicate = ctx.upload(data)

    assert duplicate.status_code == 409
    assert len(list(ctx.tmp.rglob("*.png"))) == 1


def test_upload_permissions_and_unknown_document(ctx):
    assert ctx.upload(role="viewer").status_code == 403
    assert ctx.upload(role=None).status_code == 401
    assert ctx.upload(doc_id=999999).status_code == 404


def test_upload_blocked_after_document_was_approved(ctx):
    ctx.upload()
    assert review(ctx, "VALID").status_code == 200

    assert ctx.upload(png_bytes("black")).status_code == 409


def test_list_files_requires_known_document(ctx):
    ctx.upload()
    ok = ctx.client.get(f"{BASE}/{ctx.document_id}/files", headers=ctx.headers("viewer"))
    missing = ctx.client.get(f"{BASE}/999999/files", headers=ctx.headers("viewer"))

    assert ok.status_code == 200 and len(ok.json()) == 1
    assert missing.status_code == 404


# ---------------------------------------------------------------- extraction
def test_extract_returns_fields_checks_and_never_logs_values(ctx):
    file_id = ctx.upload().json()["file"]["id"]

    response = extract(ctx, file_id)

    assert response.status_code == 200
    body = response.json()
    fields = {f["field_name"]: f for f in body["fields"]}
    assert fields["national_id"]["value"] == "29801011234567"
    assert fields["national_id"]["source"]["page"] == 1
    assert body["derived"]["birth_date"] == "1998-01-01"
    assert body["requires_human_review"] is True
    assert "مساعدة فقط" in body["ai_note"]
    checks = {c["code"]: c for c in body["checks"]}
    assert checks["NAME_MATCH"]["passed"] is True
    assert checks["ID_LAST4_MATCH"]["passed"] is True
    assert ctx.doc_status() == DocumentStatus.UNDER_REVIEW.value

    for row in ctx.audit_rows():
        assert "29801011234567" not in row.details_ar and "أحمد محمود علي" not in row.details_ar
    with ctx.session_factory() as db:
        assert db.get(KYCDocument, ctx.document_id).extracted_text.startswith("بطاقة")
        assert db.get(DocumentFile, file_id).ocr_status == "completed"


def test_extract_twice_replaces_fields_instead_of_duplicating(ctx):
    file_id = ctx.upload().json()["file"]["id"]

    extract(ctx, file_id)
    extract(ctx, file_id)

    with ctx.session_factory() as db:
        assert len(db.scalars(select(ExtractedField)).all()) == 4


def test_low_quality_scan_is_marked_needs_review(ctx):
    ctx.engine.lines = [("الاسم: أحمد محمود علي", 0.4), ("الرقم القومي: 29801011234567", 0.5)]
    file_id = ctx.upload().json()["file"]["id"]

    body = extract(ctx, file_id).json()

    assert set(body["missing_fields"]) == {"address", "id_expiry_date"}
    assert all(f["needs_review"] for f in body["fields"])
    with ctx.session_factory() as db:
        assert db.get(DocumentFile, file_id).ocr_status == "needs_review"


@pytest.mark.parametrize(
    "error,status,ocr_status",
    [(OcrUnavailable("Tesseract غير مثبت"), 503, "unavailable"), (OcrFailed("فشلت القراءة"), 502, "failed")],
)
def test_ocr_errors_are_reported_without_changing_the_document(ctx, error, status, ocr_status):
    ctx.engine.error = error
    file_id = ctx.upload().json()["file"]["id"]

    response = extract(ctx, file_id)

    assert response.status_code == status
    assert ctx.doc_status() == DocumentStatus.UPLOADED.value
    with ctx.session_factory() as db:
        assert db.get(DocumentFile, file_id).ocr_status == ocr_status
    fields = ctx.client.get(f"{BASE}/{ctx.document_id}/files/{file_id}/fields", headers=ctx.headers("viewer"))
    assert fields.status_code == 409


def test_extract_permissions_and_unknown_ids(ctx):
    file_id = ctx.upload().json()["file"]["id"]

    assert extract(ctx, file_id, role="viewer").status_code == 403
    assert extract(ctx, 999999).status_code == 404
    other = ctx.client.post(f"{BASE}/999999/files/{file_id}/extract", headers=ctx.headers("reviewer"))
    assert other.status_code == 404
    assert ctx.engine.calls == 0


def test_get_fields_after_extraction_matches_and_is_readable_by_viewer(ctx):
    file_id = ctx.upload().json()["file"]["id"]
    extract(ctx, file_id)

    response = ctx.client.get(f"{BASE}/{ctx.document_id}/files/{file_id}/fields", headers=ctx.headers("viewer"))

    assert response.status_code == 200
    assert len(response.json()["fields"]) == 4
    before = ctx.client.get(f"{BASE}/{ctx.document_id}/files/999999/fields", headers=ctx.headers("viewer"))
    assert before.status_code == 404


def test_extract_fails_cleanly_when_file_was_removed_from_storage(ctx):
    file_id = ctx.upload().json()["file"]["id"]
    for path in ctx.tmp.rglob("*.png"):
        path.unlink()

    assert extract(ctx, file_id).status_code == 404


# ------------------------------------------------------------- human review
def test_valid_requires_an_uploaded_file(ctx):
    assert review(ctx, "VALID").status_code == 409  # still MISSING

    ctx.upload()
    ok = review(ctx, "VALID")

    assert ok.status_code == 200
    assert ok.json()["new_status"] == DocumentStatus.VALID.value
    assert ok.json()["reviewer_name"] == "doc_reviewer"
    assert ctx.audit_rows()[-1].action == "DOCUMENT_HUMAN_REVIEW"


def test_reviewer_can_mark_document_invalid_or_expired(ctx):
    ctx.upload()

    assert review(ctx, "INVALID").json()["new_status"] == DocumentStatus.INVALID.value
    assert review(ctx, "EXPIRED").json()["new_status"] == DocumentStatus.EXPIRED.value


def test_review_permissions_and_validation(ctx):
    ctx.upload()

    assert review(ctx, "VALID", role="viewer").status_code == 403
    assert ctx.client.post(f"{BASE}/{ctx.document_id}/review", json={"decision": "VALID", "notes": "x y z"}).status_code == 401
    assert review(ctx, "APPROVED").status_code == 422
    assert review(ctx, "VALID", notes="x").status_code == 422
    extra = ctx.client.post(
        f"{BASE}/{ctx.document_id}/review",
        headers=ctx.headers("reviewer"),
        json={"decision": "VALID", "notes": "ملاحظات", "reviewer_name": "spoof"},
    )
    assert extra.status_code == 422
    assert review(ctx, "VALID").status_code == 200
    unknown = ctx.client.post(
        f"{BASE}/999999/review", headers=ctx.headers("reviewer"), json={"decision": "VALID", "notes": "ملاحظات"}
    )
    assert unknown.status_code == 404
