"""Generate synthetic KYC document images and ground-truth labels.

All names, numbers and addresses are random and the images are watermarked as
synthetic. Use the output to demo the upload/OCR flow and to measure OCR field
accuracy with ``scripts/evaluate_ocr.py``.

Usage:
    python scripts/generate_synthetic_documents.py --count 20 --out data/synthetic_docs
    python scripts/generate_synthetic_documents.py --count 20 --quality scan

Arabic text needs a font with Arabic glyphs and Pillow built with libraqm
(``python -c "from PIL import features; print(features.check('raqm'))"``).
Without raqm the Arabic letters are not joined and OCR results will be poor.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

FONT_CANDIDATES = [
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]

FIRST_NAMES = ["أحمد", "محمد", "محمود", "مصطفى", "عمر", "يوسف", "خالد", "سارة", "منى", "مريم", "نور", "هبة", "آية", "دينا", "علي"]
FATHER_NAMES = ["إبراهيم", "حسن", "عبد الله", "السيد", "محمود", "علي", "أحمد", "خالد", "سعيد", "فتحي"]
FAMILY_NAMES = ["الشافعي", "منصور", "عبد الرحمن", "الجمال", "النجار", "فرج", "عثمان", "رمضان", "زكي", "مصطفى"]
STREETS = ["شارع التحرير", "شارع الهرم", "شارع النيل", "شارع الجمهورية", "شارع فيصل", "شارع مصطفى كامل"]
AREAS = [("القاهرة", "01"), ("الإسكندرية", "02"), ("الجيزة", "21"), ("الدقهلية", "12"), ("الغربية", "13"), ("أسيوط", "27")]

EASTERN_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def find_font() -> str:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise SystemExit("No Arabic-capable font found. Edit FONT_CANDIDATES in this script.")


def make_person(rng: random.Random) -> dict:
    first = rng.choice(FIRST_NAMES)
    name = f"{first} {rng.choice(FATHER_NAMES)} {rng.choice(FAMILY_NAMES)}"
    birth = date(1960, 1, 1) + timedelta(days=rng.randint(0, 365 * 42))
    century = "2" if birth.year < 2000 else "3"
    area, gov = rng.choice(AREAS)
    sequence = f"{rng.randint(0, 9999):04d}"
    national_id = f"{century}{birth:%y%m%d}{gov}{sequence}{rng.randint(0, 9)}"
    expiry = date.today() + timedelta(days=rng.randint(-200, 365 * 6))
    issued = date.today() - timedelta(days=rng.randint(5, 80))
    return {
        "full_name": name,
        "national_id": national_id,
        "address": f"{rng.randint(1, 120)} {rng.choice(STREETS)}، {area}",
        "id_expiry_date": expiry.isoformat(),
        "issue_date": issued.isoformat(),
        "mobile": f"01{rng.choice('0125')}{rng.randint(10_000_000, 99_999_999)}",
    }


def build_documents(person: dict, eastern: bool) -> dict[str, tuple[str, list[tuple[str, str]], dict]]:
    def num(value: str) -> str:
        return value.translate(EASTERN_DIGITS) if eastern else value

    expiry = person["id_expiry_date"].replace("-", "/")
    issue = person["issue_date"].replace("-", "/")
    return {
        "national_id": (
            "بطاقة تحقيق الشخصية (نموذج اصطناعي)",
            [
                ("الاسم", person["full_name"]),
                ("الرقم القومي", num(person["national_id"])),
                ("العنوان", num(person["address"])),
                ("تاريخ الانتهاء", num(expiry)),
            ],
            {k: person[k] for k in ("full_name", "national_id", "address", "id_expiry_date")},
        ),
        "address_proof": (
            "إثبات عنوان - فاتورة مرافق (نموذج اصطناعي)",
            [
                ("الاسم", person["full_name"]),
                ("العنوان", num(person["address"])),
                ("تاريخ الإصدار", num(issue)),
            ],
            {k: person[k] for k in ("full_name", "address")},
        ),
        "customer_form": (
            "استمارة بيانات العميل (نموذج اصطناعي)",
            [
                ("الاسم", person["full_name"]),
                ("الرقم القومي", num(person["national_id"])),
                ("رقم الهاتف", num(person["mobile"])),
                ("العنوان", num(person["address"])),
            ],
            {k: person[k] for k in ("full_name", "national_id", "mobile", "address")},
        ),
    }


def draw_document(title: str, rows: list[tuple[str, str]], font_path: str, quality: str, rng: random.Random) -> Image.Image:
    width, height = 1400, 640 + 90 * max(0, len(rows) - 3)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype(font_path, 44, layout_engine=ImageFont.Layout.RAQM)
    body_font = ImageFont.truetype(font_path, 38, layout_engine=ImageFont.Layout.RAQM)
    small_font = ImageFont.truetype(font_path, 24, layout_engine=ImageFont.Layout.RAQM)

    draw.rectangle([20, 20, width - 20, height - 20], outline="black", width=3)
    draw.text((width - 60, 70), title, font=title_font, fill="black", anchor="ra", direction="rtl", language="ar")
    y = 190
    for label, value in rows:
        draw.text((width - 60, y), f"{label}: {value}", font=body_font, fill="black", anchor="ra", direction="rtl", language="ar")
        y += 90
    draw.text((60, height - 60), "SYNTHETIC - FOR TESTING ONLY", font=small_font, fill=(120, 120, 120), anchor="la")

    if quality == "scan":
        image = image.rotate(rng.uniform(-1.5, 1.5), expand=True, fillcolor="white", resample=Image.BICUBIC)
        image = image.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 0.9)))
        noise = Image.effect_noise(image.size, rng.uniform(8, 18)).convert("RGB")
        image = Image.blend(image, noise, 0.06)
    return image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=10, help="number of people (3 documents each)")
    parser.add_argument("--out", type=Path, default=Path("data/synthetic_docs"))
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--quality", choices=("clean", "scan"), default="clean")
    parser.add_argument("--eastern-digits-ratio", type=float, default=0.3)
    args = parser.parse_args()

    if not features.check("raqm"):
        print("WARNING: Pillow has no libraqm; Arabic will not be shaped correctly.", file=sys.stderr)

    font_path = find_font()
    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    labels = []

    for index in range(1, args.count + 1):
        person = make_person(rng)
        eastern = rng.random() < args.eastern_digits_ratio
        for doc_type, (title, rows, truth) in build_documents(person, eastern).items():
            filename = f"person{index:03d}_{doc_type}.png"
            draw_document(title, rows, font_path, args.quality, rng).save(args.out / filename)
            labels.append({"file": filename, "document_type": doc_type, "eastern_digits": eastern, "fields": truth})

    (args.out / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(labels)} images and labels.json to {args.out}")


if __name__ == "__main__":
    main()
