from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Dict, List, Optional


# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class PaymentTermItem:
    name: Optional[str] = None
    days: Optional[int] = None
    code: Optional[str] = None
    raw_text: Optional[str] = None
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =============================================================================
# PAYMENT TERMS EXTRACTOR
# =============================================================================

class PaymentTermsExtractor:
    """
    Extract explicitly printed payment terms from invoices/payable documents.

    Supported examples:

        Net 30
        Net 30 days
        Net 45 days
        Due in 15 days
        Payable within 60 days
        Due on receipt
        Due upon receipt
        Cash on delivery
        COD
        Advance payment
        Prepaid
        EOM
        End of month
        Payment Term Code: N30

    Important:
        - Never invent payment terms.
        - Never calculate a due date.
        - Never infer payment terms from invoice date.
        - Preserve explicitly printed term codes.
        - "Net 30" and "Net 30 days" are equivalent.
    """

    # =========================================================================
    # CONSTRUCTOR
    # =========================================================================

    def __init__(self) -> None:
        pass

    # =========================================================================
    # PAYMENT TERM CODE PATTERNS
    # =========================================================================

    CODE_PATTERNS = [
        re.compile(
            r"\b"
            r"(?:payment\s+term|payment\s+terms)"
            r"\s*"
            r"(?:code|id|no\.?|number)"
            r"\s*[:#\-]?\s*"
            r"([A-Za-z0-9][A-Za-z0-9_.\/-]*)"
            r"\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\b"
            r"term"
            r"\s*"
            r"(?:code|id|no\.?|number)"
            r"\s*[:#\-]?\s*"
            r"([A-Za-z0-9][A-Za-z0-9_.\/-]*)"
            r"\b",
            re.IGNORECASE,
        ),
    ]

    # =========================================================================
    # NET DAYS
    # =========================================================================

    NET_DAYS_PATTERN = re.compile(
        r"\b"
        r"(?:net|due\s+in|within|payable\s+within)"
        r"\s*"
        r"(\d{1,3})"
        r"(?:\s*days?)?"
        r"\b",
        re.IGNORECASE,
    )

    # =========================================================================
    # IMMEDIATE PAYMENT
    # =========================================================================

    IMMEDIATE_PATTERNS = [
        re.compile(
            r"\bdue\s+on\s+receipt\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\bpayable\s+on\s+receipt\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\bdue\s+upon\s+receipt\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\bimmediate\s+payment\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\bpay\s+immediately\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\bcash\s+on\s+delivery\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\bCOD\b",
            re.IGNORECASE,
        ),
    ]

    # =========================================================================
    # ADVANCE
    # =========================================================================

    ADVANCE_PATTERNS = [
        re.compile(
            r"\badvance\s+payment\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\bpayment\s+in\s+advance\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\bpre[\s-]?payment\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\bprepaid\b",
            re.IGNORECASE,
        ),
    ]

    # =========================================================================
    # END OF MONTH
    # =========================================================================

    EOM_PATTERNS = [
        re.compile(
            r"\bEOM\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\bend\s+of\s+month\b",
            re.IGNORECASE,
        ),
    ]

    # =========================================================================
    # GENERIC PAYMENT TERMS LABEL
    # =========================================================================

    PAYMENT_TERMS_LABEL_PATTERN = re.compile(
        r"\b"
        r"(?:payment\s+terms?|terms\s+of\s+payment|"
        r"payment\s+condition|payment\s+conditions)"
        r"\b",
        re.IGNORECASE,
    )

    # =========================================================================
    # NORMALIZATION
    # =========================================================================

    @staticmethod
    def _normalize_line(
        line: str,
    ) -> str:

        return re.sub(
            r"\s+",
            " ",
            line,
        ).strip()

    # =========================================================================
    # CODE EXTRACTION
    # =========================================================================

    def _extract_code(
        self,
        line: str,
    ) -> Optional[str]:

        # Only extract a code when the document explicitly labels
        # the value as Code, ID, No., or Number.
        #
        # IMPORTANT:
        #
        #   Payment Terms: Net 30
        #
        # must NOT produce:
        #
        #   code = "Net"
        #
        # A code should only be extracted from:
        #
        #   Payment Term Code: N30
        #   Payment Term ID: PT45
        #   Term Code: N30

        for pattern in self.CODE_PATTERNS:

            match = pattern.search(
                line
            )

            if match:

                code = match.group(
                    1
                ).strip()

                if code:
                    return code

        return None

    # =========================================================================
    # NET DAYS
    # =========================================================================

    def _extract_days(
        self,
        line: str,
    ) -> Optional[int]:

        match = self.NET_DAYS_PATTERN.search(
            line
        )

        if not match:
            return None

        try:

            days = int(
                match.group(1)
            )

        except ValueError:

            return None

        # Defensive bound.

        if days < 0 or days > 999:
            return None

        return days

    # =========================================================================
    # CLASSIFY TERM
    # =========================================================================

    def _classify_term(
        self,
        line: str,
    ) -> tuple[
        Optional[str],
        Optional[int],
    ]:

        # ---------------------------------------------------------------------
        # Net / due days
        # ---------------------------------------------------------------------

        days = self._extract_days(
            line
        )

        if days is not None:

            return (
                f"NET {days} DAYS",
                days,
            )

        # ---------------------------------------------------------------------
        # Immediate payment
        # ---------------------------------------------------------------------

        for pattern in self.IMMEDIATE_PATTERNS:

            if pattern.search(line):

                return (
                    "IMMEDIATE",
                    None,
                )

        # ---------------------------------------------------------------------
        # Advance
        # ---------------------------------------------------------------------

        for pattern in self.ADVANCE_PATTERNS:

            if pattern.search(line):

                return (
                    "ADVANCE",
                    None,
                )

        # ---------------------------------------------------------------------
        # End of month
        # ---------------------------------------------------------------------

        for pattern in self.EOM_PATTERNS:

            if pattern.search(line):

                return (
                    "EOM",
                    None,
                )

        # ---------------------------------------------------------------------
        # Generic payment-terms label
        # ---------------------------------------------------------------------

        if self.PAYMENT_TERMS_LABEL_PATTERN.search(
            line
        ):

            return (
                "PAYMENT_TERMS",
                None,
            )

        return (
            None,
            None,
        )

    # =========================================================================
    # SINGLE LINE
    # =========================================================================

    def _extract_one(
        self,
        line: str,
    ) -> Optional[PaymentTermItem]:

        line = self._normalize_line(
            line
        )

        if not line:
            return None

        code = self._extract_code(
            line
        )

        name, days = self._classify_term(
            line
        )

        # ---------------------------------------------------------------------
        # No recognizable payment term
        # ---------------------------------------------------------------------

        if (
            name is None
            and code is None
        ):
            return None

        # ---------------------------------------------------------------------
        # Confidence
        # ---------------------------------------------------------------------

        confidence = 0.50

        if name is not None:
            confidence += 0.20

        if days is not None:
            confidence += 0.15

        if code is not None:
            confidence += 0.15

        confidence = min(
            round(
                confidence,
                2,
            ),
            0.99,
        )

        return PaymentTermItem(
            name=name,
            days=days,
            code=code,
            raw_text=line,
            confidence=confidence,
        )

    # =========================================================================
    # MAIN EXTRACTION
    # =========================================================================

    def extract(
        self,
        text: str,
    ) -> List[PaymentTermItem]:

        if not text:
            return []

        results: List[PaymentTermItem] = []

        seen = set()

        for raw_line in text.splitlines():

            item = self._extract_one(
                raw_line
            )

            if item is None:
                continue

            key = (
                item.name,
                item.days,
                item.code,
                item.raw_text,
            )

            if key in seen:
                continue

            seen.add(key)

            results.append(item)

        return results


# =============================================================================
# TEST SUITE
# =============================================================================

if __name__ == "__main__":

    print("=" * 80)
    print("PAYMENT TERMS EXTRACTOR TESTS")
    print("=" * 80)

    extractor = PaymentTermsExtractor()

    # =========================================================================
    # 1. NET 30
    # =========================================================================

    text = """
    Payment Terms: Net 30
    """

    result = extractor.extract(
        text
    )

    print("\nTEST: Net 30")

    for item in result:
        print(item.to_dict())

    assert len(result) == 1

    assert result[0].name == (
        "NET 30 DAYS"
    )

    assert result[0].days == 30

    assert result[0].code is None

    print(
        "PASS: Net 30 extraction"
    )

    # =========================================================================
    # 2. NET 30 DAYS
    # =========================================================================

    text = """
    Payment Terms: Net 30 days
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].name == (
        "NET 30 DAYS"
    )

    assert result[0].days == 30

    assert result[0].code is None

    print(
        "PASS: Net 30 days extraction"
    )

    # =========================================================================
    # 3. NET 45
    # =========================================================================

    text = """
    Payment Terms: Net 45
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].name == (
        "NET 45 DAYS"
    )

    assert result[0].days == 45

    assert result[0].code is None

    print(
        "PASS: Net 45 extraction"
    )

    # =========================================================================
    # 4. DUE IN
    # =========================================================================

    text = """
    Due in 15 days
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].name == (
        "NET 15 DAYS"
    )

    assert result[0].days == 15

    print(
        "PASS: Due in 15 days extraction"
    )

    # =========================================================================
    # 5. PAYABLE WITHIN
    # =========================================================================

    text = """
    Payable within 60 days
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].name == (
        "NET 60 DAYS"
    )

    assert result[0].days == 60

    print(
        "PASS: Payable within 60 days"
    )

    # =========================================================================
    # 6. DUE ON RECEIPT
    # =========================================================================

    text = """
    Due on receipt
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].name == "IMMEDIATE"

    assert result[0].days is None

    print(
        "PASS: due on receipt"
    )

    # =========================================================================
    # 7. DUE UPON RECEIPT
    # =========================================================================

    text = """
    Due upon receipt
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].name == "IMMEDIATE"

    print(
        "PASS: due upon receipt"
    )

    # =========================================================================
    # 8. COD
    # =========================================================================

    text = """
    Payment Method: COD
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].name == "IMMEDIATE"

    print(
        "PASS: COD extraction"
    )

    # =========================================================================
    # 9. ADVANCE PAYMENT
    # =========================================================================

    text = """
    Payment Terms: Advance payment required
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].name == "ADVANCE"

    assert result[0].days is None

    print(
        "PASS: advance payment"
    )

    # =========================================================================
    # 10. PREPAID
    # =========================================================================

    text = """
    Payment is prepaid
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].name == "ADVANCE"

    print(
        "PASS: prepaid extraction"
    )

    # =========================================================================
    # 11. EOM
    # =========================================================================

    text = """
    Payment Terms: EOM
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].name == "EOM"

    print(
        "PASS: EOM extraction"
    )

    # =========================================================================
    # 12. END OF MONTH
    # =========================================================================

    text = """
    Payment due at end of month
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].name == "EOM"

    print(
        "PASS: end of month extraction"
    )

    # =========================================================================
    # 13. PAYMENT TERM CODE
    # =========================================================================

    text = """
    Payment Term Code: N30
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].name == (
        "PAYMENT_TERMS"
    )

    assert result[0].code == "N30"

    print(
        "PASS: payment term code"
    )

    # =========================================================================
    # 14. PAYMENT TERM ID
    # =========================================================================

    text = """
    Payment Term ID: PT45
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].code == "PT45"

    print(
        "PASS: payment term ID"
    )

    # =========================================================================
    # 15. TERM CODE WITH NET DAYS
    # =========================================================================

    text = """
    Payment Terms: Net 30 days
    Payment Term Code: N30
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 2

    assert result[0].days == 30

    assert result[0].code is None

    assert result[1].code == "N30"

    print(
        "PASS: term and code extraction"
    )

    # =========================================================================
    # 16. DUPLICATE PROTECTION
    # =========================================================================

    text = """
    Payment Terms: Net 30
    Payment Terms: Net 30
    Payment Terms: Net 30
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].days == 30

    print(
        "PASS: duplicate protection"
    )

    # =========================================================================
    # 17. UNRELATED FINANCIAL VALUES
    # =========================================================================

    text = """
    Invoice Number: INV-1001
    Invoice Date: 2026-09-10
    Subtotal: 1000.00
    VAT 18%: 180.00
    Grand Total: 1180.00
    """

    result = extractor.extract(
        text
    )

    assert result == []

    print(
        "PASS: unrelated financial rows ignored"
    )

    # =========================================================================
    # 18. NET WITHOUT DAYS WORD
    # =========================================================================

    text = """
    Terms: Net 90
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].name == (
        "NET 90 DAYS"
    )

    assert result[0].days == 90

    print(
        "PASS: Net without days word"
    )

    # =========================================================================
    # 19. NO INVENTION
    # =========================================================================

    text = """
    Payment Terms:
    """

    result = extractor.extract(
        text
    )

    assert len(result) == 1

    assert result[0].name == (
        "PAYMENT_TERMS"
    )

    assert result[0].days is None

    assert result[0].code is None

    print(
        "PASS: no payment-term invention"
    )

    # =========================================================================
    # 20. EMPTY INPUT
    # =========================================================================

    result = extractor.extract(
        ""
    )

    assert result == []

    print(
        "PASS: empty input"
    )

    # =========================================================================
    # FINAL
    # =========================================================================

    print()
    print("=" * 80)
    print("ALL PAYMENT TERMS EXTRACTOR TESTS PASSED")
    print("=" * 80)