# src/main.py

from __future__ import annotations

"""
Payable Auto-Draft application orchestrator.

Pipeline
--------

PDF
 |
 v
PyMuPDF / PDF ingestion
 |
 v
Native text
 |
 v
Native text quality check
 |
 +---- GOOD -----------------------------+
 |                                       |
 |                                       v
 |                              Document Understanding
 |                                       |
 +---- POOR --> PaddleOCR-VL 1.6 --------+
                     |
                     v
              OCR Quality Check
                     |
              low quality?
                     |
                     v
               Unlimited-OCR
                     |
                     v
                Qwen3-VL
                Supervisor
                     |
                     v
             Field Extraction
                     |
                     v
             Master Matching
                     |
                     v
            Financial Validation
                     |
                     v
              AutoDraft Build
                     |
                     v
             Schema Validation
                     |
                     v
               ERP Validation
                     |
                     v
                  JSON

Important
---------
- erp.py is never modified.
- Master-data IDs/codes are never invented.
- gross_total is the canonical document gross.
- ERP is authoritative for booking calculation.
- OCR confidence is treated as unavailable when the OCR
  backend does not provide explicit recognition confidence.
- Qwen3-VL is a visual supervisor, not a replacement for
  deterministic validation.
"""

import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


if __package__ in (None, ""):
    sys.path.insert(
        0,
        str(Path(__file__).resolve().parents[1]),
    )


# ============================================================
# CONFIGURATION
# ============================================================

from src.config.settings import (
    DOCUMENTS_DIR,
    OUTPUT_DIR,
    MAX_PAGES,
    PADDLE_ENABLED,
    PADDLE_PIPELINE_VERSION,
    PADDLE_DEVICE,
    UNLIMITED_OCR_ENABLED,
    UNLIMITED_OCR_MODEL,
    UNLIMITED_OCR_MODE,
    MODEL_DEVICE,
    HF_TOKEN,
    SUPERVISOR_ENABLED,
    SUPERVISOR_MODEL,
    SUPERVISOR_ON_CONFLICT,
    SUPERVISOR_CONFIDENCE_THRESHOLD,
    SUPPLIERS_FILE,
    TAX_MASTER_FILE,
    CHART_OF_BOOKS_FILE,
    PAYMENT_TERMS_FILE,
    PO_MASTER_FILE,
    AUTODRAFT_SCHEMA,
    ERP_MODULE,
    AMOUNT_TOLERANCE,
    ALLOW_INVENTED_MASTER_VALUES,
    STORE_EVIDENCE,
    LOG_LEVEL,
)


# ============================================================
# PROJECT MODULES
# ============================================================

from src.ingestion.pdf_loader import PDFLoader
from src.ingestion.page_processor import PageProcessor

from src.ocr.extractor import OCRExtractor

from src.document_understanding.classifier import (
    DocumentClassifier,
)

from src.document_understanding.payable_detector import (
    PayableDetector,
)

from src.document_understanding.document_splitter import (
    DocumentSplitter,
    PageRecord as SplitterPageRecord,
)

from src.extraction.invoice_extractor import (
    InvoiceExtractor,
)

from src.extraction.line_item_extractor import (
    LineItemExtractor,
)

from src.extraction.tax_extractor import (
    TaxExtractor,
)

from src.extraction.discount_extractor import (
    DiscountExtractor,
)

from src.extraction.charge_extractor import (
    ChargeExtractor,
)

from src.matching.supplier_matcher import (
    SupplierMatcher,
)

from src.matching.po_matcher import (
    POMatcher,
)

from src.matching.tax_matcher import (
    TaxMatcher,
)

from src.matching.payment_terms_matcher import (
    PaymentTermsMatcher,
)

from src.matching.chart_of_books_matcher import (
    ChartOfBooksMatcher,
)

from src.supervisor.qwen_vl import (
    QwenVL,
)

from src.supervisor.confidence import (
    ConfidenceEngine,
    ReviewDecision,
)

from src.autodraft.builder import (
    AutoDraftBuilder,
)

from src.autodraft.schema_validator import (
    SchemaValidator,
)

from src.validation.financial_validator import (
    FinancialValidator,
)

from src.validation.master_validator import (
    MasterValidator,
)

from src.validation.erp_validator import (
    ERPValidator,
)

from src.utils.logging_utils import (
    setup_logging,
)

from src.utils.json_utils import (
    save_json,
)

from src.evidence.evidence_store import (
    EvidenceStore,
)


logger = logging.getLogger(__name__)


# ============================================================
# JSON / OBJECT HELPERS
# ============================================================

def safe_object(value: Any) -> Any:
    """
    Convert project objects into JSON-safe structures.
    """

    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if is_dataclass(value):
        return safe_object(asdict(value))

    if isinstance(value, dict):
        return {
            str(key): safe_object(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            safe_object(item)
            for item in value
        ]

    if hasattr(value, "to_dict"):
        try:
            return safe_object(
                value.to_dict()
            )
        except Exception:
            pass

    if hasattr(value, "__dict__"):
        try:
            return {
                str(key): safe_object(item)
                for key, item in vars(value).items()
                if not str(key).startswith("_")
            }
        except Exception:
            pass

    return str(value)


def get_attr(
    obj: Any,
    *names: str,
    default: Any = None,
) -> Any:
    """
    Read a value from either an object or dictionary.
    """

    if obj is None:
        return default

    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]

        return default

    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)

            if value is not None:
                return value

    return default


def validation_ok(result: Any) -> bool:
    """
    Normalize validation result objects.
    """

    if isinstance(result, bool):
        return result

    if result is None:
        return False

    if isinstance(result, dict):
        for key in (
            "valid",
            "is_valid",
            "success",
        ):
            if key in result:
                return bool(result[key])

    for attribute in (
        "valid",
        "is_valid",
        "success",
    ):
        if hasattr(result, attribute):
            value = getattr(result, attribute)

            if value is not None:
                return bool(value)

    return False


# ============================================================
# MASTER DATA
# ============================================================

def load_json_file(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(
            f"Required master-data file not found: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def load_master_data() -> Dict[str, Any]:
    logger.info("Loading master data...")

    sources = {
        "suppliers": (SUPPLIERS_FILE, "suppliers"),
        "tax_master": (TAX_MASTER_FILE, "taxes"),
        "chart_of_books": (CHART_OF_BOOKS_FILE, "companies"),
        "payment_terms": (PAYMENT_TERMS_FILE, "payment_terms"),
        "po_master": (PO_MASTER_FILE, "purchase_orders"),
    }

    data: Dict[str, Any] = {}
    for name, (path, collection_key) in sources.items():
        raw = load_json_file(path)
        if isinstance(raw, dict) and collection_key in raw:
            data[name] = raw[collection_key]
        else:
            data[name] = raw

    logger.info("Master data loaded successfully.")

    return data


# ============================================================
# DOCUMENT DISCOVERY
# ============================================================

def find_documents(
    single_file: Optional[Path] = None,
) -> List[Path]:

    if single_file is not None:

        candidate = Path(single_file)

        # Accept the path as given first (absolute, or relative
        # to the current working directory — e.g. the natural
        # "documents\INV-01.pdf" shape typed on the command line).
        resolved: Optional[Path] = None

        if candidate.exists():
            resolved = candidate.resolve()

        # Otherwise, fall back to treating it as just a filename
        # (or a documents/-relative path) inside DOCUMENTS_DIR.
        if resolved is None:

            fallback = (
                DOCUMENTS_DIR / candidate.name
            )

            if fallback.exists():
                resolved = fallback.resolve()

        if resolved is None:
            logger.warning(
                "Requested file does not exist: %s",
                candidate,
            )
            return []

        logger.info(
            "Processing single file: %s",
            resolved,
        )

        return [resolved]

    if not DOCUMENTS_DIR.exists():
        logger.warning(
            "Documents directory does not exist: %s",
            DOCUMENTS_DIR,
        )
        return []

    documents = sorted(
        DOCUMENTS_DIR.glob("*.pdf")
    )

    logger.info(
        "Found %d PDF document(s).",
        len(documents),
    )

    return documents


# ============================================================
# PAGE HELPERS
# ============================================================

def get_native_text(
    page: Any,
) -> str:
    return str(
        get_attr(
            page,
            "native_text",
            "text",
            "extracted_text",
            default="",
        )
        or ""
    )


def get_image_path(
    page: Any,
) -> Optional[Path]:

    value = get_attr(
        page,
        "image_path",
        "rendered_path",
        "path",
        "image",
    )

    if not value:
        return None

    path = Path(str(value))

    if path.exists():
        return path

    return None


def get_page_number(
    page: Any,
    default: int,
) -> int:

    value = get_attr(
        page,
        "page_number",
        "page",
        "number",
    )

    try:
        if value is not None:
            return int(value)
    except (
        TypeError,
        ValueError,
    ):
        pass

    return default


# ============================================================
# PAYABLE APPLICATION
# ============================================================

class PayableAutoDraftApp:

    def __init__(self) -> None:

        logger.info(
            "Initializing Payable Auto-Draft..."
        )

        # --------------------------------------------------------
        # Master data
        # --------------------------------------------------------

        self.master_data = (
            load_master_data()
        )

        # --------------------------------------------------------
        # Ingestion
        # --------------------------------------------------------

        self.pdf_loader = PDFLoader()
        self.page_processor = PageProcessor()

        # --------------------------------------------------------
        # OCR
        # --------------------------------------------------------

        self.ocr = OCRExtractor(
            paddle_enabled=PADDLE_ENABLED,
            paddle_pipeline_version=PADDLE_PIPELINE_VERSION,
            paddle_device=PADDLE_DEVICE,
            unlimited_ocr_enabled=UNLIMITED_OCR_ENABLED,
            unlimited_ocr_model=UNLIMITED_OCR_MODEL,
            unlimited_ocr_mode=UNLIMITED_OCR_MODE,
            model_device=MODEL_DEVICE,
            hf_token=HF_TOKEN,
        )

        # --------------------------------------------------------
        # Document understanding
        # --------------------------------------------------------

        self.classifier = DocumentClassifier()
        self.payable_detector = PayableDetector()
        self.document_splitter = DocumentSplitter()

        # --------------------------------------------------------
        # Extraction
        # --------------------------------------------------------

        self.invoice_extractor = InvoiceExtractor()
        self.line_item_extractor = LineItemExtractor()
        self.tax_extractor = TaxExtractor()
        self.discount_extractor = DiscountExtractor()
        self.charge_extractor = ChargeExtractor()

        # --------------------------------------------------------
        # Matching
        # --------------------------------------------------------

        self.supplier_matcher = SupplierMatcher(
            self.master_data["suppliers"]
        )

        self.po_matcher = POMatcher(
            self.master_data["po_master"]
        )

        self.tax_matcher = TaxMatcher(
            self.master_data["tax_master"]
        )

        self.payment_terms_matcher = (
            PaymentTermsMatcher(
                self.master_data["payment_terms"]
            )
        )

        self.chart_of_books_matcher = (
            ChartOfBooksMatcher(
                self.master_data["chart_of_books"]
            )
        )

        # --------------------------------------------------------
        # Supervisor
        # --------------------------------------------------------

        self.qwen = None

        if SUPERVISOR_ENABLED:
            self.qwen = QwenVL(
                model_name=SUPERVISOR_MODEL,
                device_map="auto",
                dtype="auto",
                max_new_tokens=1024,
            )

        # --------------------------------------------------------
        # Confidence
        # --------------------------------------------------------

        self.confidence_engine = (
            ConfidenceEngine(
                accept_threshold=0.90,
                review_threshold=0.70,
            )
        )

        # --------------------------------------------------------
        # Validation
        # --------------------------------------------------------

        self.financial_validator = (
            FinancialValidator(
                tolerance=AMOUNT_TOLERANCE
            )
        )

        self.master_validator = (
            MasterValidator(
                suppliers=self.master_data["suppliers"],
                po_master=self.master_data["po_master"],
                tax_master=self.master_data["tax_master"],
                payment_terms=self.master_data["payment_terms"],
                chart_of_books=self.master_data["chart_of_books"],
            )
        )

        self.erp_validator = ERPValidator(
            erp_path=ERP_MODULE,
            tolerance=AMOUNT_TOLERANCE,
        )

        # --------------------------------------------------------
        # AutoDraft
        # --------------------------------------------------------

        self.autodraft_builder = (
            AutoDraftBuilder(
                allow_unresolved_master_data=(
                    ALLOW_INVENTED_MASTER_VALUES
                )
            )
        )

        # --------------------------------------------------------
        # Schema
        # --------------------------------------------------------

        self.schema_validator = (
            SchemaValidator(
                schema_path=AUTODRAFT_SCHEMA
            )
        )

        # --------------------------------------------------------
        # Evidence
        # --------------------------------------------------------

        self.evidence_store = (
            EvidenceStore()
            if STORE_EVIDENCE
            else None
        )

        logger.info(
            "Payable Auto-Draft initialized."
        )

    # ========================================================
    # LOAD PDF
    # ========================================================

    # ========================================================
    # PROCESS ONE PDF
    # ========================================================

    def process_document(
        self,
        pdf_path: Path,
    ) -> Dict[str, Any]:

        logger.info(
            "Processing document: %s",
            pdf_path.name,
        )

        # ----------------------------------------------------
        # 1 & 2. PDF -> page records (native text + rendered
        # images), in one call.
        #
        # PageProcessor.process(pdf_path) opens the PDF itself,
        # renders every page to an image, and returns the full
        # list[PageRecord] for the whole document in a single
        # call — it does not take a page object and does not
        # process one page at a time. (Confirmed directly from
        # src/ingestion/page_processor.py.) PDFLoader is not
        # needed here: PageProcessor already extracts native
        # text as part of building each PageRecord.
        # ----------------------------------------------------

        try:
            page_records = self.page_processor.process(
                pdf_path
            )
        except Exception as exc:
            logger.exception(
                "PDF/page processing failed."
            )

            return self.failure(
                pdf_path,
                "PDF/page processing failed",
                str(exc),
            )

        if not page_records:
            return self.failure(
                pdf_path,
                "PDF contains no pages",
            )

        if (
            MAX_PAGES > 0
            and len(page_records) > MAX_PAGES
        ):
            page_records = page_records[:MAX_PAGES]

        # ----------------------------------------------------
        # 3. OCR
        # ----------------------------------------------------

        processed_pages: List[
            Dict[str, Any]
        ] = []

        for index, page in enumerate(
            page_records,
            start=1,
        ):

            page_number = get_page_number(
                page,
                index,
            )

            native_text = get_native_text(
                page
            )

            image_path = get_image_path(
                page
            )

            logger.info(
                "Processing OCR page %d/%d",
                index,
                len(page_records),
            )

            try:

                ocr_result = (
                    self.ocr.extract_page(
                        native_text=native_text,
                        image_path=image_path,
                        page_number=page_number,
                    )
                )

            except Exception as exc:

                logger.exception(
                    "OCR failed on page %d",
                    page_number,
                )

                ocr_result = {
                    "success": False,
                    "text": native_text,
                    "confidence": None,
                    "confidence_available": False,
                    "error": str(exc),
                }

            processed_pages.append(
                {
                    "page_number": page_number,
                    "native_text": native_text,
                    "image_path": (
                        str(image_path)
                        if image_path
                        else None
                    ),
                    "ocr": safe_object(
                        ocr_result
                    ),
                }
            )

        # ----------------------------------------------------
        # 4. Combined text
        # ----------------------------------------------------

        text_parts: List[str] = []

        for page in processed_pages:

            ocr = page.get(
                "ocr",
                {},
            )

            text = get_attr(
                ocr,
                "text",
                default="",
            )

            if text:
                text_parts.append(
                    str(text)
                )

        combined_text = (
            "\n\n".join(
                text_parts
            )
        )

        if not combined_text.strip():
            return self.failure(
                pdf_path,
                "No text could be extracted from document",
            )

        # ----------------------------------------------------
        # 5. Classification
        # ----------------------------------------------------

        try:
            classification = (
                self.classifier.classify(
                    combined_text
                )
            )
        except Exception as exc:
            return self.failure(
                pdf_path,
                "Document classification failed",
                str(exc),
            )

        document_type = get_attr(
            classification,
            "document_type",
            default=str(classification),
        )

        logger.info(
            "Document classification: %s",
            document_type,
        )

        # ----------------------------------------------------
        # 6. Payable detection
        # ----------------------------------------------------

        try:
            payable_decision = (
                self.payable_detector.detect(
                    combined_text,
                    classification,
                )
            )
        except TypeError:

            # Compatibility fallback if the implementation
            # accepts only text.
            payable_decision = (
                self.payable_detector.detect(
                    combined_text
                )
            )

        is_payable = bool(
            get_attr(
                payable_decision,
                "is_payable",
                default=False,
            )
        )

        if not is_payable:

            return {
                "success": False,
                "source_file": pdf_path.name,
                "status": "DECLINED",
                "reason": "Document is not a payable",
                "declined": [
                    {
                        "doc_type": document_type,
                        "reason": "Document is not a payable",
                    }
                ],
            }

        # ----------------------------------------------------
        # 7. Logical splitting
        # ----------------------------------------------------

        try:

            splitter_pages = [
                SplitterPageRecord(
                    page_number=page["page_number"],
                    text=get_attr(
                        page.get("ocr", {}),
                        "text",
                        default=page.get("native_text", ""),
                    ) or page.get("native_text", ""),
                    metadata={
                        "image_path": page.get("image_path"),
                        "source_pdf": str(pdf_path),
                    },
                )
                for page in processed_pages
            ]

            payable_documents = self.document_splitter.split(
                splitter_pages,
                pdf_path.stem,
            )

        except TypeError:

            # Compatibility fallback.
            try:
                payable_documents = (
                    self.document_splitter.split(
                        processed_pages
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Document splitting failed: %s",
                    exc,
                )
                payable_documents = [
                    processed_pages
                ]

        except Exception as exc:

            logger.warning(
                "Document splitting failed: %s",
                exc,
            )

            payable_documents = [
                processed_pages
            ]

        if not payable_documents:
            payable_documents = [
                processed_pages
            ]

        page_by_number = {
            page["page_number"]: page
            for page in processed_pages
        }

        normalized_documents: List[List[Dict[str, Any]]] = []
        for logical_document in payable_documents:
            if isinstance(logical_document, list):
                normalized_documents.append(logical_document)
                continue

            page_numbers = getattr(
                logical_document,
                "page_numbers",
                [],
            )
            pages_for_document = [
                page_by_number[number]
                for number in page_numbers
                if number in page_by_number
            ]
            if pages_for_document:
                normalized_documents.append(pages_for_document)

        payable_documents = normalized_documents or [processed_pages]

        # ----------------------------------------------------
        # 8. Process logical payables
        # ----------------------------------------------------

        drafts: List[
            Dict[str, Any]
        ] = []

        for document_index, logical_document in enumerate(
            payable_documents,
            start=1,
        ):

            draft = (
                self.process_logical_payable(
                    pdf_path=pdf_path,
                    pages=logical_document,
                    document_index=document_index,
                )
            )

            if draft is not None:
                drafts.append(
                    draft
                )

        if not drafts:

            return self.failure(
                pdf_path,
                "No valid payable autodraft generated",
            )

        return {
            "success": True,
            "source_file": pdf_path.name,
            "draft_count": len(drafts),
            "drafts": drafts,
        }

    # ========================================================
    # LOGICAL PAYABLE
    # ========================================================

    def process_logical_payable(
        self,
        pdf_path: Path,
        pages: List[Dict[str, Any]],
        document_index: int,
    ) -> Optional[Dict[str, Any]]:

        # ----------------------------------------------------
        # Combine text
        # ----------------------------------------------------

        text_parts: List[str] = []
        image_paths: List[str] = []

        for page in pages:

            ocr = page.get(
                "ocr",
                {},
            )

            text = get_attr(
                ocr,
                "text",
                default="",
            )

            if text:
                text_parts.append(
                    str(text)
                )

            image_path = page.get(
                "image_path"
            )

            if image_path:
                image_paths.append(
                    str(image_path)
                )

        text = "\n\n".join(
            text_parts
        )

        if not text.strip():
            return {
                "status": "DECLINED",
                "document_index": document_index,
                "reason": "No usable text",
            }

        # ----------------------------------------------------
        # Extraction
        # ----------------------------------------------------

        try:
            header = (
                self.invoice_extractor.extract(
                    text
                )
            )

            line_items = (
                self.line_item_extractor.extract(
                    text
                )
            )

            taxes = (
                self.tax_extractor.extract(
                    text
                )
            )

            discounts = (
                self.discount_extractor.extract(
                    text
                )
            )

            charges = (
                self.charge_extractor.extract(
                    text
                )
            )

        except Exception as exc:

            logger.exception(
                "Field extraction failed."
            )

            return {
                "status": "REVIEW",
                "document_index": document_index,
                "reason": "Field extraction failed",
                "error": str(exc),
            }

        # ----------------------------------------------------
        # Supervisor
        # ----------------------------------------------------

        supervisor_result = None

        if (
            SUPERVISOR_ENABLED
            and self.qwen is not None
            and image_paths
        ):

            try:

                extraction_data = {
                    "invoice": safe_object(
                        header
                    ),
                    "line_items": safe_object(
                        line_items
                    ),
                    "taxes": safe_object(
                        taxes
                    ),
                    "discounts": safe_object(
                        discounts
                    ),
                    "charges": safe_object(
                        charges
                    ),
                }

                fallback_parts: List[str] = []

                for page in pages:

                    ocr = page.get(
                        "ocr",
                        {}
                    )

                    fallback = get_attr(
                        ocr,
                        "fallback_text",
                        default="",
                    )

                    if fallback:
                        fallback_parts.append(
                            str(fallback)
                        )

                fallback_text = (
                    "\n\n".join(
                        fallback_parts
                    )
                )

                supervisor_result = (
                    self.qwen.verify(
                        image_path=image_paths[0],
                        ocr_primary=text,
                        ocr_fallback=fallback_text,
                        extracted_data=(
                            extraction_data
                        ),
                    )
                )

            except Exception as exc:

                logger.exception(
                    "Supervisor verification failed."
                )

                supervisor_result = {
                    "success": False,
                    "verified": False,
                    "overall_confidence": 0.0,
                    "error": str(exc),
                }

        # ----------------------------------------------------
        # Matching
        # ----------------------------------------------------

        header_data = safe_object(
            header
        )

        if not isinstance(
            header_data,
            dict,
        ):
            header_data = {}

        supplier_name = (
            header_data.get(
                "supplier_name"
            )
        )

        supplier_tax_id = (
            header_data.get(
                "supplier_tax_id"
            )
        )

        supplier_id = (
            header_data.get(
                "supplier_id"
            )
        )

        # Supplier
        try:

            supplier = (
                self.supplier_matcher.match(
                    supplier_name=supplier_name,
                    supplier_tax_id=supplier_tax_id,
                    supplier_id=supplier_id,
                )
            )

        except Exception as exc:

            logger.warning(
                "Supplier matching failed: %s",
                exc,
            )

            supplier = None

        # PO
        po_number = header_data.get(
            "po_number"
        )

        try:

            po = (
                self.po_matcher.match(
                    po_number=po_number,
                    supplier_id=(
                        get_attr(
                            supplier,
                            "supplier_id",
                            default=None,
                        )
                    ),
                )
            )

        except Exception as exc:

            logger.warning(
                "PO matching failed: %s",
                exc,
            )

            po = None

        # Payment terms
        payment_terms_value = (
            header_data.get(
                "payment_terms"
            )
        )

        payment_term_code = (
            header_data.get(
                "payment_term_code"
            )
        )

        try:

            payment_terms = (
                self.payment_terms_matcher.match(
                    payment_terms=payment_terms_value,
                    payment_term_code=payment_term_code,
                )
            )

        except Exception as exc:

            logger.warning(
                "Payment-term matching failed: %s",
                exc,
            )

            payment_terms = None

        # Taxes
        matched_taxes: List[Any] = []

        for tax in taxes or []:

            tax_data = safe_object(
                tax
            )

            if not isinstance(
                tax_data,
                dict,
            ):
                tax_data = {}

            tax_name = (
                tax_data.get("name")
                or tax_data.get("tax_name")
                or tax_data.get("tax_type")
            )

            tax_rate = (
                tax_data.get("rate")
                or tax_data.get("tax_rate")
            )

            tax_code = (
                tax_data.get("tax_code")
            )

            try:

                matched = (
                    self.tax_matcher.match(
                        tax_name=tax_name,
                        tax_rate=tax_rate,
                        tax_code=tax_code,
                    )
                )

                matched_taxes.append(
                    matched
                )

            except Exception as exc:

                logger.warning(
                    "Tax matching failed: %s",
                    exc,
                )

                matched_taxes.append(
                    None
                )

        # Chart of books
        account = None

        try:

            account_code = (
                header_data.get(
                    "account_code"
                )
            )

            account_name = (
                header_data.get(
                    "account_name"
                )
            )

            category = (
                header_data.get(
                    "category"
                )
            )

            account = (
                self.chart_of_books_matcher.match(
                    account_code=account_code,
                    account_name=account_name,
                    category=category,
                )
            )

        except Exception as exc:

            logger.warning(
                "Chart-of-books matching failed: %s",
                exc,
            )

        # ----------------------------------------------------
        # Master validation
        # ----------------------------------------------------

        try:

            draft_result = self.autodraft_builder.build(
                header=header,
                line_items=line_items,
                taxes=taxes,
                discounts=discounts,
                charges=charges,
                supplier_result=supplier,
                po_result=po,
                tax_results=matched_taxes,
                payment_terms_result=payment_terms,
                account_result=account,
            )

            draft_payload = get_attr(
                draft_result,
                "payload",
                default=None,
            )

            if not get_attr(draft_result, "success", default=False) or not isinstance(draft_payload, dict):
                return {
                    "status": "REVIEW",
                    "document_index": document_index,
                    "reason": "Autodraft construction failed.",
                    "errors": safe_object(get_attr(draft_result, "errors", default=[])),
                }

            master_validation = self.master_validator.validate(
                draft_payload
            )

        except Exception as exc:

            logger.warning(
                "Master validation failed: %s",
                exc,
            )

            master_validation = {
                "valid": False,
                "errors": [
                    str(exc)
                ],
            }

        master_valid = validation_ok(
            master_validation
        )

        # ----------------------------------------------------
        # Financial validation
        # ----------------------------------------------------

        gross_amount = (
            header_data.get(
                "gross_total"
            )
            or header_data.get(
                "gross_amount"
            )
            or header_data.get(
                "grand_total"
            )
        )

        try:

            financial_validation = self.financial_validator.validate(
                draft_payload
            )

        except Exception as exc:

            logger.warning(
                "Financial validation failed: %s",
                exc,
            )

            financial_validation = {
                "valid": False,
                "errors": [
                    str(exc)
                ],
            }

        financial_valid = validation_ok(
            financial_validation
        )

        # ----------------------------------------------------
        # OCR confidence
        # ----------------------------------------------------

        ocr_confidence = (
            self.average_ocr_confidence(
                pages
            )
        )

        # ----------------------------------------------------
        # Extraction confidence
        # ----------------------------------------------------

        extraction_confidence = (
            get_attr(
                header,
                "confidence",
                default=0.0,
            )
        )

        try:
            extraction_confidence = float(
                extraction_confidence
            )
        except (
            TypeError,
            ValueError,
        ):
            extraction_confidence = 0.0

        # ----------------------------------------------------
        # Supervisor confidence
        # ----------------------------------------------------

        supervisor_confidence = (
            self.supervisor_confidence(
                supervisor_result
            )
        )

        # ----------------------------------------------------
        # Conflict
        # ----------------------------------------------------

        supervisor_conflict = False

        if isinstance(
            supervisor_result,
            dict,
        ):

            conflicts = (
                supervisor_result.get(
                    "conflicts",
                    [],
                )
            )

            supervisor_conflict = bool(
                supervisor_result.get(
                    "conflict",
                    False,
                )
                or conflicts
            )

        # ----------------------------------------------------
        # Required fields
        # ----------------------------------------------------

        required_fields_present = (
            self.required_fields_present(
                header_data
            )
        )

        # ----------------------------------------------------
        # Confidence engine
        # ----------------------------------------------------

        try:

            confidence_result = (
                self.confidence_engine.calculate(
                    ocr_score=ocr_confidence,
                    extraction_score=extraction_confidence,
                    supervisor_score=supervisor_confidence,
                    master_data_score=(
                        1.0
                        if master_valid
                        else 0.0
                    ),
                    financial_score=(
                        1.0
                        if financial_valid
                        else 0.0
                    ),
                    has_unresolved_conflict=supervisor_conflict,
                    has_missing_required_field=not required_fields_present,
                )
            )

        except TypeError:

            # Compatibility with a calculate() implementation
            # accepting only the core arguments.
            confidence_result = (
                self.confidence_engine.calculate(
                    ocr_score=ocr_confidence,
                    extraction_score=extraction_confidence,
                    supervisor_score=supervisor_confidence,
                    master_data_score=(
                        1.0
                        if master_valid
                        else 0.0
                    ),
                    financial_score=(
                        1.0
                        if financial_valid
                        else 0.0
                    ),
                )
            )

        decision = get_attr(
            confidence_result,
            "decision",
            default=ReviewDecision.REVIEW,
        )

        logger.info(
            "Confidence decision: %s",
            decision,
        )

        # ----------------------------------------------------
        # Decline
        # ----------------------------------------------------

        if decision == ReviewDecision.DECLINE:

            return {
                "status": "DECLINED",
                "document_index": document_index,
                "reason": get_attr(
                    confidence_result,
                    "reason",
                    default="Confidence too low",
                ),
            }

        # ----------------------------------------------------
        # Master / financial failure
        # ----------------------------------------------------

        if not master_valid:

            return {
                "status": "REVIEW",
                "document_index": document_index,
                "reason": (
                    "Master-data validation failed."
                ),
                "master_validation": safe_object(
                    master_validation
                ),
            }

        if not financial_valid:

            return {
                "status": "REVIEW",
                "document_index": document_index,
                "reason": (
                    "Financial validation failed."
                ),
                "financial_validation": safe_object(
                    financial_validation
                ),
            }

        # ----------------------------------------------------
        # Build autodraft
        # ----------------------------------------------------

        try:

            draft_result = (
                self.autodraft_builder.build(
                    header=header,
                    line_items=line_items,
                    taxes=taxes,
                    discounts=discounts,
                    charges=charges,
                    supplier_result=supplier,
                    po_result=po,
                    tax_results=matched_taxes,
                    payment_terms_result=(
                        payment_terms
                    ),
                    account_result=account,
                    supervisor_result=(
                        safe_object(
                            supervisor_result
                        )
                    ),
                )
            )

        except Exception as exc:

            logger.exception(
                "Autodraft construction failed."
            )

            return {
                "status": "REVIEW",
                "document_index": document_index,
                "reason": (
                    "Autodraft construction failed."
                ),
                "error": str(exc),
            }

        if not get_attr(
            draft_result,
            "success",
            default=False,
        ):

            return {
                "status": "REVIEW",
                "document_index": document_index,
                "reason": (
                    "Autodraft builder rejected "
                    "the document."
                ),
                "errors": safe_object(
                    get_attr(
                        draft_result,
                        "errors",
                        default=[],
                    )
                ),
                "warnings": safe_object(
                    get_attr(
                        draft_result,
                        "warnings",
                        default=[],
                    )
                ),
            }

        draft_payload = get_attr(
            draft_result,
            "payload",
            default=None,
        )

        if not isinstance(
            draft_payload,
            dict,
        ):
            return {
                "status": "REVIEW",
                "document_index": document_index,
                "reason": (
                    "Autodraft builder did not "
                    "produce a dictionary payload."
                ),
            }

        # ----------------------------------------------------
        # Schema validation
        # ----------------------------------------------------

        try:

            schema_validation = (
                self.schema_validator.validate(
                    draft_payload
                )
            )

        except Exception as exc:

            logger.exception(
                "Schema validation failed."
            )

            return {
                "status": "REVIEW",
                "document_index": document_index,
                "reason": (
                    "Schema validation failed."
                ),
                "error": str(exc),
            }

        if not validation_ok(
            schema_validation
        ):

            return {
                "status": "REVIEW",
                "document_index": document_index,
                "reason": (
                    "Autodraft schema validation failed."
                ),
                "schema_validation": safe_object(
                    schema_validation
                ),
            }

        # ----------------------------------------------------
        # ERP validation
        # ----------------------------------------------------

        try:

            erp_validation = (
                self.erp_validator.validate(
                    draft_payload
                )
            )

        except Exception as exc:

            logger.exception(
                "ERP validation failed."
            )

            return {
                "status": "REVIEW",
                "document_index": document_index,
                "reason": (
                    "ERP validation failed."
                ),
                "error": str(exc),
            }

        if not validation_ok(
            erp_validation
        ):

            return {
                "status": "REVIEW",
                "document_index": document_index,
                "reason": (
                    "ERP gross validation failed."
                ),
                "erp_validation": safe_object(
                    erp_validation
                ),
            }

        # ----------------------------------------------------
        # Final result
        # ----------------------------------------------------

        final_status = (
            "ACCEPTED"
            if decision == ReviewDecision.ACCEPT
            else "REVIEW"
        )

        return {
            "status": final_status,
            "document_index": document_index,

            "autodraft": draft_payload,

            "validation": {
                "master": safe_object(
                    master_validation
                ),
                "financial": safe_object(
                    financial_validation
                ),
                "schema": safe_object(
                    schema_validation
                ),
                "erp": safe_object(
                    erp_validation
                ),
            },

            "supervisor": safe_object(
                supervisor_result
            ),

            "confidence": safe_object(
                confidence_result
            ),

            "builder_warnings": safe_object(
                get_attr(
                    draft_result,
                    "warnings",
                    default=[],
                )
            ),
        }

    # ========================================================
    # OCR CONFIDENCE
    # ========================================================

    @staticmethod
    def average_ocr_confidence(
        pages: List[Dict[str, Any]],
    ) -> float:

        values: List[float] = []

        for page in pages:

            ocr = page.get(
                "ocr",
                {},
            )

            confidence_available = get_attr(
                ocr,
                "confidence_available",
                default=False,
            )

            confidence = get_attr(
                ocr,
                "confidence",
                default=None,
            )

            # Never convert unavailable confidence into 0.0
            # for an actual OCR-quality score.
            if (
                confidence_available
                and confidence is not None
            ):
                try:
                    values.append(
                        float(confidence)
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    pass

        if not values:
            return 0.0

        return sum(values) / len(values)

    # ========================================================
    # SUPERVISOR CONFIDENCE
    # ========================================================

    @staticmethod
    def supervisor_confidence(
        result: Any,
    ) -> float:

        if result is None:
            return 0.0

        value = get_attr(
            result,
            "overall_confidence",
            "confidence",
            default=0.0,
        )

        try:
            return float(value)
        except (
            TypeError,
            ValueError,
        ):
            return 0.0

    # ========================================================
    # REQUIRED FIELDS
    # ========================================================

    @staticmethod
    def required_fields_present(
        header: Dict[str, Any],
    ) -> bool:

        required = (
            "invoice_number",
            "invoice_date",
            "supplier_name",
            "currency",
        )

        for field_name in required:

            value = header.get(
                field_name
            )

            if value in (
                None,
                "",
            ):
                return False

        gross = (
            header.get(
                "gross_total"
            )
            or header.get(
                "gross_amount"
            )
            or header.get(
                "grand_total"
            )
        )

        return gross not in (
            None,
            "",
        )

    # ========================================================
    # SAVE
    # ========================================================

    def save_document_result(
        self,
        pdf_path: Path,
        result: Dict[str, Any],
    ) -> Path:

        OUTPUT_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_path = (
            OUTPUT_DIR
            / f"{pdf_path.stem}.json"
        )

        payables: List[Dict[str, Any]] = []
        declined: List[Dict[str, Any]] = []

        for draft in result.get("drafts", []):
            if draft.get("status") == "ACCEPTED":
                payload = draft.get("autodraft")
                if isinstance(payload, dict):
                    payables.append(payload)
                continue

            declined.append(
                {
                    "doc_type": draft.get(
                        "doc_type",
                        "UNKNOWN",
                    ),
                    "reason": draft.get(
                        "reason",
                        "Document was not accepted for booking.",
                    ),
                }
            )

        if result.get("status") == "DECLINED" and not declined:
            declined.append(
                {
                    "doc_type": result.get(
                        "doc_type",
                        "UNKNOWN",
                    ),
                    "reason": result.get(
                        "reason",
                        "Document was declined.",
                    ),
                }
            )

        output_result = {
            "file": pdf_path.name,
            "payables": payables,
            "declined": declined,
        }

        # save_json's signature is save_json(path, data) — pass the
        # output path first and the result dict second.
        save_json(
            output_path,
            output_result,
        )

        logger.info(
            "Output written: %s",
            output_path,
        )

        return output_path

    # ========================================================
    # RUN ALL
    # ========================================================

    def run(
        self,
        single_file: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:

        documents = find_documents(
            single_file=single_file
        )

        if not documents:
            logger.warning(
                "No PDF files found in %s",
                DOCUMENTS_DIR,
            )
            return []

        results: List[
            Dict[str, Any]
        ] = []

        for pdf_path in documents:

            try:

                result = (
                    self.process_document(
                        pdf_path
                    )
                )

            except KeyboardInterrupt:
                raise

            except Exception as exc:

                logger.exception(
                    "Unexpected error processing %s",
                    pdf_path.name,
                )

                result = self.failure(
                    pdf_path,
                    "Unexpected processing error",
                    str(exc),
                )

            self.save_document_result(
                pdf_path,
                result,
            )

            results.append(
                result
            )

        return results

    # ========================================================
    # FAILURE
    # ========================================================

    @staticmethod
    def failure(
        pdf_path: Path,
        reason: str,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:

        result: Dict[str, Any] = {
            "success": False,
            "source_file": pdf_path.name,
            "status": "DECLINED",
            "reason": reason,
        }

        if error:
            result["error"] = error

        return result


# ============================================================
# CLI
# ============================================================

def parse_args(argv: Optional[List[str]] = None):

    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Payable Auto-Draft: convert PDF financial "
            "documents into ERP-bookable payable autodrafts."
        )
    )

    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help=(
            "Process only this single PDF instead of every "
            "PDF in the documents/ folder. Accepts an absolute "
            "path or a path relative to the documents/ folder "
            "(e.g. --file documents/INV-01.pdf or "
            "--file INV-01.pdf)."
        ),
    )

    return parser.parse_args(argv)


def main() -> int:

    args = parse_args()

    try:
        try:
            setup_logging(
                level=LOG_LEVEL
            )
        except TypeError:
            setup_logging()

        logger.info(
            "=" * 70
        )

        logger.info(
            "PAYABLE AUTO-DRAFT"
        )

        logger.info(
            "=" * 70
        )

        logger.info(
            "Documents : %s",
            DOCUMENTS_DIR,
        )

        logger.info(
            "Output    : %s",
            OUTPUT_DIR,
        )

        logger.info(
            "PaddleOCR : %s",
            PADDLE_PIPELINE_VERSION,
        )

        logger.info(
            "Unlimited : %s",
            UNLIMITED_OCR_MODEL,
        )

        logger.info(
            "Supervisor: %s",
            SUPERVISOR_MODEL,
        )

        single_file = (
            Path(args.file)
            if args.file
            else None
        )

        if single_file is not None:
            logger.info(
                "Single-file mode: %s",
                single_file,
            )

        app = PayableAutoDraftApp()

        results = app.run(
            single_file=single_file
        )

    except KeyboardInterrupt:

        logger.warning(
            "Processing interrupted by user."
        )

        return 130

    except Exception as exc:

        logger.exception(
            "Application failed."
        )

        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )

        return 1

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    total_documents = len(
        results
    )

    accepted = 0
    review = 0
    declined = 0

    for result in results:

        drafts = result.get(
            "drafts",
            [],
        )

        if not drafts:

            if result.get(
                "status"
            ) == "DECLINED":
                declined += 1

            continue

        for draft in drafts:

            status = draft.get(
                "status"
            )

            if status == "ACCEPTED":
                accepted += 1

            elif status == "REVIEW":
                review += 1

            elif status == "DECLINED":
                declined += 1

    logger.info(
        "=" * 70
    )

    logger.info(
        "PROCESSING COMPLETE"
    )

    logger.info(
        "Documents processed: %d",
        total_documents,
    )

    logger.info(
        "Accepted: %d",
        accepted,
    )

    logger.info(
        "Review: %d",
        review,
    )

    logger.info(
        "Declined: %d",
        declined,
    )

    logger.info(
        "Output directory: %s",
        OUTPUT_DIR,
    )

    logger.info(
        "=" * 70
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )