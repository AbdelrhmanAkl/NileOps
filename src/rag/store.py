"""Load policy files into the database, keeping every version."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.policies import PolicyChunk, PolicyDocument
from src.rag.chunking import ParsedPolicy
from src.rag.retrieval import IndexedChunk, PolicyIndex


@dataclass
class LoadReport:
    added: list[str]
    unchanged: list[str]
    conflicts: list[str]


def policy_label(code: str, version: int) -> str:
    return f"{code} v{version}"


def load_policies(db: Session, policies: list[ParsedPolicy]) -> LoadReport:
    """Insert new policy versions. An existing (code, version) is never overwritten.

    Re-loading identical content is a no-op; different content under the same
    version is reported as a conflict (publish a new version instead).
    """
    report = LoadReport(added=[], unchanged=[], conflicts=[])
    for parsed in policies:
        label = policy_label(parsed.policy_code, parsed.version)
        existing = db.scalar(
            select(PolicyDocument).where(
                PolicyDocument.policy_code == parsed.policy_code,
                PolicyDocument.version == parsed.version,
            )
        )
        if existing is not None:
            (report.unchanged if existing.source_sha256 == parsed.sha256 else report.conflicts).append(label)
            continue
        document = PolicyDocument(
            policy_code=parsed.policy_code,
            version=parsed.version,
            title=parsed.title,
            department=parsed.department,
            effective_date=parsed.effective_date,
            is_active=False,
            source_sha256=parsed.sha256,
        )
        db.add(document)
        db.flush()
        for position, chunk in enumerate(parsed.chunks):
            db.add(PolicyChunk(policy_id=document.id, position=position, heading=chunk.heading, text=chunk.text))
        report.added.append(label)
    db.flush()
    refresh_active_versions(db)
    return report


def refresh_active_versions(db: Session) -> None:
    """Only the highest version of each policy is active (answers use it)."""
    documents = db.scalars(select(PolicyDocument)).all()
    latest: dict[str, int] = {}
    for doc in documents:
        latest[doc.policy_code] = max(latest.get(doc.policy_code, 0), doc.version)
    for doc in documents:
        doc.is_active = doc.version == latest[doc.policy_code]
    db.flush()


def build_index(db: Session, *, include_superseded: bool = False) -> PolicyIndex:
    statement = (
        select(PolicyChunk, PolicyDocument)
        .join(PolicyDocument, PolicyDocument.id == PolicyChunk.policy_id)
        .order_by(PolicyDocument.policy_code, PolicyDocument.version, PolicyChunk.position)
    )
    if not include_superseded:
        statement = statement.where(PolicyDocument.is_active.is_(True))
    chunks = [
        IndexedChunk(
            chunk_id=chunk.id,
            policy_code=doc.policy_code,
            version=doc.version,
            title=doc.title,
            heading=chunk.heading,
            text=chunk.text,
        )
        for chunk, doc in db.execute(statement).all()
    ]
    return PolicyIndex(chunks)
