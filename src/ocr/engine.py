"""OCR engine abstraction with a Tesseract implementation.

The engine is called through a small protocol so tests (and later other OCR
back-ends) can be swapped in without touching the API layer.
"""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps

MIN_OCR_WIDTH = 1600
MAX_OCR_WIDTH = 4000


class OcrUnavailable(RuntimeError):
    """Raised when the OCR binary (or PDF rasterizer) is not installed."""


class OcrFailed(RuntimeError):
    """Raised when OCR ran but failed or timed out."""


@dataclass(frozen=True)
class OcrWord:
    text: str
    confidence: float  # 0.0 - 1.0
    left: int
    top: int
    width: int
    height: int
    line_id: int


@dataclass(frozen=True)
class OcrPage:
    page: int
    words: list[OcrWord] = field(default_factory=list)

    def lines(self) -> list[list[OcrWord]]:
        grouped: dict[int, list[OcrWord]] = {}
        for word in self.words:
            grouped.setdefault(word.line_id, []).append(word)
        # Arabic is read right-to-left, so order words by descending x.
        return [
            sorted(words, key=lambda w: -w.left)
            for _, words in sorted(grouped.items())
        ]


@dataclass(frozen=True)
class OcrResult:
    engine: str
    languages: str
    pages: list[OcrPage]

    @property
    def text(self) -> str:
        out: list[str] = []
        for page in self.pages:
            for line in page.lines():
                out.append(" ".join(word.text for word in line))
        return "\n".join(out)


class OcrEngine(Protocol):
    name: str

    def recognize(self, path: Path, content_type: str) -> OcrResult: ...


def preprocess_image(image: Image.Image) -> Image.Image:
    """Grayscale, normalize contrast and upscale small scans for better OCR."""
    image = ImageOps.exif_transpose(image)
    image = image.convert("L")
    image = ImageOps.autocontrast(image, cutoff=1)
    width, height = image.size
    if width < MIN_OCR_WIDTH:
        scale = MIN_OCR_WIDTH / width
        image = image.resize((MIN_OCR_WIDTH, int(height * scale)), Image.LANCZOS)
    elif width > MAX_OCR_WIDTH:
        scale = MAX_OCR_WIDTH / width
        image = image.resize((MAX_OCR_WIDTH, int(height * scale)), Image.LANCZOS)
    return image


class TesseractEngine:
    name = "tesseract"

    def __init__(
        self,
        command: str = "tesseract",
        languages: str = "ara+eng",
        timeout_seconds: int = 60,
        max_pdf_pages: int = 3,
        pdftoppm_command: str = "pdftoppm",
    ) -> None:
        self.command = command
        self.languages = languages
        self.timeout_seconds = timeout_seconds
        self.max_pdf_pages = max_pdf_pages
        self.pdftoppm_command = pdftoppm_command

    # ------------------------------------------------------------ public API
    def recognize(self, path: Path, content_type: str) -> OcrResult:
        if shutil.which(self.command) is None and not Path(self.command).exists():
            raise OcrUnavailable(
                "Tesseract غير مثبت أو مساره غير صحيح (اضبط TESSERACT_CMD)."
            )

        with tempfile.TemporaryDirectory(prefix="nileops_ocr_") as workdir:
            work = Path(workdir)
            if content_type == "application/pdf":
                images = self._rasterize_pdf(path, work)
            else:
                images = [path]

            pages: list[OcrPage] = []
            for index, image_path in enumerate(images, start=1):
                prepared = work / f"page_{index}.png"
                with Image.open(image_path) as source:
                    preprocess_image(source).save(prepared, format="PNG")
                pages.append(self._ocr_image(prepared, index))

        return OcrResult(engine=self.name, languages=self.languages, pages=pages)

    # --------------------------------------------------------------- helpers
    def _rasterize_pdf(self, path: Path, work: Path) -> list[Path]:
        if shutil.which(self.pdftoppm_command) is None:
            raise OcrUnavailable(
                "قراءة ملفات PDF تحتاج أداة pdftoppm (Poppler)، وهي غير متاحة."
            )
        prefix = work / "pdfpage"
        try:
            subprocess.run(
                [
                    self.pdftoppm_command,
                    "-r", "300",
                    "-png",
                    "-l", str(self.max_pdf_pages),
                    str(path),
                    str(prefix),
                ],
                check=True,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise OcrFailed("انتهت مهلة تحويل ملف PDF.") from None
        except subprocess.CalledProcessError:
            raise OcrFailed("تعذر تحويل ملف PDF إلى صور.") from None
        pages = sorted(work.glob("pdfpage*.png"))
        if not pages:
            raise OcrFailed("ملف PDF لا يحتوي على صفحات قابلة للقراءة.")
        return pages

    def _ocr_image(self, image_path: Path, page: int) -> OcrPage:
        try:
            completed = subprocess.run(
                [
                    self.command,
                    str(image_path),
                    "stdout",
                    "-l", self.languages,
                    "--psm", "6",
                    "tsv",
                ],
                check=True,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise OcrFailed("انتهت مهلة قراءة المستند.") from None
        except subprocess.CalledProcessError:
            raise OcrFailed("فشلت قراءة المستند بواسطة OCR.") from None

        words = parse_tsv(completed.stdout.decode("utf-8", "replace"))
        words += self._recover_missed_rows(image_path, words)
        return OcrPage(page=page, words=words)

    def _recover_missed_rows(self, image_path: Path, words: list[OcrWord]) -> list[OcrWord]:
        """Re-read text rows that the full-page pass skipped entirely.

        Page-level layout analysis sometimes drops a whole line. We find ink rows
        with a projection profile and OCR any row that no recognised word covers.
        """
        recovered: list[OcrWord] = []
        with Image.open(image_path) as page_image:
            for index, (top, bottom) in enumerate(_ink_rows(page_image)):
                if any(w.top < bottom and w.top + w.height > top for w in words):
                    continue
                margin = int(page_image.width * _EDGE_MARGIN)
                pad = 12
                box = (
                    margin,
                    max(top - pad, 0),
                    page_image.width - margin,
                    min(bottom + pad, page_image.height),
                )
                crop = page_image.crop(box)
                crop = ImageOps.expand(crop, border=20, fill=255)
                with tempfile.TemporaryDirectory(prefix="nileops_row_") as tmp:
                    row_path = Path(tmp) / "row.png"
                    crop.save(row_path, format="PNG")
                    try:
                        done = subprocess.run(
                            [self.command, str(row_path), "stdout", "-l", self.languages, "--psm", "7", "tsv"],
                            check=True,
                            capture_output=True,
                            timeout=self.timeout_seconds,
                        )
                    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                        continue
                for word in parse_tsv(done.stdout.decode("utf-8", "replace")):
                    recovered.append(
                        OcrWord(
                            text=word.text,
                            confidence=word.confidence,
                            left=word.left - 20 + box[0],
                            top=word.top - 20 + box[1],
                            width=word.width,
                            height=word.height,
                            line_id=900_000 + index,
                        )
                    )
        return recovered


_EDGE_MARGIN = 0.06


def _ink_rows(image: Image.Image) -> list[tuple[int, int]]:
    """Return (top, bottom) pixel bands that contain ink, ignoring page borders."""
    margin = int(image.width * _EDGE_MARGIN)
    inner = image.crop((margin, 0, image.width - margin, image.height)).convert("L")
    ink = ImageOps.invert(inner).point(lambda p: 255 if p > 110 else 0)
    profile = ink.resize((1, image.height), Image.BOX)
    values = list(profile.tobytes())  # mode "L": one byte per row
    threshold = 255 * 0.003

    bands: list[list[int]] = []
    for y, value in enumerate(values):
        if value > threshold:
            if bands and y - bands[-1][1] <= 10:
                bands[-1][1] = y
            else:
                bands.append([y, y])
    return [(a, b) for a, b in bands if 18 <= b - a + 1 <= 220]


def parse_tsv(tsv: str) -> list[OcrWord]:
    """Parse Tesseract TSV output into words, dropping empty/negative entries."""
    words: list[OcrWord] = []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        text = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf", "-1"))
        except ValueError:
            continue
        if not text or conf < 0:
            continue
        try:
            line_id = (
                int(row["block_num"]) * 10_000
                + int(row["par_num"]) * 100
                + int(row["line_num"])
            )
            words.append(
                OcrWord(
                    text=text,
                    confidence=min(conf / 100.0, 1.0),
                    left=int(row["left"]),
                    top=int(row["top"]),
                    width=int(row["width"]),
                    height=int(row["height"]),
                    line_id=line_id,
                )
            )
        except (KeyError, ValueError):
            continue
    return words
