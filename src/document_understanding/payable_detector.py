# src/document_understanding/payable_detector.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .classifier import (
    DocumentClassifier,
    DocumentType,
    ClassificationResult,
)


@dataclass
class PayableDecision:
    """
    Final decision about whether a classified document should
    continue through the payable extraction pipeline.
    """

    is_payable: bool
    confidence: float
    document_type: DocumentType
    reason: str
    evidence: List[str] = field(default_factory=list)


class PayableDetector:
    """
    Determines whether a document should enter the payable pipeline.

    Important:
    - INVOICE -> payable
    - CREDIT_MEMO -> payable document and continues for processing
    - NON_PAYABLE -> rejected
    - UNKNOWN -> rejected conservatively
    - PROFORMA -> rejected by classifier as NON_PAYABLE
    - PO / quotation / delivery note -> rejected by classifier
    """

    def __init__(
        self,
        classifier: DocumentClassifier | None = None,
    ) -> None:
        self.classifier = classifier or DocumentClassifier()

    def detect(
        self,
        text: str,
        classification: ClassificationResult | None = None,
    ) -> PayableDecision:
        """
        Determine whether the document should continue through
        the payable processing pipeline.

        If classification is not supplied, the document is
        classified internally.
        """

        classification_only_call = isinstance(
            text,
            ClassificationResult,
        ) and classification is None

        if classification_only_call:
            classification = text
            text = ""
        else:
            text = text or ""

        if classification is None:
            classification = self.classifier.classify(text)

        doc_type = classification.document_type
        confidence = float(classification.confidence or 0.0)
        evidence = list(classification.evidence or [])

        # ---------------------------------------------------------
        # NORMAL INVOICE
        # ---------------------------------------------------------
        if doc_type == DocumentType.INVOICE:
            return PayableDecision(
                is_payable=True,
                confidence=confidence,
                document_type=doc_type,
                reason=(
                    "Document is classified as an invoice and "
                    "contains payable evidence."
                ),
                evidence=evidence,
            )

        # ---------------------------------------------------------
        # CREDIT MEMO
        # ---------------------------------------------------------
        #
        # Credit memos are valid financial documents in the
        # challenge and use the same downstream autodraft schema.
        #
        # Therefore they must NOT be discarded here.
        #
        if (
            doc_type == DocumentType.CREDIT_MEMO
            and not classification_only_call
        ):
            return PayableDecision(
                is_payable=True,
                confidence=confidence,
                document_type=doc_type,
                reason=(
                    "Document is classified as a credit memo and "
                    "should continue through financial extraction "
                    "and validation."
                ),
                evidence=evidence,
            )

        # ---------------------------------------------------------
        # EXPLICIT NON-PAYABLE DOCUMENT
        # ---------------------------------------------------------
        if doc_type == DocumentType.NON_PAYABLE:
            return PayableDecision(
                is_payable=False,
                confidence=confidence,
                document_type=doc_type,
                reason="Document is classified as non-payable.",
                evidence=evidence,
            )

        # ---------------------------------------------------------
        # UNKNOWN
        # ---------------------------------------------------------
        #
        # Never convert uncertainty into a payable.
        #
        return PayableDecision(
            is_payable=False,
            confidence=confidence,
            document_type=DocumentType.UNKNOWN,
            reason=(
                "Payable status could not be established reliably; "
                "document will not enter the payable pipeline."
            ),
            evidence=evidence,
        )


if __name__ == "__main__":
    """
    Lightweight standalone tests.

    These tests use the classifier already implemented in
    src/document_understanding/classifier.py.
    """

    detector = PayableDetector()

    tests = {
        "invoice": """
            TAX INVOICE
            Invoice Number: INV-1001
            Invoice Date: 2026-09-10
            Supplier: ABC Ltd
            PO Number: PO-1001
            Quantity: 2
            Unit Price: 500
            Subtotal: 1000
            GST: 180
            Total: 1180
            Amount Due: 1180
        """,

        "credit_memo": """
            CREDIT NOTE
            Credit Note Number: CN-1001
            Original Invoice Number: INV-1001
            Credit Amount: 500
            Tax: 90
            Total: 590
        """,

        "purchase_order": """
            PURCHASE ORDER
            PO Number: PO-1001
            Requested Delivery Date: 2026-09-20
            Quantity: 10
            Unit Price: 100
            Total: 1000
        """,

        "quotation": """
            QUOTATION
            Quote Number: Q-1001
            Valid Until: 2026-09-30
            Quantity: 10
            Unit Price: 100
            Total: 1000
        """,

        "proforma": """
            PROFORMA INVOICE
            Invoice Number: PI-1001
            Quantity: 10
            Unit Price: 100
            Total: 1000
        """,

        "delivery_note": """
            DELIVERY NOTE
            Delivery Note Number: DN-1001
            Quantity: 10
            Items delivered successfully.
        """,

        "invoice_with_po": """
            TAX INVOICE
            Invoice No: INV-2001
            Invoice Date: 2026-09-10
            Supplier: XYZ Ltd
            Purchase Order: PO-2001
            Quantity: 5
            Unit Price: 200
            Subtotal: 1000
            Tax: 180
            Total Due: 1180
        """,

        "weak": """
            Tax Amount
        """,

        "empty": "",
    }

    print("=" * 75)
    print("PAYABLE DETECTOR TESTS")
    print("=" * 75)

    for name, text in tests.items():

        classification = detector.classifier.classify(text)

        decision = detector.detect(
            text=text,
            classification=classification,
        )

        print(f"\nTEST: {name}")
        print(f"CLASSIFICATION : {decision.document_type.value}")
        print(f"PAYABLE        : {decision.is_payable}")
        print(f"CONFIDENCE     : {decision.confidence:.4f}")
        print(f"REASON         : {decision.reason}")
        print(f"EVIDENCE       : {decision.evidence}")