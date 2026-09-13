# src/document_understanding/classifier.py

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Dict, List, Tuple


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
        return self.document_type in {
            DocumentType.INVOICE,
            DocumentType.CREDIT_MEMO,
        }


class DocumentClassifier:
    """
    Conservative document classifier for payable automation.

    Categories:
        - invoice
        - credit_memo
        - non_payable
        - unknown

    Important design principles:
        - Does not use filenames.
        - Does not invent document information.
        - Uses document identity + supporting evidence.
        - A PO reference inside an invoice does not make it a PO.
        - A proforma invoice is non-payable.
        - Weak/ambiguous documents become UNKNOWN.
        - Classification confidence is not extraction confidence.

    Downstream validation remains mandatory:

        PDF
          ↓
        Native Text / OCR
          ↓
        Classification
          ↓
        Document Splitting
          ↓
        Field Extraction
          ↓
        Qwen-VL Verification
          ↓
        Master Data Matching
          ↓
        Financial Validation
          ↓
        ERP Validation
          ↓
        AutoDraft
    """

    # ==============================================================
    # INVOICE TERMS
    # ==============================================================

    INVOICE_STRONG_TERMS = (
        "invoice",
        "tax invoice",
        "commercial invoice",
        "sales invoice",
        "invoice no",
        "invoice number",
        "invoice date",
        "invoice #",
        "bill to",
        "amount due",
        "balance due",
        "total due",
        "amount payable",
    )

    INVOICE_SUPPORT_TERMS = (
        "subtotal",
        "unit price",
        "unit cost",
        "quantity",
        "qty",
        "payment terms",
        "payment term",
        "due date",
        "supplier",
        "vendor",
        "purchase order",
        "po number",
        "po no",
        "tax invoice",
        "tax",
        "vat",
        "gst",
        "currency",
        "net amount",
        "gross amount",
        "discount",
        "freight",
        "shipping",
        "charge",
    )

    # ==============================================================
    # CREDIT MEMO TERMS
    # ==============================================================

    CREDIT_STRONG_TERMS = (
        "credit memo",
        "credit note",
        "credit note no",
        "credit memo no",
        "credit note number",
        "credit memo number",
        "credit adjustment note",
        "amount credited",
        "credited amount",
    )

    CREDIT_SUPPORT_TERMS = (
        "credit adjustment",
        "credit",
        "adjustment",
        "refund",
        "return",
        "original invoice",
        "original invoice no",
        "original invoice number",
        "credit amount",
    )

    # ==============================================================
    # NON-PAYABLE TERMS
    # ==============================================================

    NON_PAYABLE_STRONG_TERMS = (
        "purchase order",
        "purchase requisition",
        "quotation",
        "quote",
        "proforma invoice",
        "pro forma invoice",
        "delivery note",
        "delivery receipt",
        "packing slip",
        "packing list",
        "goods receipt",
        "goods received note",
        "order confirmation",
        "shipping notice",
        "remittance advice",
        "timesheet",
        "expense report",
        "contract",
    )

    NON_PAYABLE_SUPPORT_TERMS = (
        "requested delivery date",
        "shipping address",
        "ship to",
        "delivery address",
        "ordered by",
        "requested by",
        "valid until",
        "quotation valid",
        "rfq",
        "request for quotation",
        "purchase request",
    )

    # ==============================================================
    # FINANCIAL TERMS
    # ==============================================================

    FINANCIAL_TERMS = (
        "subtotal",
        "total",
        "tax",
        "vat",
        "gst",
        "amount",
        "amount due",
        "amount payable",
        "balance due",
        "price",
        "unit price",
        "quantity",
        "qty",
        "currency",
        "payment",
        "payment terms",
        "discount",
        "charge",
        "freight",
        "shipping",
        "withholding tax",
        "wht",
    )

    # ==============================================================
    # REGEX
    # ==============================================================

    NUMBER_PATTERN = re.compile(
        r"(?:\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    )

    CURRENCY_PATTERN = re.compile(
        r"(?:USD|EUR|GBP|INR|JPY|CNY|AUD|CAD|SGD|THB|MYR|IDR|AED|SAR|\$|€|£|₹|¥)",
        re.IGNORECASE,
    )

    DATE_PATTERN = re.compile(
        r"""
        \b
        (?:
            \d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}
            |
            \d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}
            |
            [A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}
        )
        \b
        """,
        re.VERBOSE,
    )

    # ==============================================================
    # INITIALIZATION
    # ==============================================================

    @staticmethod
    def _compile_terms(
        terms: Tuple[str, ...],
    ) -> List[re.Pattern]:
        """
        Compile terms using word-aware matching.

        Multi-word terms support flexible whitespace.
        """

        patterns: List[re.Pattern] = []

        for term in terms:
            escaped = re.escape(term)

            escaped = escaped.replace(
                r"\ ",
                r"\s+",
            )

            patterns.append(
                re.compile(
                    rf"(?<!\w){escaped}(?!\w)",
                    re.IGNORECASE,
                )
            )

        return patterns

    def __init__(self) -> None:

        self.invoice_strong_patterns = self._compile_terms(
            self.INVOICE_STRONG_TERMS
        )

        self.invoice_support_patterns = self._compile_terms(
            self.INVOICE_SUPPORT_TERMS
        )

        self.credit_strong_patterns = self._compile_terms(
            self.CREDIT_STRONG_TERMS
        )

        self.credit_support_patterns = self._compile_terms(
            self.CREDIT_SUPPORT_TERMS
        )

        self.non_payable_strong_patterns = self._compile_terms(
            self.NON_PAYABLE_STRONG_TERMS
        )

        self.non_payable_support_patterns = self._compile_terms(
            self.NON_PAYABLE_SUPPORT_TERMS
        )

        self.financial_patterns = self._compile_terms(
            self.FINANCIAL_TERMS
        )

    # ==============================================================
    # NORMALIZATION
    # ==============================================================

    @staticmethod
    def _normalize(
        text: str,
    ) -> str:
        """
        Normalize native/OCR text.

        HTML table structures are converted into readable text.
        """

        if not text:
            return ""

        text = str(text)

        # Remove null characters.
        text = text.replace(
            "\x00",
            " ",
        )

        # Convert common HTML table structures.
        text = re.sub(
            r"<br\s*/?>",
            "\n",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"</tr\s*>",
            "\n",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"</(?:td|th)\s*>",
            " | ",
            text,
            flags=re.IGNORECASE,
        )

        # Remove remaining HTML tags.
        text = re.sub(
            r"<[^>]+>",
            " ",
            text,
            flags=re.IGNORECASE,
        )

        # Normalize spaces.
        text = re.sub(
            r"[ \t]+",
            " ",
            text,
        )

        text = re.sub(
            r"\n[ \t]+",
            "\n",
            text,
        )

        text = re.sub(
            r"[ \t]+\n",
            "\n",
            text,
        )

        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )

        return text.strip()

    # ==============================================================
    # MATCHING HELPERS
    # ==============================================================

    @staticmethod
    def _matches(
        text: str,
        patterns: List[re.Pattern],
    ) -> List[str]:
        """
        Return matched terms without duplicates.
        """

        evidence: List[str] = []

        for pattern in patterns:

            match = pattern.search(text)

            if match:

                value = match.group(0).strip()

                if value and value not in evidence:
                    evidence.append(value)

        return evidence

    @classmethod
    def _count(
        cls,
        text: str,
        patterns: List[re.Pattern],
    ) -> int:

        return len(
            cls._matches(
                text,
                patterns,
            )
        )

    # ==============================================================
    # BASIC SIGNALS
    # ==============================================================

    @staticmethod
    def _has_invoice_number(
        text: str,
    ) -> bool:
        """
        Detect explicit invoice number.

        Examples:
            Invoice No: INV-123
            Invoice Number: INV-123
            Invoice #: INV-123
            Inv No: 1001
        """

        patterns = (
            r"\binvoice\s*(?:no|number|#)\s*[:\-]?\s*\S+",
            r"\binv\s*(?:no|number|#)\s*[:\-]?\s*\S+",
        )

        return any(
            re.search(
                pattern,
                text,
                re.IGNORECASE,
            )
            for pattern in patterns
        )

    @staticmethod
    def _has_date(
        text: str,
    ) -> bool:

        return bool(
            DocumentClassifier.DATE_PATTERN.search(text)
        )

    @staticmethod
    def _has_currency(
        text: str,
    ) -> bool:

        return bool(
            DocumentClassifier.CURRENCY_PATTERN.search(text)
        )

    @staticmethod
    def _has_financial_number(
        text: str,
    ) -> bool:
        """
        Detect whether the text contains a numeric value.

        Supporting evidence only.
        """

        return bool(
            DocumentClassifier.NUMBER_PATTERN.search(text)
        )

    # ==============================================================
    # DOCUMENT IDENTITY
    # ==============================================================

    @staticmethod
    def _has_final_invoice_marker(
        text: str,
    ) -> bool:
        """
        Detect explicit final invoice identity.

        Important:

            PURCHASE ORDER + TAX INVOICE
                -> invoice

            PURCHASE ORDER alone
                -> non_payable

            PROFORMA INVOICE
                -> non_payable
        """

        patterns = (
            r"\btax\s+invoice\b",
            r"\bcommercial\s+invoice\b",
            r"\bsales\s+invoice\b",
            r"\binvoice\s*(?:no|number|#)\b",
            r"\binvoice\s+date\b",
            r"\bamount\s+due\b",
            r"\bbalance\s+due\b",
            r"\btotal\s+due\b",
            r"\bamount\s+payable\b",
        )

        return any(
            re.search(
                pattern,
                text,
                re.IGNORECASE,
            )
            for pattern in patterns
        )

    @staticmethod
    def _has_credit_marker(
        text: str,
    ) -> bool:
        """
        Detect explicit credit-document identity.
        """

        patterns = (
            r"\bcredit\s+memo\b",
            r"\bcredit\s+note\b",
            r"\bcredit\s+note\s*(?:no|number|#)\b",
            r"\bcredit\s+memo\s*(?:no|number|#)\b",
            r"\bamount\s+credited\b",
            r"\bcredited\s+amount\b",
        )

        return any(
            re.search(
                pattern,
                text,
                re.IGNORECASE,
            )
            for pattern in patterns
        )

    @staticmethod
    def _has_proforma_marker(
        text: str,
    ) -> bool:
        """
        Detect proforma invoice.

        Proforma invoices are not final payable invoices.
        """

        patterns = (
            r"\bpro\s*forma\s+invoice\b",
            r"\bproforma\s+invoice\b",
        )

        return any(
            re.search(
                pattern,
                text,
                re.IGNORECASE,
            )
            for pattern in patterns
        )

    @staticmethod
    def _has_purchase_order_marker(
        text: str,
    ) -> bool:

        patterns = (
            r"\bpurchase\s+order\b",
            r"\bpurchase\s+order\s*(?:no|number|#)\b",
            r"\bpo\s*(?:no|number|#)\b",
        )

        return any(
            re.search(
                pattern,
                text,
                re.IGNORECASE,
            )
            for pattern in patterns
        )

    @staticmethod
    def _has_quotation_marker(
        text: str,
    ) -> bool:

        patterns = (
            r"\bquotation\b",
            r"\bquote\b",
            r"\brfq\b",
            r"\brequest\s+for\s+quotation\b",
        )

        return any(
            re.search(
                pattern,
                text,
                re.IGNORECASE,
            )
            for pattern in patterns
        )

    @staticmethod
    def _has_delivery_marker(
        text: str,
    ) -> bool:

        return bool(
            re.search(
                r"\bdelivery\s+(?:note|receipt)\b",
                text,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _has_packing_marker(
        text: str,
    ) -> bool:

        return bool(
            re.search(
                r"\bpacking\s+(?:slip|list)\b",
                text,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _has_goods_receipt_marker(
        text: str,
    ) -> bool:

        return bool(
            re.search(
                r"\bgoods\s+(?:receipt|received\s+note)\b",
                text,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _has_remittance_marker(
        text: str,
    ) -> bool:

        return bool(
            re.search(
                r"\bremittance\s+advice\b",
                text,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _has_timesheet_marker(
        text: str,
    ) -> bool:

        return bool(
            re.search(
                r"\btimesheet\b",
                text,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _has_expense_marker(
        text: str,
    ) -> bool:

        return bool(
            re.search(
                r"\bexpense\s+report\b",
                text,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _has_contract_marker(
        text: str,
    ) -> bool:

        return bool(
            re.search(
                r"\bcontract\b",
                text,
                re.IGNORECASE,
            )
        )

    # ==============================================================
    # SCORE BUILDING
    # ==============================================================

    def _build_scores(
        self,
        text: str,
    ) -> Tuple[
        float,
        float,
        float,
        List[str],
        List[str],
        List[str],
        List[str],
    ]:
        """
        Build classification evidence scores.
        """

        invoice_strong = self._matches(
            text,
            self.invoice_strong_patterns,
        )

        invoice_support = self._matches(
            text,
            self.invoice_support_patterns,
        )

        credit_strong = self._matches(
            text,
            self.credit_strong_patterns,
        )

        credit_support = self._matches(
            text,
            self.credit_support_patterns,
        )

        non_payable_strong = self._matches(
            text,
            self.non_payable_strong_patterns,
        )

        non_payable_support = self._matches(
            text,
            self.non_payable_support_patterns,
        )

        financial = self._matches(
            text,
            self.financial_patterns,
        )

        # ----------------------------------------------------------
        # Invoice score
        # ----------------------------------------------------------

        invoice_score = (
            len(invoice_strong) * 3.0
            + len(invoice_support) * 0.75
        )

        if self._has_invoice_number(text):
            invoice_score += 2.0

        if self._has_date(text):
            invoice_score += 0.75

        if (
            len(financial) >= 2
            and self._has_financial_number(text)
        ):
            invoice_score += 1.5

        if self._has_currency(text):
            invoice_score += 0.5

        # ----------------------------------------------------------
        # Credit memo score
        # ----------------------------------------------------------

        credit_score = (
            len(credit_strong) * 4.0
            + len(credit_support) * 0.75
        )

        if re.search(
            r"\boriginal\s+invoice\b",
            text,
            re.IGNORECASE,
        ):
            credit_score += 1.5

        # ----------------------------------------------------------
        # Non-payable score
        # ----------------------------------------------------------

        non_payable_score = (
            len(non_payable_strong) * 3.0
            + len(non_payable_support) * 0.75
        )

        # A PO mentioned inside an actual invoice should not dominate.
        if (
            invoice_strong
            and non_payable_strong
        ):
            non_payable_score *= 0.55

        # Proforma gets explicit non-payable weight.
        if self._has_proforma_marker(text):
            non_payable_score += 5.0

        return (
            invoice_score,
            credit_score,
            non_payable_score,
            invoice_strong + invoice_support,
            credit_strong + credit_support,
            non_payable_strong + non_payable_support,
            financial,
        )

    # ==============================================================
    # CONFIDENCE
    # ==============================================================

    @staticmethod
    def _confidence(
        winning_score: float,
        second_score: float,
        evidence_strength: float,
        hard_evidence: bool,
    ) -> float:
        """
        Conservative classification confidence.

        This is not OCR confidence.
        This is not extraction confidence.
        """

        if winning_score <= 0:
            return 0.0

        margin = (
            winning_score
            - second_score
        )

        evidence_component = min(
            1.0,
            evidence_strength / 12.0,
        )

        margin_component = min(
            1.0,
            max(
                0.0,
                margin,
            ) / 10.0,
        )

        confidence = (
            0.45
            + evidence_component * 0.30
            + margin_component * 0.20
        )

        if hard_evidence:
            confidence += 0.05

        return round(
            max(
                0.0,
                min(
                    0.99,
                    confidence,
                ),
            ),
            4,
        )

    # ==============================================================
    # PUBLIC CLASSIFICATION API
    # ==============================================================

    def classify(
        self,
        text: str,
    ) -> ClassificationResult:

        text = self._normalize(text)

        # ==========================================================
        # EMPTY DOCUMENT
        # ==========================================================

        if not text:

            return ClassificationResult(
                document_type=DocumentType.UNKNOWN,
                confidence=0.0,
                evidence=[
                    "No text available"
                ],
                scores={
                    "invoice": 0.0,
                    "credit_memo": 0.0,
                    "non_payable": 0.0,
                    "financial": 0.0,
                },
            )

        # ==========================================================
        # SCORE CALCULATION
        # ==========================================================

        (
            invoice_score,
            credit_score,
            non_payable_score,
            invoice_evidence,
            credit_evidence,
            non_payable_evidence,
            financial_evidence,
        ) = self._build_scores(text)

        financial_score = float(
            len(financial_evidence)
        )

        scores = {
            "invoice": round(
                invoice_score,
                4,
            ),
            "credit_memo": round(
                credit_score,
                4,
            ),
            "non_payable": round(
                non_payable_score,
                4,
            ),
            "financial": round(
                financial_score,
                4,
            ),
        }

        # ==========================================================
        # DOCUMENT IDENTITY
        # ==========================================================

        has_final_invoice_marker = (
            self._has_final_invoice_marker(text)
        )

        has_credit_marker = (
            self._has_credit_marker(text)
        )

        has_proforma_marker = (
            self._has_proforma_marker(text)
        )

        has_po_marker = (
            self._has_purchase_order_marker(text)
        )

        has_quotation_marker = (
            self._has_quotation_marker(text)
        )

        has_delivery_marker = (
            self._has_delivery_marker(text)
        )

        has_packing_marker = (
            self._has_packing_marker(text)
        )

        has_goods_receipt_marker = (
            self._has_goods_receipt_marker(text)
        )

        has_remittance_marker = (
            self._has_remittance_marker(text)
        )

        has_timesheet_marker = (
            self._has_timesheet_marker(text)
        )

        has_expense_marker = (
            self._has_expense_marker(text)
        )

        has_contract_marker = (
            self._has_contract_marker(text)
        )

        explicit_non_payable = any(
            (
                has_po_marker,
                has_quotation_marker,
                has_delivery_marker,
                has_packing_marker,
                has_goods_receipt_marker,
                has_remittance_marker,
                has_timesheet_marker,
                has_expense_marker,
                has_contract_marker,
            )
        )

        # ==========================================================
        # INVOICE SIGNALS
        # ==========================================================

        invoice_has_number = (
            self._has_invoice_number(text)
        )

        invoice_has_financial_structure = (
            financial_score >= 2.0
            and self._has_financial_number(text)
        )

        strong_invoice_identity = (
            has_final_invoice_marker
            or invoice_has_number
        )

        # ==========================================================
        # 1. PROFORMA — HIGHEST PRIORITY
        # ==========================================================
        #
        # This MUST happen before final-invoice classification.
        #
        # Example:
        #
        # PROFORMA INVOICE
        # Invoice Number: PI-1001
        #
        # must remain NON_PAYABLE.
        #
        # The presence of "Invoice Number" must not override
        # "Proforma Invoice".

        if has_proforma_marker:

            return ClassificationResult(
                document_type=DocumentType.NON_PAYABLE,
                confidence=0.95,
                evidence=[
                    "Proforma Invoice"
                ] + financial_evidence,
                scores=scores,
            )

        # ==========================================================
        # 2. CREDIT MEMO
        # ==========================================================

        if (
            has_credit_marker
            or credit_score >= 4.0
        ):

            second_score = max(
                invoice_score,
                non_payable_score,
            )

            confidence = self._confidence(
                winning_score=credit_score,
                second_score=second_score,
                evidence_strength=(
                    credit_score
                    + financial_score
                ),
                hard_evidence=has_credit_marker,
            )

            if has_credit_marker:
                confidence = max(
                    confidence,
                    0.85,
                )

            return ClassificationResult(
                document_type=DocumentType.CREDIT_MEMO,
                confidence=round(
                    min(
                        confidence,
                        0.99,
                    ),
                    4,
                ),
                evidence=(
                    credit_evidence
                    + financial_evidence
                ),
                scores=scores,
            )

        # ==========================================================
        # 3. EXPLICIT NON-PAYABLE
        # ==========================================================

        # Critical rule:
        #
        # PURCHASE ORDER
        #     + no final invoice identity
        #     = NON_PAYABLE
        #
        # But:
        #
        # TAX INVOICE
        #     + PURCHASE ORDER
        #     = INVOICE

        if (
            explicit_non_payable
            and not has_final_invoice_marker
        ):

            strong_non_payable_identity = any(
                (
                    has_po_marker,
                    has_quotation_marker,
                    has_delivery_marker,
                    has_packing_marker,
                    has_goods_receipt_marker,
                )
            )

            if strong_non_payable_identity:

                confidence = self._confidence(
                    winning_score=max(
                        non_payable_score,
                        5.0,
                    ),
                    second_score=max(
                        invoice_score,
                        credit_score,
                    ),
                    evidence_strength=(
                        non_payable_score
                        + financial_score
                    ),
                    hard_evidence=True,
                )

                confidence = max(
                    confidence,
                    0.90,
                )

                return ClassificationResult(
                    document_type=DocumentType.NON_PAYABLE,
                    confidence=round(
                        min(
                            confidence,
                            0.98,
                        ),
                        4,
                    ),
                    evidence=(
                        non_payable_evidence
                        + financial_evidence
                    ),
                    scores=scores,
                )

        # ==========================================================
        # 4. FINAL INVOICE
        # ==============================================================

        if (
            strong_invoice_identity
            and invoice_has_financial_structure
            and invoice_score >= 3.0
            and invoice_score > credit_score
        ):

            second_score = max(
                credit_score,
                non_payable_score,
            )

            confidence = self._confidence(
                winning_score=invoice_score,
                second_score=second_score,
                evidence_strength=(
                    invoice_score
                    + financial_score
                ),
                hard_evidence=has_final_invoice_marker,
            )

            if (
                has_final_invoice_marker
                and invoice_has_number
            ):
                confidence = max(
                    confidence,
                    0.90,
                )

            return ClassificationResult(
                document_type=DocumentType.INVOICE,
                confidence=round(
                    min(
                        confidence,
                        0.99,
                    ),
                    4,
                ),
                evidence=(
                    invoice_evidence
                    + financial_evidence
                ),
                scores=scores,
            )

        # ==========================================================
        # 5. WEAK INVOICE
        # ==========================================================

        if (
            invoice_score >= 3.0
            and financial_score >= 2.0
            and invoice_score > non_payable_score
            and invoice_score > credit_score
            and not explicit_non_payable
        ):

            second_score = max(
                credit_score,
                non_payable_score,
            )

            confidence = self._confidence(
                winning_score=invoice_score,
                second_score=second_score,
                evidence_strength=(
                    invoice_score
                    + financial_score
                ),
                hard_evidence=False,
            )

            # Weak evidence cannot receive high confidence.
            confidence = min(
                confidence,
                0.89,
            )

            return ClassificationResult(
                document_type=DocumentType.INVOICE,
                confidence=round(
                    confidence,
                    4,
                ),
                evidence=(
                    invoice_evidence
                    + financial_evidence
                ),
                scores=scores,
            )

        # ==========================================================
        # 6. GENERIC NON-PAYABLE
        # ==========================================================

        if (
            non_payable_score >= 5.0
            and invoice_score < 5.0
            and credit_score < 4.0
        ):

            confidence = self._confidence(
                winning_score=non_payable_score,
                second_score=max(
                    invoice_score,
                    credit_score,
                ),
                evidence_strength=non_payable_score,
                hard_evidence=False,
            )

            return ClassificationResult(
                document_type=DocumentType.NON_PAYABLE,
                confidence=round(
                    min(
                        confidence,
                        0.95,
                    ),
                    4,
                ),
                evidence=non_payable_evidence,
                scores=scores,
            )

        # ==========================================================
        # 7. UNKNOWN
        # ==========================================================

        all_evidence = (
            invoice_evidence
            + credit_evidence
            + non_payable_evidence
            + financial_evidence
        )

        # Deduplicate while preserving order.
        all_evidence = list(
            dict.fromkeys(
                all_evidence
            )
        )

        return ClassificationResult(
            document_type=DocumentType.UNKNOWN,
            confidence=(
                0.30
                if all_evidence
                else 0.0
            ),
            evidence=all_evidence,
            scores=scores,
        )


# ======================================================================
# CLI TEST
# ======================================================================

if __name__ == "__main__":

    classifier = DocumentClassifier()

    test_documents = {

        # --------------------------------------------------------------
        # FINAL INVOICE
        # --------------------------------------------------------------

        "invoice": """
            TAX INVOICE
            Invoice Number: INV-1001
            Invoice Date: 05/09/2026
            Supplier: ABC Technologies
            PO Number: PO-123
            Quantity: 10
            Unit Price: 500.00
            Subtotal: 5000.00
            GST: 900.00
            Total Amount Due: 5900.00
            Currency: INR
        """,

        # --------------------------------------------------------------
        # CREDIT MEMO
        # --------------------------------------------------------------

        "credit_memo": """
            CREDIT NOTE
            Credit Note Number: CN-1001
            Original Invoice Number: INV-9001
            Credit Amount: 1500.00
            Tax Adjustment: 270.00
            Total Credited: 1770.00
        """,

        # --------------------------------------------------------------
        # PURCHASE ORDER
        # --------------------------------------------------------------

        "purchase_order": """
            PURCHASE ORDER
            PO Number: PO-12345
            Supplier: ABC Technologies
            Requested Delivery Date: 15/09/2026
            Quantity: 20
            Unit Price: 100.00
            Order Total: 2000.00
        """,

        # --------------------------------------------------------------
        # QUOTATION
        # --------------------------------------------------------------

        "quotation": """
            QUOTATION
            Quote Number: Q-100
            Valid Until: 30/09/2026
            Supplier: ABC Technologies
            Unit Price: 1000.00
            Total: 5000.00
        """,

        # --------------------------------------------------------------
        # PROFORMA
        # --------------------------------------------------------------

        "proforma": """
            PROFORMA INVOICE
            Invoice Number: PI-1001
            Supplier: ABC Technologies
            Quantity: 10
            Unit Price: 100.00
            Total: 1000.00
        """,

        # --------------------------------------------------------------
        # DELIVERY NOTE
        # --------------------------------------------------------------

        "delivery_note": """
            DELIVERY NOTE
            Delivery Number: DN-1001
            Supplier: ABC Technologies
            Quantity Delivered: 20
            Product: Electronics
        """,

        # --------------------------------------------------------------
        # INVOICE WITH PO
        # --------------------------------------------------------------

        "invoice_with_po": """
            TAX INVOICE
            Invoice No: INV-500
            Invoice Date: 10/09/2026
            Purchase Order: PO-888
            Supplier: XYZ Ltd
            Quantity: 5
            Unit Price: 100
            Subtotal: 500
            GST: 90
            Total Due: 590
        """,

        # --------------------------------------------------------------
        # WEAK
        # --------------------------------------------------------------

        "weak": """
            Amount 500
            Tax 90
        """,

        # --------------------------------------------------------------
        # EMPTY
        # --------------------------------------------------------------

        "empty": "",
    }

    for name, text in test_documents.items():

        result = classifier.classify(text)

        print("=" * 70)
        print(f"TEST: {name}")
        print(
            f"TYPE: {result.document_type.value}"
        )
        print(
            f"CONFIDENCE: {result.confidence:.4f}"
        )
        print(
            f"PAYABLE: {result.is_payable}"
        )
        print(
            f"SCORES: {result.scores}"
        )
        print(
            f"EVIDENCE: {result.evidence}"
        )