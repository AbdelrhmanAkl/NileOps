"""API tests for the policy knowledge base (versions, citations, abstention, audit)."""

import shutil
from collections.abc import Generator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.api.policies as policies_module
from src.api.main import app
from src.core.config import PROJECT_ROOT, get_settings
from src.core.security import create_access_token, hash_password
from src.db.session import Base, get_db
from src.models.banking import BankingAuditEvent
from src.models.policies import PolicyChunk, PolicyDocument
from src.models.user import User
from src.rag.llm import LlmUnavailable

BASE = "/api/v1/policies"
SHIPPED = PROJECT_ROOT / "knowledge" / "policies"
CARD_Q = "كم مدة تسليم البطاقة الجديدة بعد الاعتماد؟"
SECRET_Q = "العميل أحمد رقمه القومي 29901011234567 كم مدة تسليم بطاقته؟"


class FakeLlm:
    name = "fake"

    def __init__(self, output="", error=None):
        self.output, self.error, self.calls = output, error, 0

    def generate(self, system, user):
        self.calls += 1
        if self.error:
            raise self.error
        return self.output


@pytest.fixture
def ctx(tmp_path, monkeypatch) -> Generator[SimpleNamespace, None, None]:
    policies_dir = tmp_path / "policies"
    shutil.copytree(SHIPPED, policies_dir)
    settings = get_settings().model_copy(update={"policies_dir": str(policies_dir)})
    monkeypatch.setattr(policies_module, "get_settings", lambda: settings)
    llm_holder = SimpleNamespace(client=None)
    monkeypatch.setattr(policies_module, "get_llm_client", lambda: llm_holder.client)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        users = {}
        for role in ("viewer", "reviewer", "admin"):
            user = User(
                username=f"pol_{role}",
                password_hash=hash_password(f"{role.title()}-Password-123!"),
                role=role,
                is_active=True,
            )
            db.add(user)
            db.flush()
            users[role] = user.id
        db.commit()

    def override_get_db() -> Generator:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    def headers(role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(subject=str(users[role]), role=role)}"}

    try:
        with TestClient(app) as client:
            yield SimpleNamespace(
                client=client,
                headers=headers,
                policies_dir=policies_dir,
                session_factory=session_factory,
                llm=llm_holder,
                reload=lambda: client.post(f"{BASE}/reload", headers=headers("admin")),
                ask=lambda q=CARD_Q, role="viewer": client.post(
                    f"{BASE}/ask", headers=headers(role), json={"question": q}
                ),
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


# ------------------------------------------------------------------ access
def test_everything_requires_authentication(ctx):
    assert ctx.client.post(f"{BASE}/ask", json={"question": CARD_Q}).status_code == 401
    assert ctx.client.get(BASE).status_code == 401
    assert ctx.client.post(f"{BASE}/reload").status_code == 401


@pytest.mark.parametrize("role", ["viewer", "reviewer"])
def test_only_admin_can_reload(ctx, role):
    assert ctx.client.post(f"{BASE}/reload", headers=ctx.headers(role)).status_code == 403


@pytest.mark.parametrize("role", ["viewer", "reviewer", "admin"])
def test_every_role_can_ask_and_browse(ctx, role):
    assert ctx.reload().status_code == 200
    assert ctx.ask(role=role).status_code == 200
    assert ctx.client.get(BASE, headers=ctx.headers(role)).status_code == 200


# ------------------------------------------------------------- load/version
def test_reload_loads_all_files_and_is_idempotent(ctx):
    first = ctx.reload().json()
    assert "POL-CARD-001 v2" in first["added"] and "POL-CARD-001 v1" in first["added"]
    assert first["unchanged"] == [] and first["conflicts"] == []
    second = ctx.reload().json()
    assert second["added"] == [] and len(second["unchanged"]) == len(first["added"])


def test_only_latest_version_is_active(ctx):
    ctx.reload()
    active = ctx.client.get(BASE, headers=ctx.headers("viewer")).json()
    card = [p for p in active if p["policy_code"] == "POL-CARD-001"]
    assert [(p["version"], p["is_active"]) for p in card] == [(2, True)]
    everything = ctx.client.get(f"{BASE}?include_superseded=true", headers=ctx.headers("viewer")).json()
    assert sorted((p["version"], p["is_active"]) for p in everything if p["policy_code"] == "POL-CARD-001") == [
        (1, False),
        (2, True),
    ]


def test_answers_use_the_latest_version_not_the_superseded_one(ctx):
    ctx.reload()
    body = ctx.ask().json()
    assert body["status"] == "answered"
    assert "7 أيام" in body["answer"] and "10 أيام" not in body["answer"]
    assert {(c["policy_code"], c["version"]) for c in body["citations"]} == {("POL-CARD-001", 2)}


def test_new_version_file_supersedes_old_answers(ctx):
    ctx.reload()
    text = (ctx.policies_dir / "POL-CARD-001.md").read_text(encoding="utf-8")
    (ctx.policies_dir / "POL-CARD-001_v3.md").write_text(
        text.replace("version: 2", "version: 3").replace("7 أيام", "4 أيام"), encoding="utf-8"
    )
    assert ctx.reload().json()["added"] == ["POL-CARD-001 v3"]
    body = ctx.ask().json()
    assert "4 أيام" in body["answer"] and body["citations"][0]["version"] == 3
    old = ctx.client.get(f"{BASE}/POL-CARD-001?version=2", headers=ctx.headers("viewer")).json()
    assert any("7 أيام" in s["text"] for s in old["sections"])  # history stays readable


def test_changing_text_without_bumping_version_is_a_conflict_and_not_applied(ctx):
    ctx.reload()
    path = ctx.policies_dir / "POL-CARD-001.md"
    path.write_text(path.read_text(encoding="utf-8").replace("7 أيام", "2 أيام"), encoding="utf-8")
    report = ctx.reload().json()
    assert report["conflicts"] == ["POL-CARD-001 v2"] and report["added"] == []
    assert "7 أيام" in ctx.ask().json()["answer"]


def test_malformed_policy_file_is_rejected_without_loading_anything(ctx):
    (ctx.policies_dir / "broken.md").write_text("لا يوجد رأس بيانات\n## قسم\nنص", encoding="utf-8")
    response = ctx.reload()
    assert response.status_code == 422
    with ctx.session_factory() as db:
        assert db.scalar(select(PolicyDocument)) is None


def test_get_policy_returns_sections_and_404s(ctx):
    ctx.reload()
    headers = ctx.headers("viewer")
    body = ctx.client.get(f"{BASE}/POL-FEE-001", headers=headers).json()
    assert body["version"] == 1 and any("30 جنيهًا" in s["text"] for s in body["sections"])
    assert ctx.client.get(f"{BASE}/POL-NOPE-001", headers=headers).status_code == 404
    assert ctx.client.get(f"{BASE}/POL-FEE-001?version=9", headers=headers).status_code == 404


# ---------------------------------------------------------------------- ask
def test_ask_returns_citation_metadata(ctx):
    ctx.reload()
    body = ctx.ask().json()
    assert body["status"] == "answered" and body["mode"] == "extractive"
    first = body["citations"][0]
    assert {"number", "policy_code", "version", "title", "heading", "excerpt", "chunk_id", "score"} <= set(first)
    assert body["advisory"]


def test_out_of_scope_question_abstains_with_no_citations(ctx):
    ctx.reload()
    body = ctx.ask("ما هي أسعار الذهب اليوم؟").json()
    assert body["status"] == "abstained" and body["citations"] == []


def test_ask_before_any_policy_is_loaded_abstains(ctx):
    body = ctx.ask().json()
    assert body["status"] == "abstained"


def test_chunk_id_points_to_a_real_passage(ctx):
    ctx.reload()
    cited = ctx.ask().json()["citations"][0]
    with ctx.session_factory() as db:
        chunk = db.get(PolicyChunk, cited["chunk_id"])
        assert chunk is not None and chunk.text == cited["excerpt"]


@pytest.mark.parametrize("payload", [{}, {"question": "قصير"}, {"question": "س" * 501}, {"question": CARD_Q, "x": 1}])
def test_ask_validates_input(ctx, payload):
    assert ctx.client.post(f"{BASE}/ask", headers=ctx.headers("viewer"), json=payload).status_code == 422


def test_query_alias_from_the_api_document_is_accepted(ctx):
    ctx.reload()
    response = ctx.client.post(f"{BASE}/ask", headers=ctx.headers("viewer"), json={"query": CARD_Q})
    assert response.status_code == 200 and response.json()["status"] == "answered"


def test_local_llm_answer_is_used_when_valid(ctx):
    ctx.reload()
    ctx.llm.client = FakeLlm("تُسلَّم البطاقة خلال 7 أيام عمل [1].")
    body = ctx.ask().json()
    assert body["mode"] == "llm" and "7 أيام" in body["answer"]


def test_invalid_llm_answer_falls_back_to_policy_text(ctx):
    ctx.reload()
    ctx.llm.client = FakeLlm("تُسلَّم البطاقة خلال 30 يومًا [1].")
    body = ctx.ask().json()
    assert body["mode"] == "extractive" and "7 أيام" in body["answer"] and body["note"]


def test_non_local_llm_url_is_a_clear_503_not_a_crash(ctx, monkeypatch):
    def refuse():
        raise ValueError("external")

    monkeypatch.setattr(policies_module, "get_llm_client", refuse)
    assert ctx.ask().status_code == 503


def test_llm_down_never_breaks_the_endpoint(ctx):
    ctx.reload()
    ctx.llm.client = FakeLlm(error=LlmUnavailable("down"))
    response = ctx.ask()
    assert response.status_code == 200 and response.json()["mode"] == "extractive"


def test_llm_is_not_called_for_out_of_scope_questions(ctx):
    ctx.reload()
    ctx.llm.client = FakeLlm("x [1]")
    ctx.ask("ما هي أسعار الذهب اليوم؟")
    assert ctx.llm.client.calls == 0


# -------------------------------------------------------------------- audit
def test_audit_records_outcome_but_never_the_question(ctx):
    ctx.reload()
    ctx.ask(SECRET_Q)
    ctx.ask(CARD_Q)
    ctx.ask("ما هي أسعار الذهب اليوم؟")
    with ctx.session_factory() as db:
        events = db.scalars(
            select(BankingAuditEvent).where(BankingAuditEvent.entity_type == "policy_query")
        ).all()
    actions = [e.action for e in events]
    assert actions.count("POLICY_QUESTION") == 3 and "POLICY_RELOAD" in actions
    blob = " ".join(e.details_ar for e in events)
    assert "29901011234567" not in blob and "أحمد" not in blob and "الذهب" not in blob
    assert any("النتيجة=answered" in e.details_ar for e in events)
    assert any("النتيجة=abstained" in e.details_ar for e in events)
    assert {e.actor_username for e in events if e.action == "POLICY_QUESTION"} == {"pol_viewer"}
