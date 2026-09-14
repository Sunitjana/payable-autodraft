# Payable Auto-Draft

## AI-Powered Document-to-ERP Payable Automation

Payable Auto-Draft is an **automated, multi-model document-to-ERP payable automation system**. It converts heterogeneous PDF financial documents into structured, ERP-validatable payable AutoDraft JSON with minimal manual intervention.

The system is designed for the supplied challenge where the input directory may contain standard invoices, credit memos, non-payable documents, multi-page documents, scanned PDFs, different layouts, currencies, languages, line/header taxes, discounts, and additional charges.

The complete project combines **PDF parsing, OCR, document understanding, deterministic extraction, multiple OCR/AI rescue models, a VLM supervisor, master-data matching, financial validation, schema validation, and ERP recomputation**. The AI models are not the sole decision makers: they provide document evidence, recognition, classification, extraction, rescue, or verification, while deterministic validation and the supplied ERP remain the final safety gates.

> **Core principle:** Payable Auto-Draft is an end-to-end document-understanding and automation system, not just an OCR system. Multiple models can work together to handle difficult documents, while deterministic rules, master-data matching, financial validation, schema validation, and ERP recomputation determine whether a payable is safe to book.

> **Runtime note:** All integrated AI/OCR models have been tested during development. However, running the full multi-model stack locally can require substantial CPU, RAM, GPU VRAM, model-loading time, and inference time. On lower-end local systems, processing can become very slow or may appear to be stuck while a model is loading or performing inference. For that reason, the heavy model layers can be disabled on low-resource machines and enabled on a stronger system to run the full project.

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

Payable Auto-Draft is designed as a **multi-stage automated pipeline**. The lightweight CPU-first path avoids unnecessary model execution, while the full configuration can activate the complete OCR/AI/VLM stack for difficult documents.

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
                              │                  │
                            Yes                 No/Weak
                              │                  │
                              │                  ▼
                              │        ┌──────────────────┐
                              │        │ Page Rendering   │
                              │        │   pypdfium2      │
                              │        └────────┬─────────┘
                              │                 │
                              │                 ▼
                              │        ┌──────────────────┐
                              │        │ OCR Triage       │
                              │        │   Tesseract      │
                              │        └────────┬─────────┘
                              │                 │
                              │          insufficient evidence?
                              │                 │
                              │          ┌──────┴──────┐
                              │          │             │
                              │         No            Yes
                              │          │             │
                              │          │             ▼
                              │          │      ┌───────────────┐
                              │          │      │ PaddleOCR      │
                              │          │      └───────┬───────┘
                              │          │              │
                              │          │       still insufficient?
                              │          │              │
                              │          │              ▼
                              │          │      ┌───────────────┐
                              │          │      │ Unlimited-OCR │
                              │          │      └───────┬───────┘
                              │          │              │
                              └──────────┴──────────────┘
                                      │
                                      ▼
                            Document Understanding
                                      │
                         ┌────────────┼─────────────┐
                         ▼            ▼             ▼
                    Classification  Payable      Multi-payable
                                   Detection       Splitting
                         │            │             │
                         └────────────┴─────────────┘
                                      │
                                      ▼
                              Structured Extraction
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
                 Header           Line Items       Taxes/Discounts/
                                                   Charges/Terms
                    │                 │                 │
                    └─────────────────┼─────────────────┘
                                      ▼
                              Confidence / Review
                                      │
                           critical field ambiguous?
                              │                  │
                             No                 Yes
                              │                  ▼
                              │        ┌────────────────────┐
                              │        │ Qwen3-VL Supervisor│
                              │        └─────────┬──────────┘
                              │                  │
                              │            Advisory repair/
                              │               verification
                              │                  │
                              └──────────────────┘
                                      │
                                      ▼
                              Master Data Matching
                                      │
                    ┌─────────────────┼────────────────────┐
                    ▼                 ▼                    ▼
                 Supplier             PO              Tax/Terms/CoB
                                      │
                                      ▼
                              AutoDraft Builder
                                      │
                                      ▼
                         Deterministic Validation
                    ┌──────────────┬──┴──────────────┐
                    ▼              ▼                 ▼
               Master Data     Financial          Schema
               Validation      Validation        Validation
                    └──────────────┬────────────────┘
                                   ▼
                             Supplied ERP
                          Recompute + Validate
                                   │
                           ┌───────┴────────┐
                           ▼                ▼
                       ACCEPTED          REVIEW/
                       payable          DECLINED
                           │                │
                           └───────┬────────┘
                                   ▼
                            JSON + Audit Output
```

### Two operating modes

**Low-resource / CPU-first mode**

- Uses native PDF text whenever possible.
- Uses lazy rendering and Tesseract only when required.
- Heavy OCR/AI/VLM layers are disabled to keep local execution practical.
- Intended for machines with limited RAM/CPU resources.

**Full multi-model mode**

- Enables the available OCR and AI rescue layers.
- Uses PaddleOCR and Unlimited-OCR when their configured routing conditions require them.
- Enables the Qwen3-VL supervisor for difficult or ambiguous cases.
- Retains deterministic validation and ERP recomputation as the final authority.
- Requires a stronger local system; **32 GB RAM minimum is recommended for multiple models, with an NVIDIA GPU strongly recommended for the full stack.**

# 3. Processing Pipeline — Step by Step

The pipeline is automated from PDF discovery through final ERP validation. Heavy models are **conditional stages**: they are available in the full system but are invoked only when enabled and when the routing logic requires them.

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

Digitally generated PDFs are handled through native text extraction first because this is substantially cheaper than rendering every page as an image.

---

## Step 3 — Lazy page rendering

### `src/ingestion/page_processor.py`

Uses `pypdfium2` only when a page needs visual processing.

Pages are rendered lazily instead of converting the entire document to high-resolution images up front. This is important for multi-page bundles containing attachments, delivery notes, or other supporting pages.

---

## Step 4 — OCR triage

### `src/ocr/extractor.py`

The OCR layer first determines whether native PDF text is sufficient.

```text
Native PDF text
      │
      ├── good evidence ───────────────► continue
      │
      └── weak/empty
              │
              ▼
        low-resolution triage
              │
              ▼
           Tesseract
              │
       sufficient evidence?
          │           │
         Yes          No
          │           │
          │           ▼
          │       PaddleOCR
          │           │
          │    sufficient evidence?
          │       │           │
          │      Yes          No
          │       │           │
          │       │           ▼
          │       │      Unlimited-OCR
          │       │           │
          └───────┴───────────┘
                    │
                    ▼
             OCR evidence
```

### OCR model layers

1. **Tesseract** — lightweight CPU OCR and the primary local OCR fallback.
2. **PaddleOCR** — stronger visual OCR for documents where the lightweight OCR path is insufficient.
3. **Unlimited-OCR** — additional rescue OCR for difficult visual documents.

These layers are configurable through `.env`. They are not required to execute on every document/page.

---

## Step 5 — Page triage

The system performs cheap triage to identify pages likely to contain a payable.

Strong payable indicators include invoice, tax invoice, credit note/memo, amount due, total amount, VAT/IVA/MWST/GST, and payment terms.

Strong non-payable indicators include purchase order, quotation, delivery note, packing list, goods receipt, order confirmation, remittance advice, timesheet, payment reminder, and Mahnung.

This prevents expensive processing from being applied unnecessarily to every page.

---

## Step 6 — Document classification

### `src/document_understanding/classifier.py`

Classifies extracted document evidence into categories such as:

- invoice
- credit memo
- non-payable
- other/unknown

A payment reminder is not automatically converted into a new payable merely because it contains a quoted invoice number or amount.

---

## Step 7 — Payable detection

### `src/document_understanding/payable_detector.py`

Determines whether the document represents an economically payable document.

Possible states:

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

The system groups pages into payable/document groups before extraction so each bookable payable can become a separate object in `payables[]`.

---

## Step 9 — Structured invoice extraction

The extraction layer converts document evidence into structured fields.

It extracts:

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
- subtotal/tax where available
- line items
- taxes
- discounts
- additional charges

The individual extraction modules are described in the next section.

---

## Step 10 — Confidence and ambiguity handling

After extraction, critical fields are checked for missing, conflicting, or ambiguous evidence.

The system does not blindly accept an AI/OCR result simply because a value was returned.

When the configured supervisor path is enabled and a critical field requires additional verification, the document can enter the AI rescue stage.

---

## Step 11 — Qwen3-VL supervisor / AI rescue

### `src/supervisor/`

```text
src/supervisor/
├── qwen_vl.py
├── verifier.py
└── confidence.py
```

The supervisor is an additional AI layer for difficult cases.

```text
Structured extraction
        │
        ▼
Critical information missing,
conflicting, or ambiguous?
        │
       Yes
        ▼
Qwen3-VL supervisor
        │
        ▼
Advisory repair / verification
        │
        ▼
Deterministic validation again
```

The supervisor can inspect the available document evidence and propose or verify difficult fields.

**Important:** the supervisor is not the final authority. Any repaired or verified values must pass the same deterministic validation, master-data validation, schema validation, and ERP validation gates.

---

## Step 12 — Master-data matching

The extracted information is matched against the supplied master data:

```text
Supplier
PO
Tax
Payment Terms
Chart of Books
```

No master-data value is invented when a genuine match cannot be established.

---

## Step 13 — AutoDraft construction

### `src/autodraft/builder.py`

Builds the final payable object according to `AUTODRAFT_SCHEMA.md`.

---

## Step 14 — Deterministic validation

The generated AutoDraft passes through:

1. Master-data validation
2. Financial consistency validation
3. JSON schema validation
4. Supplied ERP validation

AI output does not bypass these gates.

---

## Step 15 — ERP recomputation

### `src/validation/erp_validator.py`

The supplied `erp.py` is the final financial/booking authority.

The ERP recomputes the expected booking result and the pipeline compares it against the genuine financial structure represented by the document.

---

## Step 16 — Final decision

```text
                    AutoDraft
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
       Master       Financial      Schema
      Validation    Validation    Validation
          └────────────┼────────────┘
                       ▼
                  Supplied ERP
                       │
                 ┌─────┴─────┐
                 ▼           ▼
             ACCEPTED      REVIEW/
              payable     DECLINED
```

Only a safely validated result becomes an accepted payable.

---

## Step 17 — Evidence and audit output

The system saves the final JSON and processing/audit information for traceability and review.

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

# 11. AI Supervisor / Rescue Layer

The full Payable Auto-Draft architecture includes a supervisor layer for difficult or ambiguous documents.

```text
src/supervisor/
├── qwen_vl.py
├── verifier.py
└── confidence.py
```

### Role of the supervisor

The supervisor is used after the primary extraction path when critical information is:

- missing,
- ambiguous,
- conflicting,
- low-confidence, or
- difficult to recover with the configured OCR path.

The intended flow is:

```text
OCR / deterministic extraction
            │
            ▼
     Confidence checks
            │
            ▼
 Critical ambiguity detected?
       │             │
      No            Yes
       │             │
       │             ▼
       │      Qwen3-VL supervisor
       │             │
       │             ▼
       │      Advisory verification/
       │           field repair
       │             │
       └─────────────┘
             │
             ▼
   Deterministic validation
             │
             ▼
        Supplied ERP
```

### Model configuration

The full project can enable the supervisor with:

```dotenv
SUPERVISOR_ENABLED=true
SUPERVISOR_MODEL=Qwen/Qwen3-VL-8B-Instruct
SUPERVISOR_ON_CONFLICT=true
SUPERVISOR_CONFIDENCE_THRESHOLD=0.90
```

For a low-resource local machine, it can be disabled:

```dotenv
SUPERVISOR_ENABLED=false
```

Disabling it does not remove the architecture; it selects the lightweight operating mode.

### Safety boundary

The supervisor is **advisory, not authoritative**. Its output is never accepted directly into ERP booking. Repaired fields are sent back through deterministic validation and ERP recomputation.

# 12. Model and AI Layer Summary

Payable Auto-Draft is a multi-model automation system. Each model/layer has a specific responsibility.

| Layer | Component | Role | Full Mode |
|------|-----------|------|-----------|
| PDF parsing | `pypdf` | Native PDF text extraction | ON |
| Rendering | `pypdfium2` | Convert pages to visual input when required | ON |
| OCR | Tesseract | Lightweight CPU OCR / first OCR fallback | ON |
| OCR rescue | PaddleOCR | Stronger visual OCR | ON |
| OCR rescue | Unlimited-OCR | Additional difficult-document OCR rescue | ON |
| Document understanding | Classifier / payable detector / splitter | Classify documents, detect payables, split multiple payables | ON |
| Extraction | Deterministic extraction modules | Extract header, lines, taxes, discounts, charges, terms | ON |
| AI supervisor | Qwen3-VL | Verify/repair ambiguous critical information | ON |
| Matching | Master-data matchers | Supplier, PO, tax, payment terms, CoB matching | ON |
| Validation | Deterministic validators | Master, financial, schema checks | ON |
| ERP | Supplied `erp.py` | Final financial recomputation and booking validation | ON |

### Model execution principle

**Full mode:** all configured model switches are `true`.

**CPU-first mode:** heavy model switches are `false` to keep local execution practical.

In both modes, the final decision remains controlled by deterministic validation and the supplied ERP logic.

---

# 13. Evidence and Audit

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

# 14. Project Structure

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

# 15. System Requirements

Payable Auto-Draft supports two local operating modes:

1. **CPU-first / low-resource mode** — heavy AI/OCR layers are disabled.
2. **Full multi-model mode** — the complete OCR/AI/VLM stack is available and enabled.

The project is therefore not limited to a low-end CPU-only configuration. The CPU-first mode exists so the same application can still be developed and tested on a lower-resource local machine.

## 14.1 Basic Local System — CPU-First Mode

| Component | Basic Requirement |
|-----------|-------------------|
| OS | Windows 10/11 64-bit or Linux 64-bit |
| CPU | Intel Core i5 / AMD Ryzen 5 or equivalent |
| CPU Cores | 4+ |
| RAM | 16 GB |
| GPU | Not required |
| Storage | 30 GB+ free |
| SSD | Recommended |
| Python | 3.10 – 3.12 |

This configuration is intended for the lightweight path using native PDF extraction, lazy rendering, Tesseract, deterministic extraction, matching, and validation.

Heavy OCR/AI/VLM models should be disabled on this class of machine.

## 14.2 Better Local System — Full Multi-Model Mode

For running multiple OCR/AI models together and the Qwen3-VL supervisor:

| Component | Recommended Requirement |
|-----------|-------------------------|
| OS | Windows 11 64-bit / Linux 64-bit |
| CPU | Intel Core i7 / Core Ultra 7 / AMD Ryzen 7 or better |
| CPU Cores | 8+ |
| RAM | **32 GB minimum** |
| GPU | **NVIDIA GPU strongly recommended** |
| GPU VRAM | **8 GB+ recommended** |
| Storage | 50 GB+ free |
| Storage Type | NVMe SSD recommended |
| Python | 3.10 – 3.12 |

> **Important:** 32 GB RAM is the recommended minimum for the multiple-model local configuration. A suitable NVIDIA GPU is strongly recommended for the full AI/OCR/VLM stack. CPU-only execution of several large models can be very slow and may appear to stall during model loading or inference.

## 14.3 Resource Selection

| Machine | Recommended Mode | Heavy Models |
|---------|------------------|--------------|
| Basic / low-resource PC | CPU-first | OFF |
| 16 GB RAM system | CPU-first | Prefer OFF |
| 32 GB+ RAM + strong CPU | Full local | Can be ON |
| 32 GB+ RAM + NVIDIA GPU | Full multi-model | ON / Recommended |

The same project is used in both modes. Only the model switches in `.env` change the runtime configuration.

# 16. External System Requirement — Tesseract

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

# 17. Python Dependencies

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

The OCR/Transformer packages support the full multi-model configuration. Whether their runtime model stages are active is controlled through `.env`.

---

# 18. Installation — Windows PowerShell

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

# 19. Configuration

The application reads optional configuration from:

```text
.env
```

The `.env` file controls which OCR/AI layers are active.

## 18.1 Full Multi-Model Configuration

> **PRIMARY FULL-PROJECT SETTING:** When running on a sufficiently powerful local system, change the model switches in `.env` from `false` to `true`. The full project configuration is explicitly:


**For the complete project on a sufficiently powerful local system, set all model switches to `true`:**

```dotenv
PADDLE_ENABLED=true
PADDLE_PIPELINE_VERSION=v1.6
PADDLE_DEVICE=cpu

UNLIMITED_OCR_ENABLED=true
UNLIMITED_OCR_MODEL=baidu/Unlimited-OCR
UNLIMITED_OCR_MODE=pipeline
MODEL_DEVICE=auto

SUPERVISOR_ENABLED=true
SUPERVISOR_MODEL=Qwen/Qwen3-VL-8B-Instruct
SUPERVISOR_ON_CONFLICT=true
SUPERVISOR_CONFIDENCE_THRESHOLD=0.90

OCR_CONFIDENCE_THRESHOLD=0.80
AMOUNT_TOLERANCE=0.01
STORE_EVIDENCE=true
LOG_LEVEL=INFO
```

### Important `.env` rule

The project contains multiple AI/OCR layers, but **all heavy model switches should be explicitly set to `true` when the goal is to run the full project**:

```text
PADDLE_ENABLED=true
UNLIMITED_OCR_ENABLED=true
SUPERVISOR_ENABLED=true
```

This is the **full multi-model configuration**.

## 18.2 Low-Resource CPU-First Configuration

If the local machine has limited RAM/CPU resources, keep the same project but disable the heavy layers:

```dotenv
PADDLE_ENABLED=false
UNLIMITED_OCR_ENABLED=false
SUPERVISOR_ENABLED=false
DEEP_PAGE_WINDOW=2
```

The lightweight path can then run using native PDF extraction, Tesseract, deterministic processing, master-data matching, and validation.

## 18.3 Why Two Configurations Exist

The AI models are part of the full Payable Auto-Draft system. They are not being removed from the project when disabled.

The distinction is purely operational:

```text
LOW-RESOURCE MACHINE
        │
        ▼
CPU-FIRST CONFIGURATION
        │
Heavy models OFF
        │
        ▼
Faster / lower-memory local execution


BETTER MACHINE
        │
        ▼
FULL MULTI-MODEL CONFIGURATION
        │
PaddleOCR + Unlimited-OCR + Qwen3-VL ON
        │
        ▼
Maximum available document-understanding pipeline
```

This allows the same codebase to run on both development-class and high-resource local systems.

Do not place secrets such as Hugging Face tokens in source files. If a token is required for an optional model, use:

```dotenv
HF_TOKEN=your_token_here
```

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

## Optimization 5 — Conditional multi-model routing

PaddleOCR, Unlimited-OCR, and Qwen/VLM processing are available as full-project rescue/supervisor stages. Routing avoids blindly applying every model to every page, but enabling all model switches can still substantially increase runtime and resource usage.

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

## OCR / AI processing is too slow

If all model switches are `true`, the application is running the full multi-model configuration. This can be very slow on a local CPU and can consume substantial RAM/VRAM.

For a low-resource CPU-first run, temporarily use:

```dotenv
PADDLE_ENABLED=false
UNLIMITED_OCR_ENABLED=false
SUPERVISOR_ENABLED=false
DEEP_PAGE_WINDOW=2
```

Also avoid unnecessarily increasing `OCR_DPI`.

After moving to a stronger system, restore:

```dotenv
PADDLE_ENABLED=true
UNLIMITED_OCR_ENABLED=true
SUPERVISOR_ENABLED=true
```

to run the full configured project.

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

PaddleOCR is part of the full multi-model configuration. If the machine cannot support it reliably, use the CPU-first configuration:

```dotenv
PADDLE_ENABLED=false
```

Then restore `PADDLE_ENABLED=true` when running on a sufficiently capable system.

---

## Transformer/VLM model consumes too much RAM

Qwen3-VL and other heavy model stages can consume substantial memory.

For the full multi-model configuration, use a stronger system with **32 GB RAM minimum recommended** and an NVIDIA GPU strongly recommended.

For low-resource CPU-first execution:

```dotenv
SUPERVISOR_ENABLED=false
UNLIMITED_OCR_ENABLED=false
```

When sufficient resources are available, restore the full configuration:

```dotenv
SUPERVISOR_ENABLED=true
UNLIMITED_OCR_ENABLED=true
```

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

# 37. Full Project Runtime Modes

The repository contains the **complete automated Payable Auto-Draft system with multiple AI/OCR models**.

The intended usage is:

```text
                  PAYABLE AUTO-DRAFT
                         │
            ┌────────────┴────────────┐
            │                         │
      LOW-RESOURCE LOCAL        BETTER LOCAL SYSTEM
            │                         │
       Model switches OFF         All model switches ON
            │                         │
       CPU-first path          Full multi-model path
            │                         │
            └────────────┬────────────┘
                         ▼
                 Same application
                         │
                         ▼
              Validated AutoDraft JSON
```

### Full configuration — use on a better local system

Change the model switches in `.env` from `false` to `true`:

```dotenv
PADDLE_ENABLED=true
UNLIMITED_OCR_ENABLED=true
SUPERVISOR_ENABLED=true
```

This enables the full configured multi-model project. The models remain routed through their respective OCR/rescue/supervisor stages rather than blindly running on every page.

### Low-resource configuration — use when local hardware is limited

```dotenv
PADDLE_ENABLED=false
UNLIMITED_OCR_ENABLED=false
SUPERVISOR_ENABLED=false
```

The CPU-first path remains available for lower-resource local development and testing.

> **Important:** `false` means the heavy model layer is disabled for resource reasons; it does **not** mean the model or feature is missing from the project. On a sufficiently powerful local system, set all model switches to `true` to run the full project.

---

# 38. Final Architecture Summary

```text
PDF documents
     │
     ▼
Native PDF extraction
     │
     ├──────────────► Good text ──────────────┐
     │                                         │
     └── Weak/scanned ─► Triage OCR ─► Tesseract ─► OCR rescue models ───┤
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
