"""Parse policy markdown files (with a small front matter) into sections."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

MAX_CHUNK_CHARS = 700
_REQUIRED = ("policy_code", "version", "title")
_CODE = re.compile(r"^[A-Z]{2,6}-[A-Z]{2,6}-\d{3}$")


class PolicyFormatError(ValueError):
    """Raised when a policy file is malformed."""


@dataclass(frozen=True)
class ParsedChunk:
    heading: str
    text: str


@dataclass(frozen=True)
class ParsedPolicy:
    policy_code: str
    version: int
    title: str
    department: str | None
    effective_date: date | None
    sha256: str
    chunks: list[ParsedChunk] = field(default_factory=list)


def parse_policy(raw: bytes | str) -> ParsedPolicy:
    data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PolicyFormatError("الملف يجب أن يكون بترميز UTF-8.") from exc
    text = text.replace("\r\n", "\n")

    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, flags=re.DOTALL)
    if not match:
        raise PolicyFormatError("الملف يفتقد رأس البيانات (--- ... ---).")
    meta: dict[str, str] = {}
    for line in match.group(1).split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    for key in _REQUIRED:
        if not meta.get(key):
            raise PolicyFormatError(f"الحقل المطلوب غير موجود: {key}")
    if not _CODE.match(meta["policy_code"]):
        raise PolicyFormatError("صيغة رمز السياسة غير صحيحة (مثال: POL-CARD-001).")
    try:
        version = int(meta["version"])
        if version < 1:
            raise ValueError
    except ValueError as exc:
        raise PolicyFormatError("رقم الإصدار يجب أن يكون عددًا صحيحًا موجبًا.") from exc
    effective = None
    if meta.get("effective_date"):
        try:
            effective = date.fromisoformat(meta["effective_date"])
        except ValueError as exc:
            raise PolicyFormatError("تاريخ السريان يجب أن يكون بصيغة YYYY-MM-DD.") from exc

    chunks = split_sections(match.group(2))
    if not chunks:
        raise PolicyFormatError("السياسة لا تحتوي على أقسام (## عنوان).")
    return ParsedPolicy(
        policy_code=meta["policy_code"],
        version=version,
        title=meta["title"],
        department=meta.get("department") or None,
        effective_date=effective,
        sha256=hashlib.sha256(data).hexdigest(),
        chunks=chunks,
    )


def split_sections(body: str) -> list[ParsedChunk]:
    sections: list[tuple[str, list[str]]] = []
    for line in body.split("\n"):
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections and line.strip() and not line.startswith(">"):
            sections[-1][1].append(line.strip())
    chunks: list[ParsedChunk] = []
    for heading, lines in sections:
        paragraph = " ".join(lines)
        for part in _split_long(paragraph):
            chunks.append(ParsedChunk(heading=heading, text=part))
    return chunks


def _split_long(text: str) -> list[str]:
    if not text:
        return []
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    sentences = re.split(r"(?<=[.!؟?])\s+", text)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > MAX_CHUNK_CHARS:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


def read_policy_files(directory: Path) -> list[ParsedPolicy]:
    if not directory.is_dir():
        raise PolicyFormatError("مجلد السياسات غير موجود.")
    return [parse_policy(path.read_bytes()) for path in sorted(directory.glob("*.md"))]
