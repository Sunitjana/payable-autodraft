# Payable Auto-Draft — Design

## 1. Objective

The system converts heterogeneous payable PDFs into structured AutoDraft JSON while preserving a strict validation boundary before a document is accepted for ERP use.

The central design principle is:

> **Use inexpensive deterministic processing first; use OCR/AI only as a targeted recovery mechanism; accept only data that passes financial, master-data, schema and ERP validation.**

## 2. End-to-end flow

```text
                    ┌──────────────────────┐
                    │ documents/*.pdf      │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │ PDF ingestion        │
                    │ pypdf                │
                    └──────────┬───────────┘
                               │
                    native text available?
                         ┌─────┴─────┐
                        yes          no/poor
                         │             │
                         ▼             ▼
                    Native text   Low-cost triage
                                      │
                                      ▼
                              Candidate page routing
                                      │
                                      ▼
                         Detailed OCR only when needed
                         ┌────────────┼──────────────┐
                         │            │              │
                    Tesseract    PaddleOCR     Optional rescue
                    fallback      optional     Unlimited-OCR/Qwen
                         └────────────┼──────────────┘
                                      │
                                      ▼
                         Document classification
                                      │
                       ┌──────────────┴──────────────┐
                       │                             │
                 non-payable                     payable
                       │                             │
                       ▼                             ▼
                   declined                 document grouping
                                                     │
                                                     ▼
                                              field extraction
                                                     │
                                                     ▼
                                          master-data matching
                                                     │
                                                     ▼
                                          AutoDraft construction
                                                     │
                                                     ▼
                                      ┌──────────────┼──────────────┐
                                      │              │              │
                                  Master data    Financial       Schema
                                  validation     validation      validation
                                      │              │              │
                                      └──────────────┼──────────────┘
                                                     │
                                                     ▼
                                               ERP validation
                                                     │
                                      ┌──────────────┴──────────────┐
                                      │                             │
                                   PASS                         FAIL
                                      │                             │
                                      ▼                             ▼
                                  payables                      review
```

## 3. PDF ingestion

`src/ingestion/pdf_loader.py` uses `pypdf` for native PDF text. `src/ingestion/page_processor.py` uses `pypdfium2` for lazy rendering.

This avoids rendering every page at high resolution when the PDF already contains usable text.

## 4. OCR strategy

The OCR layer is deliberately a cascade rather than a single heavyweight model.

### Stage A — native PDF text

If extracted text has sufficient quality, it is used directly.

### Stage B — triage

For pages without adequate native text, a low-DPI render is used to cheaply determine whether a page is relevant.

### Stage C — detailed OCR

Only candidate pages are rendered at the configured OCR DPI and sent through the enabled OCR stack.

### Stage D — optional rescue

Unlimited-OCR and the Qwen visual supervisor are disabled by default. They can be enabled for environments where model-based recovery is required.

This architecture reduces CPU/RAM usage on long documents containing attachments or non-payable pages.

## 5. Document understanding

`src/document_understanding/` contains:

- `classifier.py` — document type classification.
- `payable_detector.py` — payable/non-payable decision support.
- `document_splitter.py` — grouping/splitting logic for multiple payable documents.

Repeated invoice labels on continuation pages are treated as document identity evidence rather than automatically creating new invoices.

Strong non-payable indicators such as purchase orders, delivery notes and payment reminders are routed away from payable extraction.

## 6. Extraction

`src/extraction/` contains specialized and robust parsers for:

- invoice number and dates
- line items
- tax
- charges
- discounts
- payment terms
- numeric/date normalization

The robust parser also protects the pipeline from malformed OCR tokens and handles common multilingual invoice formats.

Credit memos preserve positive magnitude values while representing the document type as a credit memo, consistent with the AutoDraft contract.

## 7. Master-data matching

The system loads the supplied master files from `master_data/`:

- suppliers
- purchase orders
- tax master
- payment terms
- chart of books

Matching is performed by dedicated modules under `src/matching/`.

### No invention rule

If a supplier, PO, tax code, payment term or accounting value cannot be supported by the supplied master data and document evidence, the system does not fabricate a value. Unresolved master values remain unresolved and can cause review.

## 8. Validation boundary

The acceptance gate is intentionally strict.

A constructed payload must pass:

1. master-data validation,
2. financial consistency validation,
3. AutoDraft schema validation,
4. supplied ERP validation.

Only when the required validation results are valid is the payload returned in `payables`.

Otherwise the document/group is routed to review.

This prevents a plausible-looking OCR result from being treated as an ERP-safe posting automatically.

## 9. Financial validation

Financial validation checks relationships among invoice components such as:

```text
subtotal
+ tax
+ charges
- discounts
= total
```

Where appropriate, tax is validated at the line/summary level rather than relying only on gross-total matching. Recovery logic can reconstruct missing OCR tax information when the remaining document evidence makes the calculation deterministic.

The supplied ERP implementation is treated as the final recomputation authority.

## 10. AutoDraft contract

`src/autodraft/builder.py` creates the payload and `src/autodraft/schema_validator.py` validates it against `AUTODRAFT_SCHEMA.md`.

The top-level output contract is:

```json
{
  "file": "document.pdf",
  "payables": [],
  "declined": []
}
```

## 11. Evidence and audit

The pipeline writes an audit JSON under `output/audit/` for every processed PDF. The audit records processing status, classification/group information, OCR errors and validation results.

Rendered OCR pages are stored under `output/page_images/` when visual processing is required.

## 12. Performance design

The main CPU optimizations are:

- native text extraction before image rendering,
- lazy page rendering,
- low-DPI triage,
- bounded detailed OCR page windows,
- no default PaddleOCR/Unlimited-OCR/Qwen execution,
- deterministic parsing and matching before model escalation,
- garbage collection between documents.

The trade-off is deliberate: the system favors safe review over blindly spending CPU on every page or inventing uncertain values.

## 13. Configuration

Configuration is loaded from `.env` by `src/config/settings.py`.

Important settings include:

```text
OCR_DPI=180
TRIAGE_DPI=72
DEEP_PAGE_WINDOW=2
TESSERACT_ENABLED=true
PADDLE_ENABLED=false
UNLIMITED_OCR_ENABLED=false
SUPERVISOR_ENABLED=false
```

The defaults are intended for CPU-first execution. Heavier components can be enabled when the runtime and model resources justify them.

## 14. Processing command

The documented full-folder command is:

```powershell
python -m src.main
```

The application automatically processes every `*.pdf` under `documents/` and writes results to `output/`.
