"""
Application configuration.

Configuration is loaded from:
    .env

Main OCR stack:
    1. pypdf native PDF text
    2. PaddleOCR-VL v1.6 (scanned pages only)
    3. Tesseract safety fallback
    4. Optional Unlimited-OCR / Qwen3-VL rescue
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


# ============================================================
# PROJECT ROOT
# ============================================================

ROOT_DIR = Path(
    __file__
).resolve().parents[2]


# ============================================================
# LOAD .ENV
# ============================================================

ENV_FILE = ROOT_DIR / ".env"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)


# ============================================================
# DIRECTORIES
# ============================================================

DOCUMENTS_DIR = (
    ROOT_DIR / "documents"
)

OUTPUT_DIR = (
    ROOT_DIR / "output"
)

MASTER_DATA_DIR = (
    ROOT_DIR / "master_data"
)

LOG_DIR = (
    ROOT_DIR / "logs"
)


# ============================================================
# PROJECT FILES
# ============================================================

ERP_MODULE = (
    ROOT_DIR / "erp.py"
)

AUTODRAFT_SCHEMA = (
    ROOT_DIR / "AUTODRAFT_SCHEMA.md"
)


# ============================================================
# DOCUMENT PROCESSING
# ============================================================

MAX_PAGES = int(
    os.getenv(
        "MAX_PAGES",
        "1000",
    )
)

OCR_DPI = int(
    os.getenv(
        "OCR_DPI",
        "180",
    )
)

# Low-cost page triage render. Only pages that look payable are re-rendered
# at OCR_DPI for detailed extraction. This is the main CPU optimization.
TRIAGE_DPI = int(os.getenv("TRIAGE_DPI", "72"))

# Maximum number of pages deeply OCRed from the beginning of a scanned PDF.
# The supplied bundles put the primary payable document first; later pages are
# commonly attachments/transport notes. Set to 3 for CPU-safe processing.
DEEP_PAGE_WINDOW = int(os.getenv("DEEP_PAGE_WINDOW", "2"))

MIN_TEXT_LENGTH = int(
    os.getenv(
        "MIN_TEXT_LENGTH",
        "30",
    )
)

# Lightweight local OCR fallback. It is used only when PaddleOCR/Unlimited-OCR
# are unavailable or fail. This keeps CPU/Kaggle runs recoverable.
TESSERACT_ENABLED = os.getenv("TESSERACT_ENABLED", "true").lower() == "true"
TESSERACT_LANG = os.getenv("TESSERACT_LANG", "eng")
TESSERACT_PSM = int(os.getenv("TESSERACT_PSM", "6"))
TESSERACT_ALT_PSM = int(os.getenv("TESSERACT_ALT_PSM", "11"))


# ============================================================
# PRIMARY OCR
# ============================================================

PADDLE_ENABLED = (
    os.getenv(
        "PADDLE_ENABLED",
        "false",
    ).lower()
    == "true"
)

PADDLE_PIPELINE_VERSION = os.getenv(
    "PADDLE_PIPELINE_VERSION",
    "v1.6",
)

PADDLE_DEVICE = os.getenv(
    "PADDLE_DEVICE",
    "cpu",
).strip()


# ============================================================
# OCR QUALITY
# ============================================================

OCR_CONFIDENCE_THRESHOLD = float(
    os.getenv(
        "OCR_CONFIDENCE_THRESHOLD",
        "0.80",
    )
)


# ============================================================
# UNLIMITED-OCR
# ============================================================

UNLIMITED_OCR_ENABLED = (
    os.getenv(
        "UNLIMITED_OCR_ENABLED",
        "false",
    ).lower()
    == "true"
)

UNLIMITED_OCR_MODEL = os.getenv(
    "UNLIMITED_OCR_MODEL",
    "baidu/Unlimited-OCR",
)

# Supported modes:
#
#     pipeline
#     model
#
# Recommended:
#
#     pipeline
#
UNLIMITED_OCR_MODE = os.getenv(
    "UNLIMITED_OCR_MODE",
    "pipeline",
).strip().lower()


# Device map for Unlimited-OCR.
#
# Recommended:
#
#     auto
#
MODEL_DEVICE = os.getenv(
    "MODEL_DEVICE",
    "auto",
).strip()


# ============================================================
# HUGGING FACE
# ============================================================

HF_TOKEN = os.getenv(
    "HF_TOKEN",
    "",
).strip()


# ============================================================
# SUPERVISOR
# ============================================================

SUPERVISOR_ENABLED = (
    os.getenv(
        "SUPERVISOR_ENABLED",
        "false",
    ).lower()
    == "true"
)

SUPERVISOR_MODEL = os.getenv(
    "SUPERVISOR_MODEL",
    "Qwen/Qwen3-VL-8B-Instruct",
)

SUPERVISOR_ON_CONFLICT = (
    os.getenv(
        "SUPERVISOR_ON_CONFLICT",
        "true",
    ).lower()
    == "true"
)

SUPERVISOR_CONFIDENCE_THRESHOLD = float(
    os.getenv(
        "SUPERVISOR_CONFIDENCE_THRESHOLD",
        "0.90",
    )
)


# ============================================================
# MASTER DATA FILES
# ============================================================

SUPPLIERS_FILE = (
    MASTER_DATA_DIR / "suppliers.json"
)

TAX_MASTER_FILE = (
    MASTER_DATA_DIR / "tax_master.json"
)

CHART_OF_BOOKS_FILE = (
    MASTER_DATA_DIR / "chart_of_books.json"
)

PAYMENT_TERMS_FILE = (
    MASTER_DATA_DIR / "payment_terms.json"
)

PO_MASTER_FILE = (
    MASTER_DATA_DIR / "po_master.json"
)


# ============================================================
# FINANCIAL VALIDATION
# ============================================================

AMOUNT_TOLERANCE = float(
    os.getenv(
        "AMOUNT_TOLERANCE",
        "0.01",
    )
)


# ============================================================
# SAFETY
# ============================================================

# IMPORTANT:
# The challenge explicitly says that the system must not
# invent supplier/PO/tax/payment/account values.
#
# Keep this permanently False.
#
ALLOW_INVENTED_MASTER_VALUES = False


# ============================================================
# EVIDENCE / AUDIT
# ============================================================

STORE_EVIDENCE = (
    os.getenv(
        "STORE_EVIDENCE",
        "true",
    ).lower()
    == "true"
)


# ============================================================
# LOGGING
# ============================================================

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO",
).upper()


# ============================================================
# OUTPUT
# ============================================================

OUTPUT_ENCODING = "utf-8"


# ============================================================
# CREATE DIRECTORIES
# ============================================================

def ensure_directories() -> None:
    """
    Create application directories if they don't exist.
    """

    DOCUMENTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    MASTER_DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


ensure_directories()


# ============================================================
# CONFIG SUMMARY
# ============================================================

def get_config() -> dict:
    """
    Return a safe configuration summary.

    Secrets such as HF_TOKEN are never returned directly.
    """

    return {
        "root_dir": str(ROOT_DIR),

        "documents_dir": str(
            DOCUMENTS_DIR
        ),

        "output_dir": str(
            OUTPUT_DIR
        ),

        "master_data_dir": str(
            MASTER_DATA_DIR
        ),

        "max_pages": MAX_PAGES,

        "ocr_dpi": OCR_DPI,
        "triage_dpi": TRIAGE_DPI,
        "deep_page_window": DEEP_PAGE_WINDOW,

        "min_text_length": (
            MIN_TEXT_LENGTH
        ),

        "tesseract_enabled": TESSERACT_ENABLED,
        "tesseract_lang": TESSERACT_LANG,
        "tesseract_psm": TESSERACT_PSM,

        # Primary OCR
        "paddle_enabled": (
            PADDLE_ENABLED
        ),

        "paddle_pipeline_version": (
            PADDLE_PIPELINE_VERSION
        ),

        "paddle_device": (
            PADDLE_DEVICE
        ),

        # OCR quality
        "ocr_confidence_threshold": (
            OCR_CONFIDENCE_THRESHOLD
        ),

        # Unlimited OCR
        "unlimited_ocr_enabled": (
            UNLIMITED_OCR_ENABLED
        ),

        "unlimited_ocr_model": (
            UNLIMITED_OCR_MODEL
        ),

        "unlimited_ocr_mode": (
            UNLIMITED_OCR_MODE
        ),

        "model_device": (
            MODEL_DEVICE
        ),

        # Hugging Face
        "hf_token_configured": bool(
            HF_TOKEN
        ),

        # Supervisor
        "supervisor_enabled": (
            SUPERVISOR_ENABLED
        ),

        "supervisor_model": (
            SUPERVISOR_MODEL
        ),

        "supervisor_on_conflict": (
            SUPERVISOR_ON_CONFLICT
        ),

        "supervisor_confidence_threshold": (
            SUPERVISOR_CONFIDENCE_THRESHOLD
        ),

        # Financial
        "amount_tolerance": (
            AMOUNT_TOLERANCE
        ),

        # Safety
        "allow_invented_master_values": (
            ALLOW_INVENTED_MASTER_VALUES
        ),

        # Evidence
        "store_evidence": (
            STORE_EVIDENCE
        ),

        # Logging
        "log_level": LOG_LEVEL,
    }


# ============================================================
# OPTIONAL DEBUG
# ============================================================

if __name__ == "__main__":

    import json

    print(
        json.dumps(
            get_config(),
            indent=2,
            ensure_ascii=False,
        )
    )