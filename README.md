# Payable Auto-Draft

## AI-Powered Document-to-ERP Payable Automation

Payable Auto-Draft is a document-understanding pipeline that converts heterogeneous PDF financial documents into structured, ERP-validatable payable AutoDraft JSON.

The system is designed for the supplied challenge where the input directory may contain standard invoices, credit memos, non-payable documents, multi-page documents, scanned PDFs, different layouts, currencies, languages, line/header taxes, discounts, and additional charges.

> **Core principle:** this is a document-understanding system, not just an OCR system. OCR is used to obtain evidence; deterministic extraction, master-data matching, financial validation, schema validation, and ERP recomputation determine whether a payable is safe to book.

---

## 1. Problem Statement

The system receives a folder of PDFs and must:

1. Read every PDF in `documents/`.
2. Determine what type of document it contains.
3. Determine whether the document contains a payable.
4. Handle multiple payables when present.
5. Extract invoice/credit-memo information.
6. Extract line items, taxes, discounts, and charges.
7. Match supplier, PO, tax, payment terms, and chart-of-books information against supplied master data.
8. Never invent a master-data value when a genuine match cannot be established.
9. Build an AutoDraft payload according to `AUTODRAFT_SCHEMA.md`.
10. Validate the result financially and against the supplied ERP.
11. Produce one JSON result for every input PDF.
12. Preserve processing/audit evidence for review.

The challenge explicitly identifies document understanding as the main challenge rather than OCR alone. The economic relationship that must be understood is approximately:

```text
Quantity × Unit Price
        − Discounts
        + Taxes
        + Charges
        = Gross Amount
```

The pipeline must also correctly preserve whether taxes are header-level or line-level, handle different rates, credit memos, multiple invoices, missing/ambiguous information, and non-payable documents.

---

# 2. Architecture Overview

```text
                         documents/*.pdf
                                │
                                ▼
                    ┌─────────────────────┐
                    │     PDF Loader      │
                    │       pypdf         │
                    └──────────┬──────────┘
                               │
                  Native PDF text available?
                         │               │
                       Yes              No/weak
                         │               │
                         │               ▼
                         │      ┌─────────────────┐
                         │      │ Page Rendering  │
                         │      │   pypdfium2     │
                         │      └────────┬────────┘
                         │               │
                         │               ▼
                         │      ┌─────────────────┐
                         │      │ OCR Triage      │
                         │      │   Tesseract     │
                         │      └────────┬────────┘
                         │               │
                         └───────┬───────┘
                                 ▼
                       Document Understanding
                                 │
                ┌────────────────┴────────────────┐
                │                                 │
                ▼                                 ▼
        Payable / Non-payable             Multiple payable groups
                │                                 │
                └────────────────┬────────────────┘
                                 ▼
                         Invoice Extraction
                                 │
          ┌──────────────┬───────┼────────┬─────────────┐
          ▼              ▼       ▼        ▼             ▼
       Header        Line Items  Tax   Discounts      Charges
          │              │       │        │             │
          └──────────────┴───────┴────────┴─────────────┘
                                 │
                                 ▼
                         Master Data Matching
                                 │
        ┌────────────┬───────────┼──────────┬────────────┐
        ▼            ▼           ▼          ▼            ▼
     Supplier        PO         Tax    Payment Terms   CoB
                                 │
                                 ▼
                         AutoDraft Builder
                                 │
                                 ▼
                  ┌─────────────────────────┐
                  │ Validation              │
                  │ • Master data           │
                  │ • Financial consistency │
                  │ • JSON schema           │
                  │ • Supplied ERP          │
                  └────────────┬────────────┘
                               │
                  ┌────────────┴────────────┐
                  ▼                         ▼
             ERP-safe payable          Review/declined
                  │
                  ▼
             output/*.json
```

---

# 3. Processing Pipeline — Step by Step

## Step 1 — Discover input PDFs

`src.main` automatically scans:

```text
documents/*.pdf
```

The user does not need to provide each file individually.

The application processes files sequentially to keep memory and CPU usage predictable.

---

## Step 2 — PDF ingestion

### `src/ingestion/pdf_loader.py`

Uses `pypdf` for native PDF text extraction.

This is intentionally preferred over rendering every page as an image because digitally generated invoices usually contain selectable text. Native extraction is significantly cheaper than OCR.

---

## Step 3 — Lazy page rendering

### `src/ingestion/page_processor.py`

Uses `pypdfium2` only when a page needs visual processing.

Pages are rendered lazily instead of converting the entire document to high-resolution images up front.

This is important for multi-page bundles containing attachments, delivery notes, or other supporting pages.

---

## Step 4 — OCR cascade

### `src/ocr/extractor.py`


Default strategy:

```text
Native PDF text
      │
      ├── good → use native text
      │
      └── weak/empty
              │
              ▼
        low-resolution triage
              │
              ▼
           Tesseract
              │
              ├── enough evidence → continue
              │
              └── optional rescue
                       ├── PaddleOCR
                       └── Unlimited-OCR
```

### Optional OCR components

- PaddleOCR can be enabled for stronger visual OCR.
- Unlimited-OCR can be enabled as an additional rescue layer.
- These are disabled by default to keep CPU/local execution practical.

---

## Step 5 — Page triage

The system first performs cheap triage to identify pages likely to contain a payable.

Examples of strong payable indicators include:

- invoice
- tax invoice
- Rechnung
- Arve
- Fatura/Factura
- credit note
- credit memo
- amount due
- total amount
- VAT/IVA/MWST/GST
- payment terms

Strong non-payable indicators include:

- purchase order
- quotation
- delivery note
- packing list
- goods receipt
- order confirmation
- remittance advice
- timesheet
- payment reminder
- Mahnung

This prevents expensive OCR from being applied to every page of a large PDF bundle.

---

## Step 6 — Document classification

### `src/document_understanding/classifier.py`

Classifies the document using the extracted evidence and identifies categories such as:

- invoice
- credit memo
- non-payable
- other/unknown

A payment reminder is not automatically converted into a new payable merely because it contains a quoted invoice number or amount.

---

## Step 7 — Payable detection

### `src/document_understanding/payable_detector.py`

Determines whether the document represents an economically payable document.

A document can therefore be:

```text
PAYABLE
NON-PAYABLE
REVIEW
```

A non-payable document is not forced into the invoice schema.

---

## Step 8 — Split multiple payables

### `src/document_understanding/document_splitter.py`

A single PDF can contain more than one payable.

The system groups pages into payable/document groups before extraction so that each bookable payable can become a separate object in the `payables[]` array.

Output contract:

```json
{
  "file": "example.pdf",
  "payables": [
    {},
    {}
  ],
  "declined": []
}
```

---

# 4. Extraction Layer

The extraction layer converts document evidence into structured fields.

## Invoice header

### `src/extraction/invoice_extractor.py`

Extracts information such as:

- invoice number
- invoice date
- due date
- invoice type
- currency
- supplier information
- VAT/tax identifier
- PO number
- payment terms
- gross amount
- declared subtotal/tax where available

---

## Robust parsing

### `src/extraction/robust_parser.py`

Provides resilient parsing for:

- numeric amounts
- decimal separators
- dates
- textual dates
- multilingual date patterns
- currency labels
- malformed OCR tokens
- credit memo values

It also protects the pipeline from corrupted OCR numbers that could otherwise create invalid `Decimal` calculations.

---

## Line-item extraction

### `src/extraction/line_item_extractor.py`

Extracts:

- description
- item type
- quantity
- unit of measure
- unit price
- line total
- line discount
- line tax

The AutoDraft schema expects raw components so the ERP can recompute the financial result.

---

## Tax extraction

### `src/extraction/tax_extractor.py`

Handles:

- header-level taxes
- line-level taxes
- different tax rates
- explicit tax amounts
- tax names/codes where printed

Tax placement matters. A line-level tax should remain attached to the relevant line instead of being incorrectly moved to the header.

---

## Discount extraction

### `src/extraction/discount_extractor.py`

Handles discount amounts and percentages where they can be grounded in the document.

---

## Charge extraction

### `src/extraction/charge_extractor.py`

Handles additional financial components such as:

- freight
- insurance
- extra charges
- excise duties

---

## Payment-term extraction

### `src/extraction/payment_terms_extractor.py`

Extracts payment terms from the source document so they can be matched against `payment_terms.json`.

---

# 5. Master Data Matching

The challenge supplies reference data under `master_data/`.

```text
master_data/
├── suppliers.json
├── tax_master.json
├── chart_of_books.json
├── payment_terms.json
└── po_master.json
```

The pipeline matches extracted document information against these references.

## Supplier

### `src/matching/supplier_matcher.py`

Resolves:

```text
supplier.supplier_id
```

against `suppliers.json`.

---

## Purchase Order

### `src/matching/po_matcher.py`

Resolves:

```text
po_number  → printed/raw PO number
po_id      → matched master-data PO identifier
```

---

## Tax

### `src/matching/tax_matcher.py`

Matches tax information against `tax_master.json`.

---

## Payment Terms

### `src/matching/payment_terms_matcher.py`

Matches the extracted payment terms against `payment_terms.json`.

---

## Chart of Books

### `src/matching/chart_of_books_matcher.py`

Resolves buyer/tenant organizational codes against `chart_of_books.json`.

Expected fields include:

```text
company_code
business_unit_code
location_code
```

---

# 6. Master-Data Safety Rule

The system follows the challenge requirement:

> **Never invent a supplier, PO, tax, payment-term, company, business-unit, location, or other master-data value.**

When a genuine match cannot be established, the corresponding master-data code remains empty rather than being guessed.

This behavior is intentional and protects ERP booking integrity.

The configuration permanently keeps:

```text
ALLOW_INVENTED_MASTER_VALUES = False
```

---

# 7. AutoDraft Construction

### `src/autodraft/builder.py`

Builds the final payable object according to `AUTODRAFT_SCHEMA.md`.

The per-file contract is:

```json
{
  "file": "INV-01.pdf",
  "payables": [
    {
      "invoice_number": "...",
      "invoice_date": "YYYY-MM-DD",
      "due_date": "YYYY-MM-DD",
      "invoice_type": "INVOICE",
      "currency": "EUR",
      "supplier": {},
      "buyer": {},
      "payment_term_id": "",
      "po_number": "",
      "po_id": "",
      "gross_total": "...",
      "subtotal": "",
      "total_tax_amount": "",
      "discount_amount": "",
      "freight_charges": "",
      "insurance_charges": "",
      "extra_charges": "",
      "excise_duties": "",
      "taxes": [],
      "line_items": []
    }
  ],
  "declined": []
}
```

---

# 8. Financial Model

The system does not blindly trust OCR totals.

It extracts the financial components and checks whether the resulting economic structure explains the document total.

Conceptually:

```text
Line extensions
      ↓
Discounts
      ↓
Net/subtotal
      ↓
Taxes
      ↓
Charges
      ↓
Expected gross
      ↓
Compare with printed gross
```

The ERP is the final recomputation authority.

---

# 9. Credit Memos

Credit memos use the same AutoDraft schema as invoices.

```json
{
  "invoice_type": "CREDIT_MEMO"
}
```

The document's monetary values are represented as positive magnitudes in the AutoDraft structure, while the invoice type communicates the credit nature of the document.

---

# 10. Validation Pipeline

A payable is not accepted merely because OCR found an invoice number and total.

The current pipeline performs multiple validation stages.

```text
                AutoDraft
                    │
       ┌────────────┼─────────────┐
       ▼            ▼             ▼
  Master Data   Financial       Schema
   Validation   Validation    Validation
       │            │             │
       └────────────┼─────────────┘
                    ▼
               ERP Validation
                    │
             ┌──────┴──────┐
             ▼             ▼
          ACCEPTED       REVIEW
```

## Master validation

### `src/validation/master_validator.py`

Checks that resolved master-data identifiers correspond to the supplied masters.

## Financial validation

### `src/validation/financial_validator.py`

Checks whether the extracted financial components are internally consistent within the configured tolerance.

Default tolerance:

```text
0.01
```

## Schema validation

### `src/autodraft/schema_validator.py`

Checks the generated payload against:

```text
AUTODRAFT_SCHEMA.md
```

## ERP validation

### `src/validation/erp_validator.py`

Loads the supplied `erp.py` and validates the generated payable using the supplied ERP calculation logic.

**The supplied ERP logic is not modified by the application.**

A payable is accepted only when the deterministic validation gates required by the pipeline pass.

---

# 11. Supervisor / AI Rescue Layer

The project contains an optional supervisor layer:

```text
src/supervisor/
├── qwen_vl.py
├── verifier.py
└── confidence.py
```

The intended architecture is:

```text
Deterministic extraction
        │
        ▼
Is critical information missing/ambiguous?
        │
       Yes
        ▼
Optional Qwen3-VL supervisor
        │
        ▼
Advisory repair
        │
        ▼
Run deterministic validation again
```

The supervisor is **not** the final authority. Any repaired fields are passed through the same validation process.

By default:

```text
SUPERVISOR_ENABLED=false
```

This keeps the standard run lightweight and avoids requiring a large local model.

---

# 12. Evidence and Audit

The application can store processing evidence and audit information.

Relevant modules:

```text
src/evidence/evidence_store.py
src/evidence/page_reference.py
```

The output directory contains:

```text
output/
├── <document>.json
├── audit/
│   └── <document>.json
└── page_images/
    └── ...
```

### Main JSON

Contains the AutoDraft result:

```text
output/INV-01.json
```

### Audit JSON

Contains processing information such as:

- elapsed processing time
- OCR errors
- classification
- document groups
- validation results
- payable count
- supervisor information where enabled

### Page images

Rendered pages used by the OCR path are retained when evidence storage is enabled.

---

# 13. Project Structure

```text
payable-autodraft/
│
├── documents/                         # Input PDF folder
│   ├── INV-01.pdf
│   ├── INV-02.pdf
│   └── ...
│
├── master_data/                       # Supplied reference data
│   ├── suppliers.json
│   ├── tax_master.json
│   ├── chart_of_books.json
│   ├── payment_terms.json
│   └── po_master.json
│
├── output/                            # Generated output
│   ├── *.json
│   ├── audit/
│   └── page_images/
│
├── src/
│   ├── main.py                        # Application entry point
│   │
│   ├── config/
│   │   └── settings.py                # Configuration and .env handling
│   │
│   ├── ingestion/
│   │   ├── pdf_loader.py              # Native PDF extraction
│   │   └── page_processor.py          # Lazy page rendering
│   │
│   ├── ocr/
│   │   ├── extractor.py               # OCR routing/cascade
│   │   ├── paddle_ocr.py              # Optional PaddleOCR
│   │   ├── unlimited_ocr.py            # Optional Unlimited-OCR
│   │   └── quality_check.py            # OCR quality checks
│   │
│   ├── document_understanding/
│   │   ├── classifier.py              # Document classification
│   │   ├── document_splitter.py       # Multi-payable grouping
│   │   └── payable_detector.py        # Payable detection
│   │
│   ├── extraction/
│   │   ├── invoice_extractor.py
│   │   ├── line_item_extractor.py
│   │   ├── tax_extractor.py
│   │   ├── charge_extractor.py
│   │   ├── discount_extractor.py
│   │   ├── payment_terms_extractor.py
│   │   └── robust_parser.py
│   │
│   ├── matching/
│   │   ├── supplier_matcher.py
│   │   ├── po_matcher.py
│   │   ├── tax_matcher.py
│   │   ├── payment_terms_matcher.py
│   │   ├── chart_of_books_matcher.py
│   │   └── common.py
│   │
│   ├── supervisor/
│   │   ├── qwen_vl.py
│   │   ├── verifier.py
│   │   └── confidence.py
│   │
│   ├── validation/
│   │   ├── consistency.py
│   │   ├── financial_validator.py
│   │   ├── master_validator.py
│   │   └── erp_validator.py
│   │
│   ├── autodraft/
│   │   ├── builder.py
│   │   └── schema_validator.py
│   │
│   ├── evidence/
│   │   ├── evidence_store.py
│   │   └── page_reference.py
│   │
│   └── utils/
│       ├── json_utils.py
│       ├── logging_utils.py
│       └── text_utils.py
│
├── erp.py                             # Supplied ERP calculation engine
├── AUTODRAFT_SCHEMA.md                # Required output contract
├── requirements.txt                   # Python dependencies
├── README.md                          # This document
└── DESIGN.md                          # Detailed design document
```

---

# 14. System Requirements

This project uses PDF processing, OCR, image processing, optional Transformers/VLM components, and AI-based document validation.

## 14.1 Local Minimum Requirements

| Component | Requirement |
|-----------|-------------|
| OS | Windows 10/11 64-bit or Linux 64-bit |
| CPU | Intel Core i5 / AMD Ryzen 5 or equivalent |
| CPU Cores | 4+ |
| RAM | 16 GB |
| GPU | NVIDIA RTX 4050 or equivalent |
| GPU VRAM | 6 GB+ |
| Storage | 30 GB+ free |
| SSD | Recommended |

## 14.2 Local Recommended / Better Requirements

| Component | Requirement |
|-----------|-------------|
| OS | Windows 11 64-bit / Linux 64-bit |
| CPU | Intel Core i7 / Core Ultra 7 / AMD Ryzen 7 |
| CPU Cores | 8+ |
| RAM | 32 GB |
| GPU | NVIDIA RTX 4060 / RTX 5060 or better |
| GPU VRAM | 8 GB+ |
| Storage | 50 GB+ free |
| Storage Type | NVMe SSD |

## 14.3 Cloud / Virtual GPU Requirements

### Minimum Cloud Configuration

| Component | Requirement |
|-----------|-------------|
| OS | Ubuntu 22.04/24.04 64-bit |
| vCPU | 4+ |
| System RAM | 16 GB+ |
| GPU | NVIDIA GPU |
| GPU VRAM | 8 GB+ |
| CUDA | CUDA-compatible environment |
| Storage | 30 GB+ |
| Internet | Required |

### Recommended Cloud Configuration

| Component | Requirement |
|-----------|-------------|
| vCPU | 8+ |
| System RAM | 32 GB+ |
| GPU | NVIDIA T4 / L4 / A10 / A100 or equivalent |
| GPU VRAM | 16 GB+ preferred |
| Storage | 50–100 GB SSD |
| CUDA | CUDA-compatible environment |



# 15. External System Requirement — Tesseract

`pytesseract` is only the Python wrapper.

The **Tesseract OCR executable must also be installed** and available on `PATH` when:

```text
TESSERACT_ENABLED=true
```

Verify installation:

```powershell
tesseract --version
```

If Windows cannot find `tesseract`, install Tesseract and add its installation directory to `PATH`, then restart the terminal.

The default language is:

```text
eng
```

For multilingual Tesseract OCR, the corresponding trained-language data must also be installed and `TESSERACT_LANG` must be configured accordingly.

---

# 16. Python Dependencies

The main dependencies are defined in `requirements.txt`.

## Required core packages

```text
pypdf
pypdfium2
pytesseract
Pillow
python-dotenv
jsonschema
```

## Optional OCR packages

```text
paddleocr
paddlepaddle
```

## Optional Transformer / AI packages

```text
torch
transformers
```

The optional packages are kept in the environment for rescue/supervisor functionality but are disabled by default in the standard CPU configuration.

---

# 17. Installation — Windows PowerShell

Open PowerShell in the project root.

## Step 1 — Create virtual environment

```powershell
python -m venv venv
```

## Step 2 — Activate it

```powershell
venv\Scripts\activate
```

You should see:

```text
(venv)
```

at the beginning of the terminal prompt.

## Step 3 — Upgrade pip (optional)

```powershell
python -m pip install --upgrade pip
```

## Step 4 — Install dependencies

```powershell
python -m pip install -r requirements.txt
```

## Step 5 — Verify Python dependencies

```powershell
python -c "import pypdf, pypdfium2, pytesseract, PIL, dotenv, jsonschema; print('Core dependencies OK')"
```

## Step 6 — Verify Tesseract

```powershell
tesseract --version
```

---

# 18. Configuration

The application reads optional configuration from:

```text
.env
```

The file is optional because safe defaults are already defined in `src/config/settings.py`.

PADDLE_PIPELINE_VERSION=v1.6
PADDLE_DEVICE=cpu

UNLIMITED_OCR_ENABLED=false
UNLIMITED_OCR_MODEL=baidu/Unlimited-OCR
UNLIMITED_OCR_MODE=pipeline
MODEL_DEVICE=auto

SUPERVISOR_ENABLED=false
SUPERVISOR_MODEL=Qwen/Qwen3-VL-8B-Instruct
SUPERVISOR_ON_CONFLICT=true
SUPERVISOR_CONFIDENCE_THRESHOLD=0.90

OCR_CONFIDENCE_THRESHOLD=0.80
AMOUNT_TOLERANCE=0.01
STORE_EVIDENCE=true
LOG_LEVEL=INFO
```

Do not place secrets such as Hugging Face tokens in source files. If a token is required for an optional model, use the environment variable:

```dotenv
HF_TOKEN=your_token_here
```

---

# 20. The One Required Command

From the **project root**, run:

```powershell
python -m src.main
```

That is the documented command for processing the **entire `documents/` folder**.

The program automatically discovers every:

```text
*.pdf
```

inside:

```text
documents/
```

and writes corresponding JSON files to:

```text
output/
```

No PDF filename needs to be passed manually.

---

# 21. Single-Document Testing

For debugging one document only:

```powershell
python -m src.main --file documents\INV-01.pdf
```

The `--file` option is optional and does not replace the standard whole-folder command.

---

# 22. What Happens When the Command Runs

Example:

```powershell
python -m src.main
```

Typical flow:

```text
1. Load configuration
2. Load master data
3. Load ERP validator
4. Discover documents/*.pdf
5. Process each PDF
6. Extract native text where available
7. Render/OCR only when required
8. Classify document
9. Detect payable/non-payable
10. Group multiple payables if present
11. Extract invoice fields
12. Extract lines/taxes/discounts/charges
13. Match master data
14. Build AutoDraft
15. Run master validation
16. Run financial validation
17. Run schema validation
18. Run ERP validation
19. Save output JSON
20. Save audit JSON
21. Continue to the next PDF
```

Processing is sequential and calls garbage collection between documents to reduce memory accumulation during large batches.

---

# 23. Output Files

For an input:

```text
documents/INV-01.pdf
```

main output:

```text
output/INV-01.json
```

Audit output:

```text
output/audit/INV-01.json
```

If page evidence is enabled:

```text
output/page_images/
```

---

# 24. Output Contract

Every PDF gets a corresponding JSON result.

Example:

```json
{
  "file": "INV-01.pdf",
  "payables": [
    {
      "invoice_number": "852566",
      "invoice_date": "2026-02-02",
      "due_date": "2026-02-12",
      "invoice_type": "INVOICE",
      "currency": "EUR",
      "supplier": {
        "name": "",
        "supplier_id": "",
        "address": "",
        "vat_id": ""
      },
      "buyer": {
        "company_code": "",
        "business_unit_code": "",
        "location_code": ""
      },
      "payment_term_id": "",
      "po_number": "",
      "po_id": "",
      "gross_total": "438.00",
      "subtotal": "",
      "total_tax_amount": "0.00",
      "discount_amount": "",
      "freight_charges": "",
      "insurance_charges": "",
      "extra_charges": "",
      "excise_duties": "",
      "taxes": [],
      "line_items": []
    }
  ],
  "declined": []
}
```

The exact fields and rules are defined by `AUTODRAFT_SCHEMA.md`; that file is the authoritative schema contract.

---

# 25. Declined / Review Documents

A document is not forced into `payables[]` when the system cannot establish a safe ERP-bookable result.

The output may contain:

```json
"payables": [],
"declined": [
  {
    "doc_type": "...",
    "reason": "..."
  }
]
```

This behavior is deliberate.

For example, if a source document does not provide a reliable invoice number or deterministic validation cannot reconcile the financial structure, the system should prefer review over inventing a value.

---

# 26. How to Validate an Individual AutoDraft with ERP

The supplied `erp.py` is the booking/calculation authority.

A generated JSON can be passed to the ERP oracle according to the supplied interface. For example, if testing a payable object saved in a compatible JSON file:

```powershell
python erp.py my_payable.json
```

The ERP reports its recomputed booking gross and currency.

The key check is:

```text
ERP recomputed gross == genuine gross represented by the source document
```

within the configured tolerance.

**Do not modify `erp.py`.**

---

# 27. Performance Design

The system was intentionally redesigned for CPU/local execution.

## Optimization 1 — Native text first

Digital PDFs are parsed with `pypdf` before any image rendering.

## Optimization 2 — Lazy rendering

`pypdfium2` renders pages only when needed.

## Optimization 3 — Low-resolution triage

A small render is used to decide whether detailed OCR is necessary.

## Optimization 4 — Limited deep OCR window

The default:

```text
DEEP_PAGE_WINDOW=2
```

keeps large attachment bundles from sending every page through expensive OCR.

This setting can be increased when a particular document format requires more pages to be inspected.

## Optimization 5 — Optional heavy models

PaddleOCR, Unlimited-OCR, and Qwen/VLM processing are not automatically applied to every page.

## Optimization 6 — Sequential processing

Documents are processed one at a time and memory is reclaimed between documents.

---

# 28. Accuracy and Safety Principles

## Evidence-first extraction

Values should be grounded in the source document.

## No invented master data

If no genuine master match exists, leave the corresponding code empty.

## ERP authority

The supplied ERP is used to recompute the financial booking result.

## Structural tax correctness

The system distinguishes header-level and line-level taxes rather than matching only the final gross amount.

## Credit memo semantics

Credit memos retain `invoice_type: CREDIT_MEMO` and use the same schema.

## Review instead of guessing

Ambiguous or unsupported documents are allowed to enter review/declined output.

---

# 29. Multilingual Documents

The document set can contain multiple languages.

The application includes multilingual classification/parsing patterns and currency/country-aware logic.

For Tesseract specifically, language support depends on installed Tesseract language data.

For example, a multilingual Tesseract configuration can be supplied through `.env` when the corresponding trained-data packages are installed:

```dotenv
TESSERACT_LANG=eng+deu+fra
```

Do not configure a language code unless its Tesseract trained data is actually installed.

---

# 30. Troubleshooting

## `ModuleNotFoundError`

Activate the correct environment:

```powershell
venv\Scripts\activate
```

Then install:

```powershell
python -m pip install -r requirements.txt
```

---

## `tesseract is not recognized`

Check:

```powershell
tesseract --version
```

Install Tesseract and add its executable directory to the system `PATH`.

Restart PowerShell after changing `PATH`.

---

## OCR is too slow

Keep the CPU configuration:

```dotenv
PADDLE_ENABLED=false
UNLIMITED_OCR_ENABLED=false
SUPERVISOR_ENABLED=false
DEEP_PAGE_WINDOW=2
```

Also avoid unnecessarily increasing `OCR_DPI`.

---

## A multi-page invoice is incomplete

Increase the deep OCR window:

```dotenv
DEEP_PAGE_WINDOW=3
```

or:

```dotenv
DEEP_PAGE_WINDOW=4
```

Then rerun:

```powershell
python -m src.main
```

---

## PaddleOCR model download causes problems

PaddleOCR is optional. Keep:

```dotenv
PADDLE_ENABLED=false
```

for the lightweight local path.

---

## Transformer/VLM model consumes too much RAM

Keep:

```dotenv
SUPERVISOR_ENABLED=false
UNLIMITED_OCR_ENABLED=false
```

unless the machine has enough memory and the relevant models are available.

---

## A document goes to review

Inspect:

```text
output/<document>.json
output/audit/<document>.json
```

The audit JSON contains the validation information needed to determine whether the issue is related to:

- extraction
- classification
- master-data matching
- financial reconciliation
- schema validation
- ERP validation

Do not manually invent missing values simply to force acceptance.

---

# 31. Clean Re-run

To rerun the entire dataset cleanly, remove generated output files first if you want to avoid mixing results from different runs.

PowerShell example:

```powershell
Remove-Item -Recurse -Force output
```

Then run:

```powershell
python -m src.main
```

The application recreates the required output directories.

> Do not delete `documents/`, `master_data/`, `erp.py`, or `AUTODRAFT_SCHEMA.md` when cleaning generated output.

---

# 32. Development and Extension Points

The code is intentionally modular.

To improve OCR:

```text
src/ocr/
```

To improve document classification:

```text
src/document_understanding/classifier.py
```

To improve invoice parsing:

```text
src/extraction/
```

To improve master-data matching:

```text
src/matching/
```

To improve financial checks:

```text
src/validation/financial_validator.py
```

To improve ERP integration validation:

```text
src/validation/erp_validator.py
```

To change the AutoDraft payload construction:

```text
src/autodraft/builder.py
```

---

# 33. Important Generalization Requirement

The supplied challenge explicitly requires the solution to generalize to unseen/held-back documents.

Therefore, the implementation should not be described as a filename-specific rule engine.

The intended design is based on:

- document evidence
- multilingual patterns
- financial relationships
- master-data matching
- validation
- layout-aware extraction
- OCR fallbacks

It should **not** depend on hardcoding a particular invoice number or relying on the filename to determine the answer.

---

# 34. Validation Status

The fixed source was exercised against the provided validation documents during development.

The larger validation run was performed document-by-document using the fixed source package, with successful process execution for the tested PDFs. Some documents can legitimately finish in review/declined output when the available evidence or deterministic validation is insufficient.

This distinction is important:

```text
Successful execution
        ≠
Perfect extraction on every possible document
```

The project should therefore be presented as a robust document-processing and ERP-validation prototype rather than a claim of universal 100% invoice accuracy.

---

# 35. Deliverables

The final project contains the challenge deliverables:

```text
1. Working source code
2. requirements.txt
3. README.md
4. DESIGN.md
5. Generated JSON outputs
6. One documented command to process the entire documents/ folder
```

The required whole-folder command is:

```powershell
python -m src.main
```

---

# 36. Quick Start — Copy/Paste Version

For a fresh Windows setup:

```powershell
cd D:\projects\payable-autodraft
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
tesseract --version
python -m src.main
```

For an already configured environment:

```powershell
venv\Scripts\activate
python -m src.main
```

Then inspect:

```text
output/
```

---

# 37. Final Architecture Summary

```text
PDF documents
     │
     ▼
Native PDF extraction
     │
     ├──────────────► Good text ──────────────┐
     │                                         │
     └── Weak/scanned ─► Triage OCR ─► OCR ───┤
                                               ▼
                                    Document Understanding
                                               │
                                    Payable Detection
                                               │
                                    Multiple-Doc Splitting
                                               │
                                               ▼
                                      Structured Extraction
                                               │
                              ┌────────────────┼────────────────┐
                              ▼                ▼                ▼
                           Header          Line Items       Taxes/Charges
                              │                │                │
                              └────────────────┼────────────────┘
                                               ▼
                                      Master Data Matching
                                               │
                                               ▼
                                        AutoDraft Builder
                                               │
                                               ▼
                                  Master + Financial + Schema
                                               │
                                               ▼
                                         Supplied ERP
                                               │
                                    ┌──────────┴──────────┐
                                    ▼                     ▼
                                 ACCEPTED               REVIEW
                                    │                     │
                                    └──────────┬──────────┘
                                               ▼
                                      JSON + Audit Output
```

---

## License / Challenge Context

This repository is intended for the supplied document-to-ERP payable automation challenge and related development/testing work.

The supplied `erp.py` and `AUTODRAFT_SCHEMA.md` remain authoritative for ERP calculation and output-contract requirements.
