"""Tests for secure upload validation and storage."""

import io
import os
import stat

import pytest
from PIL import Image

from src.services.file_storage import (
    UploadRejected,
    detect_kind,
    read_limited,
    sanitize_display_name,
    store_file,
    validate_upload,
)


def png_bytes(size=(200, 120)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (200, 120), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


def test_valid_files_are_detected_by_content():
    assert validate_upload(png_bytes()) == "png"
    assert validate_upload(jpeg_bytes()) == "jpeg"
    assert validate_upload(MINIMAL_PDF) == "pdf"


@pytest.mark.parametrize(
    "data",
    [b"MZ\x90\x00 executable", b"<html><script>alert(1)</script></html>", b"GIF89a....", b"PK\x03\x04zip"],
)
def test_unsupported_types_are_rejected_with_415(data):
    with pytest.raises(UploadRejected) as error:
        validate_upload(data)
    assert error.value.status_code == 415


def test_truncated_or_corrupt_image_is_rejected():
    with pytest.raises(UploadRejected):
        validate_upload(png_bytes()[:60])
    with pytest.raises(UploadRejected):
        validate_upload(b"\x89PNG\r\n\x1a\n" + b"garbage" * 20)


def test_tiny_image_is_rejected():
    with pytest.raises(UploadRejected):
        validate_upload(png_bytes((10, 10)))


def test_pdf_without_eof_or_with_active_content_is_rejected():
    with pytest.raises(UploadRejected):
        validate_upload(b"%PDF-1.4\nno end marker")
    for token in (b"/JavaScript", b"/JS", b"/Launch", b"/OpenAction", b"/EmbeddedFile"):
        with pytest.raises(UploadRejected):
            validate_upload(b"%PDF-1.4\n<< " + token + b" (x) >>\n%%EOF")


def test_pdf_name_prefix_does_not_cause_false_positive():
    assert validate_upload(b"%PDF-1.4\n<< /JSONData 1 >>\n%%EOF") == "pdf"


def test_extension_and_content_type_cannot_fake_the_type():
    # The content decides; a ".png" name on HTML bytes is still rejected.
    assert detect_kind(b"<html>") is None


def test_read_limited_enforces_size_while_reading():
    assert read_limited(io.BytesIO(b"abc"), 10) == b"abc"
    with pytest.raises(UploadRejected) as too_big:
        read_limited(io.BytesIO(b"x" * 11), 10)
    assert too_big.value.status_code == 413
    with pytest.raises(UploadRejected) as empty:
        read_limited(io.BytesIO(b""), 10)
    assert empty.value.status_code == 422


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\id card.png", "id card.png"),
        ("evil\x00name\n.png", "evilname.png"),
        ("   ", "document"),
        (None, "document"),
        ("..", "document"),
        ("a" * 300 + ".png", "a" * 120),
    ],
)
def test_display_names_are_sanitized(raw, expected):
    assert sanitize_display_name(raw) == expected


def test_store_file_uses_random_name_private_permissions_and_hash(tmp_path):
    data = png_bytes()

    first = store_file(data, "png", "../../evil.png", tmp_path, "documents/1")
    second = store_file(data, "png", "evil.png", tmp_path, "documents/1")

    assert first.path.read_bytes() == data
    assert first.path.parent == (tmp_path / "documents" / "1").resolve()
    assert first.stored_name != second.stored_name
    assert first.stored_name.endswith(".png")
    assert "evil" not in first.stored_name
    assert first.original_name == "evil.png"
    assert first.sha256 == second.sha256
    assert first.content_type == "image/png"
    if os.name == "posix":
        assert stat.S_IMODE(first.path.stat().st_mode) == 0o600
    assert not list(first.path.parent.glob("*.part"))


def test_store_file_refuses_to_escape_the_upload_directory(tmp_path):
    base = tmp_path / "uploads"
    base.mkdir()

    with pytest.raises(UploadRejected):
        store_file(png_bytes(), "png", "x.png", base, "../outside")
    assert not (tmp_path / "outside").exists()
