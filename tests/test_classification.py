from src.document_understanding.classifier import (
    DocumentClassifier,
    DocumentType,
)

from src.document_understanding.payable_detector import (
    PayableDetector,
)


def test_invoice_classification():
    classifier = DocumentClassifier()

    text = """
    TAX INVOICE

    Invoice Number: INV-1001
    Invoice Date: 2026-09-01

    Supplier: ABC Technologies

    Subtotal: 1000.00
    GST: 180.00
    Total Amount: 1180.00
    """

    result = classifier.classify(text)

    assert result.document_type == DocumentType.INVOICE


def test_credit_memo_classification():
    classifier = DocumentClassifier()

    text = """
    CREDIT MEMO

    Credit Note Number: CN-1001
    Supplier: ABC Technologies
    Amount: 500.00
    """

    result = classifier.classify(text)

    assert result.document_type == DocumentType.CREDIT_MEMO


def test_non_payable_document():
    classifier = DocumentClassifier()

    text = """
    PURCHASE ORDER

    PO Number: PO-1001
    Supplier: ABC Technologies
    """

    result = classifier.classify(text)

    assert result.document_type == DocumentType.NON_PAYABLE


def test_invoice_is_payable():
    classifier = DocumentClassifier()
    detector = PayableDetector()

    text = """
    TAX INVOICE
    Invoice Number: INV-1001
    Total: 1180.00
    """

    classification = classifier.classify(text)
    decision = detector.detect(
        classification
    )

    assert decision.is_payable is True


def test_credit_memo_is_not_normal_payable():
    classifier = DocumentClassifier()
    detector = PayableDetector()

    text = """
    CREDIT MEMO
    Credit Note: CN-1001
    Amount: 500.00
    """

    classification = classifier.classify(text)
    decision = detector.detect(
        classification
    )

    assert decision.is_payable is False