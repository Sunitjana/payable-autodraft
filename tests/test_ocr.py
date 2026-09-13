from src.ocr.quality_check import (
    TextQualityChecker,
    OCRResultAssessment,
)


def test_good_native_text_does_not_need_ocr():
    checker = TextQualityChecker()

    text = """
    TAX INVOICE
    Invoice Number: INV-1001
    Invoice Date: 2026-09-01
    Supplier: ABC Technologies Pvt Ltd
    Total: 1180.00
    """

    result = checker.analyze(text)

    assert result["is_usable"] is True
    assert result["needs_ocr"] is False


def test_empty_text_needs_ocr():
    checker = TextQualityChecker()

    result = checker.analyze("")

    assert result["needs_ocr"] is True


def test_ocr_result_with_good_text_is_accepted():
    checker = TextQualityChecker()

    text = """
    TAX INVOICE
    Invoice No: INV-12345
    Supplier: ABC Pvt Ltd
    Total Amount: 1000.00
    """

    result = checker.assess_ocr_result(
        text=text,
        confidence=0.95,
    )

    assert result["accepted"] is True


def test_poor_ocr_result_triggers_fallback():
    checker = TextQualityChecker()

    result = checker.should_use_fallback(
        text="INV ???",
        confidence=0.30,
    )

    assert result is True