from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .classifier import DocumentClassifier, DocumentType, ClassificationResult


@dataclass
class PayableDecision:
    is_payable: bool
    confidence: float
    document_type: DocumentType
    reason: str
    evidence: List[str] = field(default_factory=list)


class PayableDetector:
    def __init__(self, classifier: DocumentClassifier | None = None) -> None:
        self.classifier = classifier or DocumentClassifier()

    def detect(self, text: str, classification: ClassificationResult | None = None) -> PayableDecision:
        if isinstance(text, ClassificationResult) and classification is None:
            classification, text = text, ""
        classification = classification or self.classifier.classify(text or "")
        typ = classification.document_type
        if typ == DocumentType.INVOICE:
            return PayableDecision(True, classification.confidence, typ, "Invoice identity established.", classification.evidence)
        if typ == DocumentType.CREDIT_MEMO and text != "":
            return PayableDecision(True, classification.confidence, typ, "Credit memo identity established.", classification.evidence)
        if typ == DocumentType.NON_PAYABLE:
            return PayableDecision(False, classification.confidence, typ, "Document explicitly classified as non-payable.", classification.evidence)
        return PayableDecision(False, classification.confidence, DocumentType.UNKNOWN, "Payable identity could not be established safely.", classification.evidence)
