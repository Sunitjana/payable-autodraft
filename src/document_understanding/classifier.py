from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
import unicodedata
from typing import Dict, List


class DocumentType(str, Enum):
    INVOICE = "invoice"
    CREDIT_MEMO = "credit_memo"
    NON_PAYABLE = "non_payable"
    UNKNOWN = "unknown"


@dataclass
class ClassificationResult:
    document_type: DocumentType
    confidence: float
    evidence: List[str] = field(default_factory=list)
    scores: Dict[str, float] = field(default_factory=dict)

    @property
    def is_payable(self) -> bool:
        return self.document_type in {DocumentType.INVOICE, DocumentType.CREDIT_MEMO}


class DocumentClassifier:
    """Fast multilingual rule-based classifier.

    It is intentionally conservative: explicit non-payable documents win over
    generic financial words, while invoice/credit-note identity terms are
    required before a document enters the payable path.
    """

    INVOICE_TERMS = {
        "invoice": 4.0, "invoi": 3.5, "tax invoice": 5.0, "invoice number": 4.0,
        "invoice no": 4.0, "invoice date": 3.0, "amount due": 4.0,
        "total due": 4.0, "amount payable": 4.0, "rechnun": 5.0,
        "rechnung": 5.0, "rechnungsdatum": 4.0, "endbetrag": 4.0,
        "arve": 4.0, "arve number": 5.0, "arve kuupaev": 4.0,
        "arve kokku": 5.0, "fatura": 5.0, "factura": 5.0, "fctura": 5.0,
        "total da factura": 5.0, "data de vencimento": 3.0,
        "copy tax invoice": 5.0, "invoice nr": 4.0,
    }

    CREDIT_TERMS = {
        "credit memo": 7.0, "credit note": 7.0, "credit note no": 7.0,
        "credit note number": 7.0, "credit invoice": 6.0,
        "credit adjustment": 5.0, "credit amount": 5.0,
        "kreeditarve": 8.0, "kreditnote": 7.0, "creditnota": 7.0,
    }

    NONPAYABLE_TERMS = {
        "purchase order": 8.0, "purchase requisition": 8.0,
        "quotation": 8.0, "quote": 6.0, "proforma invoice": 10.0,
        "delivery note": 8.0, "delivery receipt": 8.0,
        "packing slip": 8.0, "packing list": 8.0, "goods receipt": 8.0,
        "order confirmation": 7.0, "shipping notice": 7.0,
        "remittance advice": 8.0, "timesheet": 7.0, "expense report": 7.0,
        "donations and charitable contributions": 12.0,
        "charitable contributions": 10.0, "sponsorship": 8.0,
        "contribution": 7.0, "donation": 7.0, "mahnung": 10.0,
        "payment reminder": 10.0, "reminder": 7.0,
    }

    SUPPORT_TERMS = {
        "subtotal": 1.5, "total": 1.0, "tax": 1.0, "vat": 1.0,
        "iva": 1.0, "gst": 1.0, "quantity": 1.0, "qty": 1.0,
        "unit price": 1.5, "payment terms": 1.5, "due date": 1.5,
        "currency": 1.0, "discount": 1.0, "freight": 1.0,
        "gross total": 2.0, "net amount": 1.5, "kogusumma": 2.0,
        "maksetahtaeg": 2.0, "iva": 1.0, "subtotal": 1.0,
    }

    @staticmethod
    def _norm(text: str) -> str:
        text = unicodedata.normalize("NFKD", text or "")
        text = "".join(c for c in text if not unicodedata.combining(c))
        text = text.casefold().replace("€", " eur ").replace("£", " gbp ")
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _hit(text: str, term: str) -> bool:
        # OCR frequently drops punctuation; substring matching is more robust
        # for multi-word identity phrases than strict token matching.
        return term in text

    def classify(self, text: str) -> ClassificationResult:
        text_n = self._norm(text)
        if not text_n:
            return ClassificationResult(DocumentType.UNKNOWN, 0.0, [], {"invoice": 0.0, "credit_memo": 0.0, "non_payable": 0.0})

        invoice_score = 0.0
        credit_score = 0.0
        nonpay_score = 0.0
        evidence: list[str] = []

        for term, weight in self.INVOICE_TERMS.items():
            if self._hit(text_n, term):
                invoice_score += weight
                evidence.append(term)

        for term, weight in self.CREDIT_TERMS.items():
            if self._hit(text_n, term):
                credit_score += weight
                evidence.append(term)

        # Some documents legitimately contain references such as "delivery
        # note: 8358769" inside an invoice. Treat these terms as a document
        # identity only when they occur as a header/standalone phrase near the
        # top; otherwise they must not override invoice evidence.
        top_lines = [self._norm(x) for x in (text or "").splitlines()[:12] if x.strip()]
        top_blob = " ".join(top_lines)
        header_only_terms = {
            "delivery note", "delivery receipt", "packing slip", "packing list",
            "shipment notice", "shipping notice", "shipment cartage advice",
            "cartage advice", "mahnung", "payment reminder", "remittance advice",
        }
        for term, weight in self.NONPAYABLE_TERMS.items():
            if not self._hit(text_n, term):
                continue
            if term in header_only_terms and not self._hit(top_blob, term):
                continue
            nonpay_score += weight
            evidence.append(term)

        support = sum(weight for term, weight in self.SUPPORT_TERMS.items() if self._hit(text_n, term))
        invoice_score += min(support, 7.0)
        credit_score += min(support * 0.5, 4.0)

        # Explicit reminder/collection notices are never payable invoices,
        # even when the body quotes an underlying `Rechnung`/invoice.
        # This is a document-level identity marker, not a financial keyword.
        if any(self._hit(text_n, term) for term in ("mahnung", "payment reminder", "reminder")):
            conf = min(0.99, 0.72 + max(nonpay_score, 10.0) / 30.0)
            return ClassificationResult(DocumentType.NON_PAYABLE, conf, evidence, {
                "invoice": invoice_score, "credit_memo": credit_score,
                "non_payable": nonpay_score, "financial": support,
            })

        # Strong explicit non-payable identity beats generic invoice words.
        if nonpay_score >= 8.0 and nonpay_score >= invoice_score:
            conf = min(0.99, 0.65 + nonpay_score / 30.0)
            return ClassificationResult(DocumentType.NON_PAYABLE, conf, evidence, {
                "invoice": invoice_score, "credit_memo": credit_score,
                "non_payable": nonpay_score, "financial": support,
            })

        # Credit identity wins when explicit.
        if credit_score >= 7.0 and credit_score >= invoice_score * 0.70:
            conf = min(0.99, 0.65 + credit_score / 30.0)
            return ClassificationResult(DocumentType.CREDIT_MEMO, conf, evidence, {
                "invoice": invoice_score, "credit_memo": credit_score,
                "non_payable": nonpay_score, "financial": support,
            })

        if invoice_score >= 5.0:
            conf = min(0.99, 0.55 + invoice_score / 25.0)
            return ClassificationResult(DocumentType.INVOICE, conf, evidence, {
                "invoice": invoice_score, "credit_memo": credit_score,
                "non_payable": nonpay_score, "financial": support,
            })

        return ClassificationResult(DocumentType.UNKNOWN, 0.30 if support else 0.0, evidence, {
            "invoice": invoice_score, "credit_memo": credit_score,
            "non_payable": nonpay_score, "financial": support,
        })
