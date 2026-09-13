# Payable Autodraft — AI Document-to-ERP Pipeline

## Architecture

PDF folder
→ PyMuPDF ingestion
→ native text quality check
→ PP-OCRv5/PaddleOCR first-pass OCR
→ Unlimited-OCR fallback for difficult pages
→ document understanding
→ payable detection / multi-payable handling
→ financial extraction
→ master-data matching
→ Qwen3-VL supervisor
→ autodraft generation
→ supplied ERP validation
→ JSON output.

## Important

- `erp.py` is a challenge-supplied component and must not be modified.
- `AUTODRAFT_SCHEMA.md` is challenge-supplied and must be added before strict
  schema validation.
- Master-data JSON files belong in `master_data/`.
- OCR/VLM outputs are evidence, not ground truth.
- The system must never invent supplier/PO/tax/payment-term/account values.
- Pages are processed sequentially; `MAX_PAGES` defaults to 1000.

## Run

```bash
python run.py
```

Optional:

```bash
python run.py --input documents --output output --max-pages 1000
```

For a lightweight test without heavy models:

```bash
set PADDLE_ENABLED=false
set UNLIMITED_OCR_ENABLED=false
set SUPERVISOR_ENABLED=false
python run.py
```
