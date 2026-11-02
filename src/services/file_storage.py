"""Secure validation and storage for uploaded KYC document files.

Rules:
* The file type is decided from the file's magic bytes, never from the
  client-supplied name or Content-Type.
* Size is enforced while reading, so oversized uploads are never fully buffered.
* Stored names are random; the original name is kept only as sanitized display
  metadata, so path traversal through the name is impossible.
* Files are written with owner-only permissions and moved into place atomically.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

CHUNK_SIZE = 64 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_DISPLAY_NAME = 120

# PDF features that can run code or open external content.
_RISKY_PDF_TOKENS = (
    b"/JavaScript",
    b"/JS",
    b"/Launch",
    b"/OpenAction",
    b"/AA",
    b"/EmbeddedFile",
    b"/RichMedia",
    b"/XFA",
)

_CONTENT_TYPES = {
    "png": ("image/png", ".png"),
    "jpeg": ("image/jpeg", ".jpg"),
    "pdf": ("application/pdf", ".pdf"),
}


class UploadRejected(Exception):
    """Raised when an upload fails validation. ``status_code`` maps to HTTP."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class StoredFile:
    stored_name: str
    path: Path
    original_name: str
    content_type: str
    size_bytes: int
    sha256: str


def sanitize_display_name(name: str | None) -> str:
    """Return a safe display name (no path parts or control characters)."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFC", name)
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name[:MAX_DISPLAY_NAME] or "document"


def detect_kind(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"%PDF-"):
        return "pdf"
    return None


def _validate_image(data: bytes) -> None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise UploadRejected("أبعاد الصورة كبيرة جدًا.")
            if width < 50 or height < 50:
                raise UploadRejected("الصورة صغيرة جدًا ولا يمكن قراءتها.")
            image.verify()
    except UploadRejected:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise UploadRejected("ملف الصورة تالف أو غير صالح.") from None


def _validate_pdf(data: bytes) -> None:
    if b"%%EOF" not in data[-2048:]:
        raise UploadRejected("ملف PDF غير مكتمل أو تالف.")
    for token in _RISKY_PDF_TOKENS:
        # Match whole PDF names only (e.g. "/JS" must not match "/JSON").
        if re.search(re.escape(token) + rb"(?![A-Za-z0-9])", data):
            raise UploadRejected("ملف PDF يحتوي على عناصر نشطة غير مسموح بها.")


def read_limited(stream: BinaryIO, max_bytes: int) -> bytes:
    """Read ``stream`` fully, failing as soon as ``max_bytes`` is exceeded."""
    buffer = bytearray()
    while True:
        chunk = stream.read(CHUNK_SIZE)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise UploadRejected("حجم الملف أكبر من الحد المسموح به.", 413)
    if not buffer:
        raise UploadRejected("الملف فارغ.")
    return bytes(buffer)


def validate_upload(data: bytes) -> str:
    """Validate the bytes and return the detected kind (png/jpeg/pdf)."""
    kind = detect_kind(data[:16])
    if kind is None:
        raise UploadRejected("نوع الملف غير مدعوم. المسموح: PNG وJPEG وPDF.", 415)
    if kind == "pdf":
        _validate_pdf(data)
    else:
        _validate_image(data)
    return kind


def store_file(
    data: bytes,
    kind: str,
    original_name: str | None,
    base_dir: Path,
    subdir: str,
) -> StoredFile:
    """Write ``data`` atomically under ``base_dir/subdir`` with a random name."""
    content_type, extension = _CONTENT_TYPES[kind]
    stored_name = f"{uuid.uuid4().hex}{extension}"

    target_dir = (base_dir / subdir).resolve()
    base_resolved = base_dir.resolve()
    if base_resolved not in target_dir.parents and target_dir != base_resolved:
        raise UploadRejected("مسار التخزين غير صالح.", 500)
    target_dir.mkdir(parents=True, exist_ok=True)

    final_path = target_dir / stored_name
    fd, temp_name = tempfile.mkstemp(dir=target_dir, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        try:
            os.chmod(temp_name, 0o600)
        except OSError:  # pragma: no cover - not supported on every platform
            pass
        os.replace(temp_name, final_path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise

    return StoredFile(
        stored_name=stored_name,
        path=final_path,
        original_name=sanitize_display_name(original_name),
        content_type=content_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def delete_file(path: Path) -> None:
    """Best-effort removal used to roll back after a failed database write."""
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover
        pass
