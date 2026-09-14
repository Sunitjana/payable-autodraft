# Payable Auto-Draft — Detailed System Design

## 1. System Overview

Payable Auto-Draft is an **automated, multi-model document-to-ERP payable automation system**.

The system accepts heterogeneous financial PDF documents and converts them into structured, ERP-validatable AutoDraft JSON. The input set can contain:

- standard invoices,
- credit memos,
- non-payable documents,
- multi-page documents,
- scanned PDFs,
- different document layouts,
- different currencies,
- multilingual documents,
- line-level and header-level taxes,
- discounts,
- freight and other additional charges,
- multiple payable documents inside one PDF.

The system is designed as a complete document-understanding pipeline rather than as a standalone OCR application.

Its architecture combines:

1. native PDF text extraction,
2. page rendering,
3. OCR,
4. OCR rescue models,
5. document classification,
6. payable detection,
7. multiple-payable grouping,
8. deterministic field extraction,
9. AI/VLM supervision for difficult cases,
10. master-data matching,
11. AutoDraft construction,
12. financial validation,
13. schema validation,
14. ERP recomputation,
15. evidence and audit generation.

The central safety principle is:

> **AI and OCR provide evidence and recovery capabilities. Deterministic validation and the supplied ERP logic remain the final authority for ERP-safe payable creation.**

---

## 2. Design Goals

The system is designed to achieve the following goals.

### 2.1 Automation

A user should be able to place PDFs into:

```text
documents/
```

and execute:

```powershell
python -m src.main
```

The application discovers and processes the documents automatically.

### 2.2 Document Generalization

The implementation should not depend on:

- a specific filename,
- a specific invoice number,
- a single invoice layout,
- a single language,
- a single currency,
- one OCR engine.

The pipeline uses document evidence, financial relationships, layout-aware processing, OCR fallbacks, master-data matching, and validation.

### 2.3 Evidence-First Processing

Extracted values should be grounded in document evidence.

When evidence is insufficient, the system should prefer:

```text
REVIEW / DECLINED
```

over inventing a value.

### 2.4 ERP Safety

A plausible OCR or AI result is not automatically considered bookable.

A payable must pass the configured validation gates before it can be accepted.

### 2.5 Resource-Aware Model Execution

The project contains multiple AI/OCR models.

The same application supports:

- a low-resource CPU-first configuration,
- a full multi-model configuration on stronger local hardware.

Heavy models can be disabled when local resources are limited without removing those models from the project architecture.

---

# 3. High-Level Architecture

```text
                         PDF INPUT DIRECTORY
                         documents/*.pdf
                                │
                                ▼
                     ┌──────────────────────┐
                     │     PDF Ingestion    │
                     │       pypdf          │
                     └──────────┬───────────┘
                                │
                     Native PDF text available?
                          │              │
                         YES            NO/WEAK
                          │              │
                          │              ▼
                          │     ┌─────────────────┐
                          │     │ Page Rendering  │
                          │     │   pypdfium2     │
                          │     └────────┬────────┘
                          │              │
                          │              ▼
                          │     ┌─────────────────┐
                          │     │ OCR Triage      │
                          │     │   Tesseract     │
                          │     └────────┬────────┘
                          │              │
                          │      Evidence sufficient?
                          │         │          │
                          │        YES        NO
                          │         │          │
                          │         │          ▼
                          │         │    ┌──────────────┐
                          │         │    │  PaddleOCR   │
                          │         │    └──────┬───────┘
                          │         │           │
                          │         │   Still insufficient?
                          │         │           │
                          │         │           ▼
                          │         │    ┌──────────────┐
                          │         │    │ Unlimited-OCR│
                          │         │    └──────┬───────┘
                          │         │           │
                          └─────────┴───────────┘
                                    │
                                    ▼
                         DOCUMENT UNDERSTANDING
                                    │
                    ┌───────────────┼────────────────┐
                    ▼               ▼                ▼
              Classification   Payable Detection   Document Split
                    │               │                │
                    └───────────────┼────────────────┘
                                    ▼
                           STRUCTURED EXTRACTION
                                    │
                  ┌─────────────────┼─────────────────┐
                  ▼                 ▼                 ▼
               Header          Line Items       Taxes/Charges
                  │                 │                 │
                  └─────────────────┼─────────────────┘
                                    ▼
                         Confidence / Consistency
                                    │
                          Critical ambiguity?
                              │            │
                             NO           YES
                              │            │
                              │            ▼
                              │    ┌───────────────────┐
                              │    │ Qwen3-VL Supervisor│
                              │    └─────────┬─────────┘
                              │              │
                              │       Advisory repair /
                              │          verification
                              │              │
                              └──────────────┘
                                    │
                                    ▼
                          MASTER-DATA MATCHING
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
           Supplier                 PO              Tax / Terms / CoB
              │                     │                     │
              └─────────────────────┼─────────────────────┘
                                    ▼
                           AUTODRAFT BUILDER
                                    │
                                    ▼
                         VALIDATION BOUNDARY
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
         Master Data            Financial               Schema
         Validation             Validation            Validation
              └─────────────────────┼─────────────────────┘
                                    ▼
                              SUPPLIED ERP
                         Recompute + Validation
                                    │
                           ┌────────┴────────┐
                           ▼                 ▼
                       ACCEPTED          REVIEW /
                        PAYABLE          DECLINED
                           │                 │
                           └────────┬────────┘
                                    ▼
                           JSON + AUDIT OUTPUT
```

---

# 4. Architectural Principles

## 4.1 Native Text Before OCR

Digitally generated PDFs often contain selectable text.

Therefore:

```text
PDF
 │
 ▼
pypdf
 │
 ├── good text → continue
 │
 └── weak/empty → visual processing
```

This prevents unnecessary rendering and OCR.

---

## 4.2 Lazy Rendering

Pages are rendered only when visual processing is required.

`pypdfium2` is used for page rendering.

The system does not render every page at maximum resolution by default.

This is particularly important for:

- long invoice bundles,
- supporting attachments,
- delivery notes,
- purchase orders,
- other non-payable pages.

---

## 4.3 OCR Cascade

OCR is implemented as a cascade.

```text
Native PDF text
      │
      ▼
Triage
      │
      ▼
Tesseract
      │
      ├── sufficient evidence → continue
      │
      └── insufficient → PaddleOCR
                              │
                              └── insufficient → Unlimited-OCR
```

The objective is not to run every OCR model on every page.

The objective is to escalate processing when the current evidence is insufficient.

---

## 4.4 AI Supervisor Is Advisory

The Qwen3-VL supervisor is not the final booking authority.

Its role is:

```text
identify difficult evidence
        ↓
inspect visual/document context
        ↓
propose or verify information
        ↓
return to deterministic validation
        ↓
ERP recomputation
```

The supervisor cannot bypass validation.

---

# 5. Runtime Modes

Payable Auto-Draft contains multiple AI/OCR models, but local hardware determines which runtime configuration is practical.

## 5.1 CPU-First / Low-Resource Mode

This mode is intended for development machines with limited resources.

Typical configuration:

```dotenv
PADDLE_ENABLED=false
UNLIMITED_OCR_ENABLED=false
SUPERVISOR_ENABLED=false
```

The pipeline can still use:

- native PDF text extraction,
- pypdfium2 rendering,
- Tesseract,
- deterministic extraction,
- master-data matching,
- financial validation,
- schema validation,
- ERP validation.

This mode minimizes model memory consumption.

---

## 5.2 Full Multi-Model Mode

On a stronger local system, the complete model stack can be enabled.

```dotenv
PADDLE_ENABLED=true
UNLIMITED_OCR_ENABLED=true
SUPERVISOR_ENABLED=true
```

Recommended additional configuration:

```dotenv
PADDLE_PIPELINE_VERSION=v1.6
PADDLE_DEVICE=cpu

UNLIMITED_OCR_MODEL=baidu/Unlimited-OCR
UNLIMITED_OCR_MODE=pipeline

MODEL_DEVICE=auto

SUPERVISOR_MODEL=Qwen/Qwen3-VL-8B-Instruct
SUPERVISOR_ON_CONFLICT=true
SUPERVISOR_CONFIDENCE_THRESHOLD=0.90
```

This configuration exposes the complete multi-model architecture.

### Important distinction

```text
false
```

does **not** mean a model is missing.

It means the model layer is disabled for the current runtime because of resource constraints.

When sufficient hardware is available:

```text
PADDLE_ENABLED=true
UNLIMITED_OCR_ENABLED=true
SUPERVISOR_ENABLED=true
```

can be used to run the full project.

---

# 6. Model and Component Responsibilities

## 6.1 pypdf

Location:

```text
src/ingestion/pdf_loader.py
```

Responsibilities:

- open PDF documents,
- extract native text,
- identify pages containing usable text,
- avoid unnecessary image OCR.

---

## 6.2 pypdfium2

Location:

```text
src/ingestion/page_processor.py
```

Responsibilities:

- render PDF pages,
- provide visual page input,
- support OCR processing,
- perform lazy rendering.

---

## 6.3 Tesseract

Location:

```text
src/ocr/extractor.py
```

Responsibilities:

- OCR scanned documents,
- process pages without usable native text,
- provide lightweight CPU OCR evidence,
- act as the initial OCR fallback.

---

## 6.4 PaddleOCR

Location:

```text
src/ocr/paddle_ocr.py
```

Responsibilities:

- stronger visual OCR,
- recovery from difficult page layouts,
- OCR rescue when Tesseract evidence is insufficient.

It is a heavy model layer and therefore should be enabled on systems with sufficient resources.

---

## 6.5 Unlimited-OCR

Location:

```text
src/ocr/unlimited_ocr.py
```

Responsibilities:

- additional OCR recovery,
- difficult scanned documents,
- difficult visual layouts,
- rescue processing after earlier OCR stages are insufficient.

---

## 6.6 Document Classifier

Location:

```text
src/document_understanding/classifier.py
```

Responsibilities:

- identify document type,
- distinguish invoices from credit memos,
- identify non-payable documents,
- route unknown documents toward review when necessary.

Possible categories include:

```text
INVOICE
CREDIT_MEMO
NON_PAYABLE
OTHER / UNKNOWN
```

---

## 6.7 Payable Detector

Location:

```text
src/document_understanding/payable_detector.py
```

Responsibilities:

- determine whether a document represents an economically payable document,
- prevent non-payable documents from being forced into the invoice schema.

Possible states:

```text
PAYABLE
NON-PAYABLE
REVIEW
```

---

## 6.8 Document Splitter

Location:

```text
src/document_understanding/document_splitter.py
```

Responsibilities:

- detect multiple payable groups,
- associate continuation pages with the correct document,
- prevent repeated invoice headers from automatically creating duplicate invoices.

---

## 6.9 Qwen3-VL Supervisor

Location:

```text
src/supervisor/qwen_vl.py
```

Supporting modules:

```text
src/supervisor/verifier.py
src/supervisor/confidence.py
```

Responsibilities:

- inspect difficult visual evidence,
- assist with ambiguous fields,
- verify critical information,
- provide advisory repairs,
- support confidence-based escalation.

The supervisor output is always passed back through deterministic validation.

---

# 7. Detailed Processing Pipeline

## Step 1 — Discover Documents

The application scans:

```text
documents/*.pdf
```

No individual filename is required for normal batch execution.

---

## Step 2 — Load PDF

`pypdf` attempts native text extraction.

The system determines whether sufficient text evidence exists.

---

## Step 3 — Render Only When Required

If native text is missing or weak:

```text
pypdfium2
```

renders the relevant page.

---

## Step 4 — Triage

A low-resolution render is used to determine whether a page is likely to contain payable information.

Typical payable indicators include:

- invoice,
- tax invoice,
- credit note,
- credit memo,
- amount due,
- total,
- VAT,
- GST,
- payment terms.

Typical non-payable indicators include:

- purchase order,
- quotation,
- delivery note,
- packing list,
- goods receipt,
- order confirmation,
- remittance advice,
- payment reminder.

---

## Step 5 — OCR

The enabled OCR stack processes candidate pages.

```text
Tesseract
   ↓
PaddleOCR
   ↓
Unlimited-OCR
```

Escalation depends on the available evidence and configuration.

---

## Step 6 — Document Classification

The classifier determines the document type.

---

## Step 7 — Payable Detection

The payable detector determines whether the document should enter payable processing.

---

## Step 8 — Multi-Payable Grouping

A single PDF can contain multiple payables.

Pages are grouped into logical payable/document units.

---

## Step 9 — Field Extraction

The extraction layer identifies:

### Header

- invoice number,
- invoice date,
- due date,
- invoice type,
- currency,
- supplier,
- VAT ID,
- PO number,
- payment terms,
- gross amount,
- subtotal,
- tax total.

### Line Items

- description,
- item type,
- quantity,
- unit of measure,
- unit price,
- line total,
- line discount,
- line tax.

### Taxes

- tax name,
- tax code,
- tax rate,
- tax amount,
- header-level tax,
- line-level tax.

### Discounts

- discount amount,
- discount percentage.

### Charges

- freight,
- insurance,
- extra charges,
- excise duties.

---

# 8. Robust Parsing

Location:

```text
src/extraction/robust_parser.py
```

The parser normalizes difficult document representations.

It handles:

- decimal separators,
- currency symbols,
- numeric OCR corruption,
- dates,
- textual dates,
- multilingual date patterns,
- malformed OCR tokens,
- credit memo values.

Financial values should be represented using safe decimal arithmetic rather than floating-point assumptions.

---

# 9. Multilingual Processing

The document set can contain multiple languages.

The system includes multilingual patterns for:

- classification,
- dates,
- currencies,
- invoice terminology,
- payable indicators.

Tesseract language support depends on the installed trained-language data.

Example:

```dotenv
TESSERACT_LANG=eng+deu+fra
```

Only languages whose Tesseract trained data is actually installed should be configured.

---

# 10. Master-Data Matching

Master data is loaded from:

```text
master_data/
├── suppliers.json
├── tax_master.json
├── chart_of_books.json
├── payment_terms.json
└── po_master.json
```

Dedicated matching modules are located under:

```text
src/matching/
```

---

## 10.1 Supplier Matching

```text
src/matching/supplier_matcher.py
```

Resolves supplier identity against the supplied supplier master.

---

## 10.2 PO Matching

```text
src/matching/po_matcher.py
```

Maps:

```text
printed/raw PO number
        ↓
master-data PO
```

The system distinguishes the document's printed PO number from the matched master-data PO identifier.

---

## 10.3 Tax Matching

```text
src/matching/tax_matcher.py
```

Matches extracted tax information against:

```text
tax_master.json
```

---

## 10.4 Payment-Term Matching

```text
src/matching/payment_terms_matcher.py
```

Matches extracted payment terms against:

```text
payment_terms.json
```

---

## 10.5 Chart-of-Books Matching

```text
src/matching/chart_of_books_matcher.py
```

Resolves accounting organization information such as:

```text
company_code
business_unit_code
location_code
```

---

# 11. Master-Data Safety Rule

The system follows a strict no-invention rule.

It must never fabricate:

- supplier IDs,
- PO IDs,
- tax codes,
- payment-term IDs,
- company codes,
- business-unit codes,
- location codes,
- other master-data identifiers.

If a genuine match cannot be established:

```text
unresolved value
       ↓
review / declined
```

rather than:

```text
guess
 ↓
ERP posting
```

This is a critical ERP safety boundary.

---

# 12. AI Supervisor and Confidence Architecture

The supervisor is triggered for difficult cases rather than being treated as the sole extraction engine.

```text
                    Extracted data
                         │
                         ▼
                 Confidence checks
                         │
              ┌──────────┴──────────┐
              │                     │
        High confidence       Low/conflicting
              │                     │
              │                     ▼
              │              Qwen3-VL
              │              Supervisor
              │                     │
              │              Verification /
              │               advisory repair
              │                     │
              └──────────┬──────────┘
                         ▼
                Deterministic checks
                         │
                         ▼
                   ERP validation
```

### Supervisor safety

The supervisor can:

- identify evidence,
- suggest a field,
- verify a field,
- resolve visual ambiguity.

The supervisor cannot:

- invent master data,
- bypass financial validation,
- bypass schema validation,
- bypass ERP validation,
- directly authorize ERP booking.

---

# 13. Financial Model

The system does not blindly trust the printed total.

Conceptually:

```text
Line Extensions
       │
       ▼
Line Discounts
       │
       ▼
Net / Subtotal
       │
       ▼
Taxes
       │
       ▼
Additional Charges
       │
       ▼
Expected Gross
       │
       ▼
Compare With Source Gross
       │
       ▼
Supplied ERP Recalculation
```

A simplified relationship is:

```text
Quantity × Unit Price
        − Discounts
        + Taxes
        + Charges
        = Gross Amount
```

The exact calculation behavior is governed by the supplied ERP implementation.

---

# 14. Tax Architecture

Tax handling distinguishes between:

```text
HEADER-LEVEL TAX
```

and:

```text
LINE-LEVEL TAX
```

This distinction is important because the same final tax amount can represent different accounting structures.

The system therefore attempts to preserve:

- tax rate,
- tax amount,
- tax code/name,
- tax location,
- associated line where applicable.

Financial validation should consider the actual tax structure rather than only comparing final gross totals.

---

# 15. Credit Memo Architecture

Credit memos use the same AutoDraft contract as invoices.

The document type is represented as:

```json
{
  "invoice_type": "CREDIT_MEMO"
}
```

The monetary representation follows the AutoDraft contract, while the document type communicates the credit nature of the document.

---

# 16. AutoDraft Construction

Location:

```text
src/autodraft/builder.py
```

The builder transforms the extracted and matched information into the required AutoDraft structure.

Example:

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

The authoritative field definitions remain in:

```text
AUTODRAFT_SCHEMA.md
```

---

# 17. Validation Boundary

Validation is deliberately separated from extraction.

The pipeline performs:

```text
              AutoDraft
                  │
      ┌───────────┼───────────┐
      ▼           ▼           ▼
 Master Data   Financial    Schema
 Validation    Validation   Validation
      └───────────┼───────────┘
                  ▼
             Supplied ERP
                  │
           ┌──────┴──────┐
           ▼             ▼
        ACCEPTED       REVIEW
```

---

## 17.1 Master Validation

Location:

```text
src/validation/master_validator.py
```

Checks whether resolved identifiers correspond to supplied master data.

---

## 17.2 Financial Validation

Location:

```text
src/validation/financial_validator.py
```

Checks whether:

- line calculations,
- discounts,
- taxes,
- charges,
- subtotal,
- gross total

are internally consistent within the configured tolerance.

Default tolerance:

```text
0.01
```

---

## 17.3 Schema Validation

Location:

```text
src/autodraft/schema_validator.py
```

Validates the generated AutoDraft against:

```text
AUTODRAFT_SCHEMA.md
```

---

## 17.4 ERP Validation

Location:

```text
src/validation/erp_validator.py
```

Loads the supplied:

```text
erp.py
```

and uses its calculation logic to validate the generated payable.

The supplied ERP implementation is not modified by the application.

---

# 18. Review and Decline Strategy

The system is designed to fail safely.

A document can enter review when:

- invoice identity is uncertain,
- payable status is uncertain,
- critical fields are missing,
- OCR engines disagree,
- AI supervision cannot resolve ambiguity,
- master data cannot be matched,
- financial values cannot be reconciled,
- schema validation fails,
- ERP validation fails.

Example:

```json
{
  "file": "document.pdf",
  "payables": [],
  "declined": [
    {
      "doc_type": "...",
      "reason": "..."
    }
  ]
}
```

The system should not fabricate missing values simply to force a document into `payables[]`.

---

# 19. Evidence and Audit

The system maintains processing evidence.

Relevant modules:

```text
src/evidence/evidence_store.py
src/evidence/page_reference.py
```

Typical output:

```text
output/
├── document.json
├── audit/
│   └── document.json
└── page_images/
    └── ...
```

Audit information can include:

- processing status,
- elapsed time,
- OCR errors,
- document classification,
- payable groups,
- validation results,
- payable count,
- supervisor information when enabled.

Rendered page images can be retained when evidence storage is enabled.

---

# 20. Performance Architecture

The project is designed to reduce unnecessary computation.

## Optimization 1 — Native Text First

Use `pypdf` before rendering.

## Optimization 2 — Lazy Rendering

Use `pypdfium2` only when visual processing is required.

## Optimization 3 — Low-DPI Triage

Use a small render to determine whether detailed processing is necessary.

## Optimization 4 — Bounded Deep OCR

Default:

```dotenv
DEEP_PAGE_WINDOW=2
```

This limits expensive OCR processing for long documents.

## Optimization 5 — Conditional Model Escalation

The system does not blindly run every model on every page.

Instead:

```text
cheap evidence
    ↓
OCR
    ↓
OCR rescue
    ↓
AI supervisor
```

is used as an escalation strategy.

## Optimization 6 — Sequential Processing

Documents are processed sequentially.

Memory is reclaimed between documents to reduce accumulation during large batches.

---

# 21. Local Hardware and Runtime Considerations

## 21.1 Basic CPU-First System

Recommended:

| Component | Requirement |
|---|---|
| CPU | Intel Core i5 / AMD Ryzen 5 or equivalent |
| CPU cores | 4+ |
| RAM | 16 GB |
| GPU | Not required |
| Storage | 30 GB+ free |
| SSD | Recommended |
| Python | 3.10–3.12 |

This configuration is intended for the CPU-first path.

---

## 21.2 Full Multi-Model Local System

Recommended:

| Component | Requirement |
|---|---|
| CPU | Intel Core i7 / AMD Ryzen 7 or better |
| CPU cores | 8+ |
| RAM | **32 GB minimum recommended** |
| GPU | **NVIDIA GPU strongly recommended** |
| GPU VRAM | **8 GB+ recommended** |
| Storage | 50 GB+ free |
| Storage | NVMe SSD recommended |
| Python | 3.10–3.12 |

### Why stronger hardware is required

Multiple OCR/AI models can require substantial:

- RAM,
- CPU time,
- GPU VRAM,
- model-loading time,
- inference time.

On a lower-end CPU-only machine, a large model may take a long time to load or process a document and can appear to be stuck.

This does not necessarily indicate a software failure.

For a full local multi-model run, a stronger system is recommended.

---

# 22. Configuration

Configuration is loaded through:

```text
src/config/settings.py
```

and:

```text
.env
```

## Full Multi-Model Configuration

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

## Low-Resource Configuration

```dotenv
PADDLE_ENABLED=false
UNLIMITED_OCR_ENABLED=false
SUPERVISOR_ENABLED=false
DEEP_PAGE_WINDOW=2
```

### Configuration rule

The project is built with all model layers available.

Use:

```text
false
```

when the machine cannot practically run the heavy model.

Use:

```text
true
```

when the machine has enough resources and the goal is to run the complete multi-model project.

---

# 23. External Tesseract Requirement

`pytesseract` is only the Python wrapper.

The Tesseract executable must also be installed and available on `PATH` when:

```dotenv
TESSERACT_ENABLED=true
```

Verification:

```powershell
tesseract --version
```

For multilingual OCR, the corresponding Tesseract language data must be installed.

---

# 24. Project Module Structure

```text
payable-autodraft/
│
├── documents/
│
├── master_data/
│   ├── suppliers.json
│   ├── tax_master.json
│   ├── chart_of_books.json
│   ├── payment_terms.json
│   └── po_master.json
│
├── output/
│   ├── *.json
│   ├── audit/
│   └── page_images/
│
├── src/
│   │
│   ├── main.py
│   │
│   ├── config/
│   │   └── settings.py
│   │
│   ├── ingestion/
│   │   ├── pdf_loader.py
│   │   └── page_processor.py
│   │
│   ├── ocr/
│   │   ├── extractor.py
│   │   ├── paddle_ocr.py
│   │   ├── unlimited_ocr.py
│   │   └── quality_check.py
│   │
│   ├── document_understanding/
│   │   ├── classifier.py
│   │   ├── payable_detector.py
│   │   └── document_splitter.py
│   │
│   ├── extraction/
│   │   ├── invoice_extractor.py
│   │   ├── line_item_extractor.py
│   │   ├── tax_extractor.py
│   │   ├── discount_extractor.py
│   │   ├── charge_extractor.py
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
├── erp.py
├── AUTODRAFT_SCHEMA.md
├── requirements.txt
├── README.md
└── DESIGN.md
```

---

# 25. End-to-End Sequence

```text
User
 │
 │ place PDFs in documents/
 ▼
src.main
 │
 ▼
Load configuration
 │
 ▼
Load master data
 │
 ▼
Load ERP validator
 │
 ▼
Discover PDFs
 │
 ├── PDF 1
 ├── PDF 2
 └── PDF N
 │
 ▼
Native text extraction
 │
 ├── sufficient ──────────────┐
 │                            │
 └── insufficient             │
          │                   │
          ▼                   │
     Page rendering           │
          │                   │
          ▼                   │
       Triage OCR             │
          │                   │
          ▼                   │
      Tesseract               │
          │                   │
          ├── sufficient ─────┤
          │                   │
          └── insufficient    │
                   │          │
                   ▼          │
               PaddleOCR      │
                   │          │
                   ▼          │
              Unlimited-OCR  │
                   │          │
                   └──────────┘
                          │
                          ▼
                  Document classifier
                          │
                          ▼
                   Payable detector
                          │
                    ┌─────┴─────┐
                    ▼           ▼
                non-payable   payable
                    │           │
                    ▼           ▼
                 decline    document split
                                │
                                ▼
                         field extraction
                                │
                                ▼
                       confidence checks
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
                 reliable              ambiguous
                    │                       │
                    │                 Qwen3-VL
                    │                 supervisor
                    │                       │
                    └───────────┬───────────┘
                                ▼
                         master matching
                                │
                                ▼
                         AutoDraft builder
                                │
                                ▼
                         master validation
                                │
                                ▼
                        financial validation
                                │
                                ▼
                         schema validation
                                │
                                ▼
                          supplied ERP
                                │
                       ┌────────┴────────┐
                       ▼                 ▼
                    accepted           review
                       │                 │
                       └────────┬────────┘
                                ▼
                           JSON output
                                │
                                ▼
                           audit output
```

---

# 26. Failure Handling

The system is designed around controlled failure rather than forced success.

## OCR failure

```text
Tesseract
   ↓
PaddleOCR
   ↓
Unlimited-OCR
   ↓
Review if evidence remains insufficient
```

## Extraction ambiguity

```text
Extraction
   ↓
Confidence check
   ↓
Qwen3-VL supervisor
   ↓
Deterministic validation
```

## Master-data mismatch

```text
No genuine master match
        ↓
Do not invent
        ↓
Review / unresolved
```

## Financial mismatch

```text
Source financial structure
        ↓
Financial validation
        ↓
Mismatch
        ↓
Review
```

## ERP failure

```text
AutoDraft
   ↓
ERP recomputation
   ↓
Failure
   ↓
Review / decline
```

---

# 27. Security and Data Integrity Principles

The system follows several data-integrity rules.

### No fabricated master data

Never guess master-data identifiers.

### No blind AI acceptance

AI output must pass deterministic validation.

### No ERP bypass

The supplied ERP remains part of the acceptance boundary.

### Evidence preservation

Audit information can be stored for later review.

### Secrets outside source code

Tokens should be supplied through environment variables.

Example:

```dotenv
HF_TOKEN=your_token_here
```

Secrets should not be hardcoded into Python source files.

---

# 28. Testing Strategy

Testing should cover the major document categories.

## Document-type tests

- standard invoice,
- credit memo,
- purchase order,
- quotation,
- delivery note,
- payment reminder,
- unknown document.

## Layout tests

- single-page invoice,
- multi-page invoice,
- scanned invoice,
- mixed native/scanned PDF,
- invoice with attachments,
- multiple invoices in one PDF.

## Financial tests

- line taxes,
- header taxes,
- multiple tax rates,
- discounts,
- freight,
- insurance,
- extra charges,
- excise duties,
- credit memos,
- currency differences.

## Matching tests

- exact supplier match,
- normalized supplier match,
- PO match,
- tax match,
- payment-term match,
- chart-of-books match,
- unresolved master data.

## AI/OCR tests

- native-text path,
- Tesseract path,
- PaddleOCR rescue,
- Unlimited-OCR rescue,
- Qwen3-VL supervisor,
- supervisor conflict handling.

---

# 29. Validation Philosophy

The system intentionally separates:

```text
Extraction
```

from:

```text
Validation
```

This separation is important because extraction can be uncertain.

For example:

```text
OCR says:
Gross = 438.00
```

does not automatically mean:

```text
ERP-safe = TRUE
```

Instead:

```text
OCR evidence
     ↓
Structured extraction
     ↓
Master matching
     ↓
Financial validation
     ↓
Schema validation
     ↓
ERP recomputation
     ↓
Final decision
```

This provides a stronger safety boundary for financial automation.

---

# 30. Performance vs Accuracy Trade-Off

There is an intentional trade-off between:

```text
runtime / memory
```

and:

```text
document recovery capability
```

### Low-resource mode

```text
Native text
   ↓
Tesseract
   ↓
Deterministic pipeline
```

Advantages:

- lower RAM usage,
- lower CPU usage,
- faster model startup,
- suitable for basic local systems.

### Full multi-model mode

```text
Native text
   ↓
Tesseract
   ↓
PaddleOCR
   ↓
Unlimited-OCR
   ↓
Qwen3-VL supervisor
   ↓
Deterministic validation
```

Advantages:

- stronger recovery capability,
- better handling of difficult visual documents,
- additional AI verification.

Trade-off:

- higher RAM usage,
- higher GPU requirements,
- longer model-loading time,
- longer inference time,
- greater local resource consumption.

---

# 31. Operational Guidance

## Low-resource machine

Use:

```dotenv
PADDLE_ENABLED=false
UNLIMITED_OCR_ENABLED=false
SUPERVISOR_ENABLED=false
```

Run:

```powershell
python -m src.main
```

## Stronger local machine

Set:

```dotenv
PADDLE_ENABLED=true
UNLIMITED_OCR_ENABLED=true
SUPERVISOR_ENABLED=true
```

Then run:

```powershell
python -m src.main
```

For a large multi-model configuration, allow sufficient time for model initialization before assuming that processing has failed.

---

# 32. Output Architecture

For:

```text
documents/INV-01.pdf
```

the application produces:

```text
output/INV-01.json
```

and:

```text
output/audit/INV-01.json
```

when audit storage is enabled.

The main result follows:

```json
{
  "file": "INV-01.pdf",
  "payables": [],
  "declined": []
}
```

Each payable object is constructed according to:

```text
AUTODRAFT_SCHEMA.md
```

---

# 33. ERP Boundary

The supplied:

```text
erp.py
```

is treated as authoritative for the supplied ERP calculation behavior.

The application does not modify the supplied ERP implementation.

The critical principle is:

```text
Document evidence
      ↓
Extracted financial structure
      ↓
AutoDraft
      ↓
ERP recomputation
      ↓
Compare
      ↓
Accept / Review
```

---

# 34. Design Summary

Payable Auto-Draft is a layered automated system rather than a single-model OCR application.

The architecture deliberately combines:

```text
PDF Parsing
    +
OCR
    +
OCR Rescue Models
    +
Document Understanding
    +
Deterministic Extraction
    +
AI/VLM Supervision
    +
Master-Data Matching
    +
Financial Validation
    +
Schema Validation
    +
ERP Recalculation
    +
Audit Evidence
```

The complete system can run in two local configurations:

```text
LOW-RESOURCE
    ↓
CPU-FIRST
    ↓
Heavy models OFF


BETTER HARDWARE
    ↓
FULL MULTI-MODEL
    ↓
PaddleOCR + Unlimited-OCR + Qwen3-VL ON
```

The heavy layers are disabled on low-resource machines for practical runtime reasons, not because those capabilities are absent from the project.

The final acceptance boundary remains:

```text
                Extracted Evidence
                       │
                       ▼
                AutoDraft Object
                       │
            ┌──────────┼──────────┐
            ▼          ▼          ▼
         Master     Financial   Schema
        Validation  Validation Validation
            └──────────┼──────────┘
                       ▼
                  Supplied ERP
                       │
                ┌──────┴──────┐
                ▼             ▼
             ACCEPTED       REVIEW
```

This design ensures that AI improves document recovery and understanding while deterministic validation protects the ERP posting boundary.

---

# 35. Authoritative Project Files

The following files remain authoritative for their respective responsibilities:

```text
AUTODRAFT_SCHEMA.md
    → AutoDraft output contract

erp.py
    → Supplied ERP calculation/recomputation logic

src/
    → Application implementation

README.md
    → Installation, configuration, usage and operational documentation

DESIGN.md
    → Technical architecture and design
```

The system should be evaluated as an automated document-to-ERP payable processing pipeline whose objective is **safe, evidence-grounded, validated payable generation**, rather than as an OCR-only application.
