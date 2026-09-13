# src/document_understanding/document_splitter.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence
import re

from .classifier import DocumentClassifier, DocumentType
from .payable_detector import PayableDetector


@dataclass
class PageRecord:
    page_number: int
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PayableDocument:
    """
    Represents one logical payable document inside an input PDF.
    """

    document_id: str
    page_numbers: List[int]
    text: str
    document_type: DocumentType
    confidence: float
    metadata: Dict[str, Any] = field(default_factory=dict)


class DocumentSplitter:
    """
    Groups PDF pages into logical documents.

    The splitter is intentionally conservative.

    Main goals:
    - Keep multi-page invoices together.
    - Detect multiple invoices inside one PDF.
    - Detect credit memos as separate logical documents.
    - Avoid splitting continuation pages.
    - Avoid filename-based or hardcoded invoice-number logic.
    - Allow non-payable documents to exist between payables.
    """

    DOCUMENT_START_PATTERNS = [
        r"\btax\s+invoice\b",
        r"\bcommercial\s+invoice\b",
        r"\binvoice\b",
        r"\bcredit\s+memo\b",
        r"\bcredit\s+note\b",
        r"\bpurchase\s+order\b",
        r"\bquotation\b",
        r"\bquote\b",
        r"\bproforma\s+invoice\b",
        r"\bdelivery\s+note\b",
        r"\bgoods\s+receipt\b",
        r"\bpacking\s+list\b",
        r"\bstatement\b",
        r"\bremittance\b",
    ]

    INVOICE_NUMBER_PATTERNS = [
        r"\binvoice\s*(?:no|number|#)\s*[:#\-]?\s*"
        r"([A-Za-z0-9][A-Za-z0-9./_-]*)",

        r"\binv\s*(?:no|number|#)\s*[:#\-]?\s*"
        r"([A-Za-z0-9][A-Za-z0-9./_-]*)",

        r"\bbill\s*(?:no|number|#)\s*[:#\-]?\s*"
        r"([A-Za-z0-9][A-Za-z0-9./_-]*)",
    ]

    SUPPLIER_PATTERNS = [
        r"\bsupplier\s*[:\-]\s*(.+)",
        r"\bvendor\s*[:\-]\s*(.+)",
        r"\bfrom\s*[:\-]\s*(.+)",
    ]

    CONTINUATION_PATTERNS = [
        r"\bpage\s+\d+\s+of\s+\d+\b",
        r"\bpage\s+\d+\s*/\s*\d+\b",
        r"\bcontinued\b",
        r"\bcontinuation\b",
        r"\bcontinued\s+from\b",
        r"\bcarried\s+forward\b",
        r"\bbrought\s+forward\b",
    ]

    STRONG_START_PATTERNS = [
        r"^\s*(?:tax\s+)?invoice\b",
        r"^\s*commercial\s+invoice\b",
        r"^\s*credit\s+(?:memo|note)\b",
        r"^\s*purchase\s+order\b",
        r"^\s*proforma\s+invoice\b",
        r"^\s*quotation\b",
        r"^\s*delivery\s+note\b",
        r"^\s*goods\s+receipt\b",
        r"^\s*packing\s+list\b",
    ]

    def __init__(
        self,
        classifier: DocumentClassifier | None = None,
        payable_detector: PayableDetector | None = None,
    ) -> None:

        self.classifier = classifier or DocumentClassifier()

        self.payable_detector = (
            payable_detector
            or PayableDetector(self.classifier)
        )

    # ------------------------------------------------------------------
    # NORMALIZATION
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(text: str) -> str:
        if not text:
            return ""

        text = text.replace("\x00", " ")
        text = text.replace("\r\n", "\n")
        text = text.replace("\r", "\n")

        return text.strip()

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()

    # ------------------------------------------------------------------
    # CLASSIFICATION
    # ------------------------------------------------------------------

    def _classify(self, text: str):
        return self.classifier.classify(text or "")

    # ------------------------------------------------------------------
    # INVOICE NUMBER
    # ------------------------------------------------------------------

    def _extract_invoice_number(
        self,
        text: str,
    ) -> str | None:

        if not text:
            return None

        for pattern in self.INVOICE_NUMBER_PATTERNS:

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                value = match.group(1).strip()

                # Remove obvious trailing punctuation.
                value = value.rstrip(".,;:")

                if value:
                    return value

        return None

    # ------------------------------------------------------------------
    # SUPPLIER
    # ------------------------------------------------------------------

    def _extract_supplier(
        self,
        text: str,
    ) -> str | None:

        if not text:
            return None

        for pattern in self.SUPPLIER_PATTERNS:

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE | re.MULTILINE,
            )

            if match:

                value = self._compact(match.group(1))

                if value:
                    # Keep only a reasonable first line/value.
                    value = value.split("\n")[0].strip()

                    if len(value) > 150:
                        value = value[:150].strip()

                    return value

        return None

    # ------------------------------------------------------------------
    # DOCUMENT START
    # ------------------------------------------------------------------

    def _has_strong_document_start(
        self,
        text: str,
    ) -> bool:

        if not text:
            return False

        normalized = self._compact(text)

        for pattern in self.STRONG_START_PATTERNS:

            if re.search(
                pattern,
                normalized,
                flags=re.IGNORECASE,
            ):
                return True

        return False

    def _has_document_start_signal(
        self,
        text: str,
    ) -> bool:

        if not text:
            return False

        lower = self._compact(text).lower()

        for pattern in self.DOCUMENT_START_PATTERNS:

            if re.search(
                pattern,
                lower,
                flags=re.IGNORECASE,
            ):
                return True

        return False

    # ------------------------------------------------------------------
    # CONTINUATION
    # ------------------------------------------------------------------

    def _is_continuation_page(
        self,
        text: str,
    ) -> bool:

        if not text:
            return False

        for pattern in self.CONTINUATION_PATTERNS:

            if re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            ):
                return True

        return False

    # ------------------------------------------------------------------
    # PAGE BOUNDARY DECISION
    # ------------------------------------------------------------------

    def _looks_like_new_document(
        self,
        text: str,
        current_group: Sequence[PageRecord],
    ) -> bool:

        if not text or not current_group:
            return False

        current_classification = self._classify(text)

        previous_text = current_group[-1].text or ""

        previous_classification = self._classify(
            previous_text
        )

        current_invoice_number = (
            self._extract_invoice_number(text)
        )

        group_text = "\n".join(
            page.text
            for page in current_group
            if page.text
        )

        group_invoice_number = (
            self._extract_invoice_number(group_text)
        )

        current_supplier = self._extract_supplier(text)

        group_supplier = self._extract_supplier(group_text)

        current_type = current_classification.document_type
        previous_type = previous_classification.document_type

        # --------------------------------------------------------------
        # 1. Explicit continuation markers have highest priority.
        # --------------------------------------------------------------

        if self._is_continuation_page(text):

            # If this page explicitly says "continued", keep it
            # with the current document unless there is a very strong
            # conflicting invoice identity.
            if (
                current_invoice_number
                and group_invoice_number
                and current_invoice_number != group_invoice_number
            ):
                return True

            return False

        # --------------------------------------------------------------
        # 2. Same invoice number = definitely same document.
        # --------------------------------------------------------------

        if (
            current_invoice_number
            and group_invoice_number
            and current_invoice_number.lower()
            == group_invoice_number.lower()
        ):
            return False

        # --------------------------------------------------------------
        # 3. A new invoice number is a very strong boundary.
        # --------------------------------------------------------------

        if (
            current_invoice_number
            and group_invoice_number
            and current_invoice_number.lower()
            != group_invoice_number.lower()
        ):
            return True

        # --------------------------------------------------------------
        # 4. Strong document start.
        # --------------------------------------------------------------

        strong_start = self._has_strong_document_start(text)

        if strong_start:

            # If current page is clearly a new financial/document
            # type, start a new logical document.
            if current_type != previous_type:
                return True

            # Same type + strong header + no known invoice number:
            # still likely a new document, but only when the page
            # looks like an actual first page.
            if current_invoice_number:
                return True

            # A strong invoice header appearing after an existing
            # invoice with no shared identity is a reasonable boundary.
            if (
                current_type in (
                    DocumentType.INVOICE,
                    DocumentType.CREDIT_MEMO,
                )
                and previous_type in (
                    DocumentType.INVOICE,
                    DocumentType.CREDIT_MEMO,
                )
            ):
                return True

        # --------------------------------------------------------------
        # 5. Supplier change can support a boundary.
        # --------------------------------------------------------------

        if (
            current_supplier
            and group_supplier
            and current_supplier.lower() != group_supplier.lower()
        ):
            if current_classification.confidence >= 0.70:
                return True

        # --------------------------------------------------------------
        # 6. Document type change with strong confidence.
        # --------------------------------------------------------------

        if (
            current_type != previous_type
            and current_classification.confidence >= 0.75
            and previous_classification.confidence >= 0.75
        ):
            return True

        # --------------------------------------------------------------
        # 7. Generic "invoice" text alone is NOT enough.
        #
        # This is the key protection against splitting a multi-page
        # invoice merely because "invoice" appears in a footer/header.
        # --------------------------------------------------------------

        return False

    # ------------------------------------------------------------------
    # PAGE GROUPING
    # ------------------------------------------------------------------

    def split_pages(
        self,
        pages: Sequence[PageRecord],
    ) -> List[List[PageRecord]]:
        """
        Group pages into logical documents.

        The algorithm intentionally favors keeping pages together
        unless there is strong evidence of a new document.
        """

        if not pages:
            return []

        groups: List[List[PageRecord]] = []

        current_group: List[PageRecord] = []

        for page in pages:

            text = self._normalize(page.text)

            normalized_page = PageRecord(
                page_number=page.page_number,
                text=text,
                metadata=dict(page.metadata),
            )

            if not current_group:
                current_group = [normalized_page]
                continue

            if self._looks_like_new_document(
                text=text,
                current_group=current_group,
            ):
                groups.append(current_group)
                current_group = [normalized_page]
            else:
                current_group.append(normalized_page)

        if current_group:
            groups.append(current_group)

        return groups

    # ------------------------------------------------------------------
    # BUILD PAYABLE DOCUMENTS
    # ------------------------------------------------------------------

    def build_payable_documents(
        self,
        pages: Sequence[PageRecord],
        pdf_stem: str,
    ) -> List[PayableDocument]:

        groups = self.split_pages(pages)

        results: List[PayableDocument] = []

        for index, group in enumerate(groups, start=1):

            combined_text = "\n\n".join(
                page.text
                for page in group
                if page.text
            ).strip()

            if not combined_text:
                continue

            classification = self._classify(
                combined_text
            )

            decision = self.payable_detector.detect(
                combined_text,
                classification,
            )

            # Ignore non-payable logical documents.
            if not decision.is_payable:
                continue

            invoice_number = self._extract_invoice_number(
                combined_text
            )

            document_id = (
                f"{pdf_stem}_payable_{index:03d}"
            )

            results.append(
                PayableDocument(
                    document_id=document_id,
                    page_numbers=[
                        page.page_number
                        for page in group
                    ],
                    text=combined_text,
                    document_type=classification.document_type,
                    confidence=decision.confidence,
                    metadata={
                        "invoice_number": invoice_number,
                        "source_pdf": pdf_stem,
                        "page_count": len(group),
                        "classification_evidence": list(
                            classification.evidence or []
                        ),
                    },
                )
            )

        return results

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------

    def split(
        self,
        pages: Sequence[PageRecord],
        pdf_stem: str,
    ) -> List[PayableDocument]:

        return self.build_payable_documents(
            pages=pages,
            pdf_stem=pdf_stem,
        )


# ======================================================================
# STANDALONE TESTS
# ======================================================================

if __name__ == "__main__":

    splitter = DocumentSplitter()

    test_cases = {

        # --------------------------------------------------------------
        # One-page invoice
        # --------------------------------------------------------------
        "single_invoice": [
            PageRecord(
                1,
                """
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
            ),
        ],

        # --------------------------------------------------------------
        # Multi-page invoice
        # --------------------------------------------------------------
        "multi_page_invoice": [
            PageRecord(
                1,
                """
                TAX INVOICE
                Invoice Number: INV-2001
                Invoice Date: 2026-09-10
                Supplier: ABC Ltd
                Quantity: 5
                Unit Price: 200
                Subtotal: 1000
                """,
            ),
            PageRecord(
                2,
                """
                Invoice Number: INV-2001
                Page 2 of 2
                Line Items
                Item A 2 x 200
                Item B 3 x 200
                Tax 180
                Total 1180
                """,
            ),
        ],

        # --------------------------------------------------------------
        # Two invoices in one PDF
        # --------------------------------------------------------------
        "two_invoices": [
            PageRecord(
                1,
                """
                TAX INVOICE
                Invoice Number: INV-3001
                Supplier: ABC Ltd
                Quantity: 2
                Unit Price: 500
                Total: 1000
                """,
            ),
            PageRecord(
                2,
                """
                TAX INVOICE
                Invoice Number: INV-3002
                Supplier: XYZ Ltd
                Quantity: 3
                Unit Price: 700
                Total: 2100
                """,
            ),
        ],

        # --------------------------------------------------------------
        # Invoice + continuation + second invoice
        # --------------------------------------------------------------
        "invoice_continuation_then_new_invoice": [
            PageRecord(
                1,
                """
                TAX INVOICE
                Invoice Number: INV-4001
                Supplier: ABC Ltd
                Quantity: 5
                Unit Price: 100
                Subtotal: 500
                """,
            ),
            PageRecord(
                2,
                """
                Continued
                Page 2 of 3
                More line items
                Item B 2 x 100
                Item C 3 x 100
                """,
            ),
            PageRecord(
                3,
                """
                TAX INVOICE
                Invoice Number: INV-4002
                Supplier: XYZ Ltd
                Quantity: 4
                Unit Price: 250
                Total: 1000
                """,
            ),
        ],

        # --------------------------------------------------------------
        # Invoice containing PO information
        # --------------------------------------------------------------
        "invoice_with_po": [
            PageRecord(
                1,
                """
                TAX INVOICE
                Invoice Number: INV-5001
                Supplier: ABC Ltd
                Purchase Order: PO-5001
                Quantity: 10
                Unit Price: 100
                Subtotal: 1000
                GST: 180
                Total Due: 1180
                """,
            ),
        ],

        # --------------------------------------------------------------
        # Invoice + non-payable PO
        # --------------------------------------------------------------
        "invoice_then_po": [
            PageRecord(
                1,
                """
                TAX INVOICE
                Invoice Number: INV-6001
                Supplier: ABC Ltd
                Quantity: 2
                Unit Price: 500
                Total: 1000
                """,
            ),
            PageRecord(
                2,
                """
                PURCHASE ORDER
                PO Number: PO-6002
                Requested Delivery Date: 2026-09-20
                Quantity: 10
                Unit Price: 100
                Total: 1000
                """,
            ),
        ],
    }

    print("=" * 80)
    print("DOCUMENT SPLITTER TESTS")
    print("=" * 80)

    for name, pages in test_cases.items():

        documents = splitter.split(
            pages=pages,
            pdf_stem=name,
        )

        print(f"\nTEST: {name}")
        print(f"LOGICAL PAYABLES: {len(documents)}")

        for document in documents:

            print(
                f"  ID={document.document_id} "
                f"TYPE={document.document_type.value} "
                f"PAGES={document.page_numbers} "
                f"CONF={document.confidence:.4f} "
                f"INVOICE={document.metadata.get('invoice_number')}"
            )

    print("\n" + "=" * 80)
    print("EXPECTED")
    print("=" * 80)

    print("single_invoice                         -> 1 payable")
    print("multi_page_invoice                    -> 1 payable, pages [1,2]")
    print("two_invoices                           -> 2 payables")
    print(
        "invoice_continuation_then_new_invoice "
        "-> 2 payables, [1,2] + [3]"
    )
    print("invoice_with_po                       -> 1 payable")
    print("invoice_then_po                      -> 1 payable")