# Payable Auto-Draft

CPU-first invoice/document processing pipeline that converts payable documents into ERP-validatable AutoDraft JSON.

## What it does

The pipeline processes PDFs from `documents/` and runs them through:

1. PDF ingestion and native text extraction with `pypdf`.
2. Lazy page rendering with `pypdfium2` only when visual OCR is required.
3. Lightweight OCR triage and Tesseract fallback.
4. Optional PaddleOCR for visual OCR.
5. Document classification and payable/non-payable detection.
6. Invoice/credit-memo splitting and extraction.
7. Supplier, PO, tax, payment-term and chart-of-books matching against `master_data/`.
8. Deterministic financial, master-data, schema and ERP validation.
9. Optional Qwen/Unlimited-OCR rescue layers when explicitly enabled.
10. JSON AutoDraft output plus per-document audit information.

The design intentionally keeps expensive OCR/VLM work optional so the default configuration is suitable for CPU/local/Kaggle-style execution.

## Project structure

```text
payable-autodraft/
├── documents/                  # Input PDFs
├── master_data/                # Supplied master data
│   ├── suppliers.json
│   ├── po_master.json
│   ├── tax_master.json
│   ├── payment_terms.json
│   └── chart_of_books.json
├── output/                     # Generated results
│   ├── <document>.json         # AutoDraft result per PDF
│   ├── audit/                  # Detailed validation/audit per PDF
│   └── page_images/            # Rendered pages used by OCR
├── src/
│   ├── config/
│   ├── ingestion/
│   ├── ocr/
│   ├── document_understanding/
│   ├── extraction/
│   ├── matching/
│   ├── supervisor/
│   ├── validation/
│   ├── autodraft/
│   ├── evidence/
│   └── utils/
├── erp.py
├── AUTODRAFT_SCHEMA.md
├── requirements.txt
├── README.md
└── DESIGN.md
```

## Installation

Create/activate a virtual environment and install the dependencies:

```powershell
venv\Scripts\activate
python -m pip install -r requirements.txt
```

### Tesseract requirement

`pytesseract` is the Python wrapper. The Tesseract OCR executable must also be installed and available on `PATH` when `TESSERACT_ENABLED=true`.

## One command to process the entire `documents/` folder

From the **project root**:

```powershell
python -m src.main
```

That command automatically discovers every `*.pdf` in `documents/` and writes the results into `output/`.

For a single document, the optional command is:

```powershell
python -m src.main --file documents\INV-02.pdf
```

## Output

For each input PDF, the application writes:

```text
output/<document-stem>.json
output/audit/<document-stem>.json
```

The main JSON follows the AutoDraft contract:

```json
{
  "file": "example.pdf",
  "payables": [],
  "declined": []
}
```

A document that cannot be safely validated is kept out of `payables` and placed in `declined`/review output rather than inventing unsupported master-data values.

## Default CPU configuration

The default configuration in `src/config/settings.py` is intentionally conservative:

- Native PDF text first.
- Triage rendering at low DPI.
- Detailed OCR only for selected pages.
- Tesseract enabled as the lightweight local fallback.
- PaddleOCR disabled by default.
- Unlimited-OCR disabled by default.
- Qwen supervisor disabled by default.

These settings can be overridden through `.env` without changing source code.

## Validation notes

The fixed source was validated against the supplied document test set during development. Validation means the documents could be processed through the pipeline without a fatal command failure; individual documents can still legitimately end in review when deterministic validation cannot establish a safe ERP-ready result.

This project should therefore be treated as a document-processing/validation prototype rather than a claim of universal invoice accuracy or production readiness.
