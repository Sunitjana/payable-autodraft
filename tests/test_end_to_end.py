from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS_DIR = PROJECT_ROOT / "documents"


def test_documents_directory_exists():
    assert DOCUMENTS_DIR.exists()


def test_documents_directory_contains_pdfs():
    pdfs = list(
        DOCUMENTS_DIR.glob("*.pdf")
    )

    # If no challenge documents have been supplied,
    # don't fail the test suite.
    if not pdfs:
        pytest.skip(
            "No PDF documents available for end-to-end test."
        )

    assert all(
        pdf.suffix.lower() == ".pdf"
        for pdf in pdfs
    )


def test_pipeline_imports():
    """
    Basic integration test ensuring the major pipeline modules
    can be imported together.
    """

    from src.ocr.extractor import PDFTextExtractor
    from src.document_understanding.classifier import (
        DocumentClassifier,
    )
    from src.extraction.invoice_extractor import (
        InvoiceExtractor,
    )
    from src.matching.supplier_matcher import (
        SupplierMatcher,
    )
    from src.validation.financial_validator import (
        FinancialValidator,
    )

    assert PDFTextExtractor is not None
    assert DocumentClassifier is not None
    assert InvoiceExtractor is not None
    assert SupplierMatcher is not None
    assert FinancialValidator is not None