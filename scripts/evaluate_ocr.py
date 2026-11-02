"""Measure OCR field-extraction quality against ground-truth labels.

Usage:
    python scripts/generate_synthetic_documents.py --count 30 --out data/synthetic_docs
    python scripts/evaluate_ocr.py --dir data/synthetic_docs

``labels.json`` format: a list of {"file", "document_type", "fields": {...}} where
document_type is one of national_id / address_proof / customer_form and the
fields use the names full_name, national_id, address, id_expiry_date, mobile.

Reported metrics (per field and macro):
* precision = correct / extracted        * recall = correct / expected
* F1        = harmonic mean              * silent errors = wrong values NOT flagged
  for review (the dangerous case: a wrong value the reviewer is not warned about)

Run it on YOUR documents before quoting accuracy numbers; synthetic images are
cleaner than real scans.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import get_settings  # noqa: E402
from src.ocr.engine import TesseractEngine  # noqa: E402
from src.ocr.fields import (  # noqa: E402
    DOC_ADDRESS_PROOF,
    DOC_CUSTOMER_FORM,
    DOC_NATIONAL_ID,
    EXPECTED_FIELDS,
    normalize_digits,
    extract_fields,
)
from src.services.complaint_router import normalize_arabic  # noqa: E402

DOC_TYPES = {
    "national_id": DOC_NATIONAL_ID,
    "address_proof": DOC_ADDRESS_PROOF,
    "customer_form": DOC_CUSTOMER_FORM,
}


def norm(name: str, value: str) -> str:
    value = normalize_digits(value)
    if name in {"full_name", "address"}:
        value = normalize_arabic(value)
        value = re.sub(r"[^\w]", "", value)
        if name == "address":
            value = value.replace("ء", "")  # OCR often reads the Arabic comma as hamza
    return re.sub(r"\s+", "", value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0, help="evaluate only the first N files")
    parser.add_argument("--show-errors", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    engine = TesseractEngine(
        command=settings.tesseract_cmd,
        languages=settings.ocr_languages,
        timeout_seconds=settings.ocr_timeout_seconds,
    )
    labels = json.loads((args.dir / "labels.json").read_text(encoding="utf-8"))
    if args.limit:
        labels = labels[: args.limit]

    stats = defaultdict(lambda: {"expected": 0, "extracted": 0, "correct": 0, "silent": 0, "flagged_wrong": 0})
    for entry in labels:
        doc_type = DOC_TYPES[entry["document_type"]]
        result = extract_fields(
            engine.recognize(args.dir / entry["file"], "image/png"),
            doc_type,
            settings.ocr_min_confidence,
        )
        for name in EXPECTED_FIELDS[doc_type]:
            truth = entry["fields"].get(name)
            if truth is None:
                continue
            s = stats[name]
            s["expected"] += 1
            got = result.get(name)
            if got is None:
                continue
            s["extracted"] += 1
            if norm(name, got.value) == norm(name, truth):
                s["correct"] += 1
            elif got.needs_review:
                s["flagged_wrong"] += 1
            else:
                s["silent"] += 1
                if args.show_errors:
                    print(f"SILENT ERROR {entry['file']} {name}: got={got.value!r} truth={truth!r} conf={got.confidence}")

    print(f"{'field':16} {'n':>4} {'prec':>6} {'recall':>7} {'F1':>6} {'silent':>7} {'flagged-wrong':>14}")
    f1s = []
    for name, s in sorted(stats.items()):
        precision = s["correct"] / s["extracted"] if s["extracted"] else 0.0
        recall = s["correct"] / s["expected"] if s["expected"] else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1s.append(f1)
        print(f"{name:16} {s['expected']:>4} {precision:>6.2f} {recall:>7.2f} {f1:>6.2f} {s['silent']:>7} {s['flagged_wrong']:>14}")
    if f1s:
        print(f"\nMacro F1 = {sum(f1s) / len(f1s):.3f}   (handbook target >= 0.90)")
        print(f"Silent errors in total = {sum(s['silent'] for s in stats.values())}  (should be 0 or very low)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
