# Final `src` build

CPU-first Payable Auto-Draft source tree.

Key changes in this build:
- `pypdf` for native PDF text extraction (no `fitz`/PyMuPDF).
- `pypdfium2` for lazy page rendering only when OCR needs an image.
- 72-DPI triage plus higher-DPI deep OCR routing.
- Tesseract local fallback when heavyweight OCR is disabled/unavailable.
- PaddleOCR-VL, Unlimited-OCR and Qwen3-VL remain optional rescue layers.
- Deterministic extraction, master matching, financial validation, schema validation and ERP validation remain authoritative.

Validated in the working project against the supplied 20-document validation batch after the latest source fix.
