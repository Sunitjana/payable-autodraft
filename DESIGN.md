# DESIGN — Payable Autodraft

## 1. Input

The application processes every PDF in `documents/`. Documents may contain
invoices, credit memos, non-payables, multiple payables, multiple pages,
scanned pages, different layouts/languages/currencies, line/header taxes,
discounts and charges.

## 2. OCR strategy

1. PyMuPDF first attempts native PDF text extraction.
2. Low-quality or image-only pages go to PP-OCRv5/PaddleOCR.
3. Low-confidence or difficult pages can go to `baidu/Unlimited-OCR`.
4. OCR is evidence generation, not final truth.

## 3. Supervisor

Qwen3-VL is an optional verification layer. It receives the original page and
candidate OCR outputs and is intended to resolve conflicts/ambiguity. It should
not override deterministic ERP/business validation.

## 4. Master-data matching

Supplier, PO, tax, payment-term and chart-of-books IDs/codes must come from the
provided master data. No value is fabricated when a genuine match cannot be
established.

## 5. ERP validation

The generated autodraft is passed to the supplied `erp.py`. The supplied ERP
logic is not modified. The expected financial relationship is:

Quantity × Unit Price − Discounts + Taxes + Charges = Gross Amount

## 6. Evidence

Page numbers and source text are preserved so a final field can be traced back
to document evidence.

## 7. Scalability

Documents are processed page-by-page rather than loading all page images into
memory/VRAM. `MAX_PAGES` is configurable and defaults to 1000.
