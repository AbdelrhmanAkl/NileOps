# Document Upload, OCR and Review (Simulation)

Base path: `/api/v1/kyc/documents`

The OCR output and the cross-checks **assist** the reviewer. Nothing here
approves a document or an application on its own: a document becomes
`مستوفٍ مبدئيًا` (VALID) only through `POST /{id}/review` by a human, and the
existing application approval still requires all three documents to be VALID.

## Flow

1. `POST /{document_id}/files` uploads a PNG / JPEG / PDF (reviewer, admin).
2. `POST /{document_id}/files/{file_id}/extract` runs OCR and extracts fields.
3. `GET  /{document_id}/files/{file_id}/fields` shows fields and checks (viewer+).
4. `POST /{document_id}/review` records the human decision
   (`VALID`, `INVALID`, `EXPIRED`, `UNDER_REVIEW`).

## Upload security

* File type comes from the **content** (magic bytes), never from the name or
  Content-Type. Allowed: PNG, JPEG, PDF.
* Size limit `MAX_UPLOAD_SIZE_MB` is enforced while reading (413 when exceeded).
* Images must decode and are limited to 40 megapixels; PDFs must end with
  `%%EOF` and are rejected if they contain `/JavaScript`, `/JS`, `/Launch`,
  `/OpenAction`, `/AA`, `/EmbeddedFile`, `/RichMedia` or `/XFA`.
* Stored under `UPLOAD_DIR/documents/<application_id>/<random>.<ext>` with
  owner-only permissions, written atomically. The original name is kept only as
  sanitized display metadata.
* The same file (SHA-256) cannot be uploaded twice for one document (409).
* A document already `VALID` or `EXPIRED` cannot receive new files (409).
* There is deliberately **no download endpoint** in this phase.
* `storage/` is git-ignored. Do not use real customer documents without legal
  and data-protection approval.

## Extraction output

Each field has `value`, `confidence` (0-1), `needs_review` + `review_reason`,
and `source` (`page`, `line`, `bbox`, `line_text`). Fields come from fixed rules:

| Document type | Expected fields |
|---|---|
| بطاقة الرقم القومي | full_name, national_id, address, id_expiry_date |
| إثبات العنوان | full_name, address |
| استمارة بيانات العميل | full_name, national_id, mobile, address |

A field is flagged for review when its confidence is below `OCR_MIN_CONFIDENCE`,
any single word is read with low confidence, Latin garbage appears inside
Arabic text, the national-ID structure is invalid, or the label was unclear.
Missing fields are listed in `missing_fields`. Eastern Arabic digits are
normalized. Birth date and gender are derived from a structurally valid
national ID.

### Checks returned with the fields

`NAME_MATCH`, `ID_STRUCTURE`, `ID_LAST4_MATCH`, `ID_NOT_EXPIRED`,
`OCR_CONFIDENCE`. `passed: null` means "could not be evaluated". Checks are
computed on request and are **not** written to `validation_results`.

### Privacy

Audit events record counts only, never extracted values. `extracted_text`
(the full OCR text) is stored on the document for the reviewer; apply your
retention policy to it.

## Setup

0. Install the new dependency: `pip install -r requirements.txt` (adds Pillow).
1. Database: `python -m scripts.init_db` (adds `document_files` and
   `extracted_fields`; existing tables are untouched).
2. Tesseract with Arabic:
   * Windows: install the UB Mannheim build and tick **Arabic** (`ara`) in the
     language list. Then set in `.env`:
     `TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe`
   * Linux: `apt install tesseract-ocr tesseract-ocr-ara`
   * Verify: `tesseract --list-langs` must list `ara`.
3. PDF files need Poppler (`pdftoppm` on PATH). Images work without it.

## Measuring quality

```
python -m scripts.generate_synthetic_documents --count 30 --out data/synthetic_docs
python -m scripts.generate_synthetic_documents --count 30 --out data/synthetic_docs_scan --quality scan
python -m scripts.evaluate_ocr --dir data/synthetic_docs --show-errors
```

The evaluator reports per-field precision, recall, F1 and **silent errors**
(wrong values that were not flagged for review). Synthetic images are cleaner
than real scans, so measure again on your own labelled documents before quoting
numbers against the handbook targets (macro F1 >= 0.90).

Known limits: Eastern-Arabic digit accuracy depends on the font/scan and is not
validated; leading digits of house numbers can be dropped without a warning;
street names are the weakest field.
