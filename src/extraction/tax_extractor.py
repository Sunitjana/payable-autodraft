from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Dict, List, Optional


@dataclass
class TaxItem:
    name: Optional[str] = None
    rate: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    raw_text: Optional[str] = None
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)

        for key, value in result.items():
            if isinstance(value, Decimal):
                result[key] = str(value)

        return result


class TaxExtractor:
    """
    Extract explicit tax rows from invoice/payable text.

    Rules:
    - Extract only values explicitly present in the document.
    - Never calculate missing tax amounts.
    - Preserve tax rates when visible.
    - Support common international number formats.
    - Do not treat withholding tax as normal invoice tax.
    - Do not treat tax IDs / registration numbers as tax amounts.
    - Do not treat subtotal/grand-total rows as tax rows.
    - Preserve separate taxes such as VAT, GST, CGST, SGST and IGST.
    """

    TAX_ALIASES = {
        "vat": "VAT",
        "gst": "GST",
        "cgst": "CGST",
        "sgst": "SGST",
        "igst": "IGST",
        "utgst": "UTGST",
        "sales tax": "SALES_TAX",
        "service tax": "SERVICE_TAX",
        "iva": "IVA",
        "mwst": "MWST",
        "hst": "HST",
        "pst": "PST",
        "qst": "QST",
        "sst": "SST",
        "jct": "JCT",
        "tva": "TVA",
    }

    TAX_TERMS = tuple(TAX_ALIASES.keys())

    EXCLUDED_PATTERNS = [
        r"\bwithholding\s+tax\b",
        r"\bwithholding\b",
        r"\btax\s+(?:id|identification|number|no\.?|registration)\b",
        r"\btax\s+invoice\s+(?:no\.?|number|#)\b",
        r"\btotal\s+tax(?:es)?\b",
        r"\btax\s+total\b",
        r"^\s*(?:grand\s+)?total\b",
        r"\btotal\s+including\b",
    ]

    RATE_PATTERN = re.compile(
        r"(?<![\d.,])"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*%",
    )

    NUMBER_PATTERN = re.compile(
        r"(?<![A-Za-z0-9])"
        r"-?\d[\d.,]*"
        r"(?![A-Za-z0-9])"
    )

    CURRENCY_PREFIX_PATTERN = re.compile(
        r"(?:"
        r"[$€£¥₹]"
        r"|"
        r"(?:USD|EUR|GBP|JPY|INR|THB|CNY|CAD|AUD|SGD|HKD)"
        r")"
        r"\s*"
        r"(?P<number>-?(?:\d[\d.,]*))",
        re.IGNORECASE,
    )

    def __init__(self) -> None:
        pass

    # =========================================================================
    # NUMBER PARSING
    # =========================================================================

    @staticmethod
    def _number(
        value: str,
    ) -> Optional[Decimal]:

        if value is None:
            return None

        value = str(value).strip()

        if not value:
            return None

        negative = (
            value.startswith("-")
            or (
                value.startswith("(")
                and value.endswith(")")
            )
        )

        value = value.strip("()")

        value = re.sub(
            r"[^\d.,]",
            "",
            value,
        )

        if not value:
            return None

        # ---------------------------------------------------------------------
        # Both comma and dot
        # ---------------------------------------------------------------------

        if "," in value and "." in value:

            # European:
            # 1.234,56 -> 1234.56

            if value.rfind(",") > value.rfind("."):

                value = value.replace(
                    ".",
                    "",
                )

                value = value.replace(
                    ",",
                    ".",
                )

            # US:
            # 1,234.56 -> 1234.56

            else:

                value = value.replace(
                    ",",
                    "",
                )

        # ---------------------------------------------------------------------
        # Comma only
        # ---------------------------------------------------------------------

        elif "," in value:

            parts = value.split(",")

            # 500,00 -> 500.00
            if (
                len(parts) == 2
                and len(parts[-1]) in (1, 2)
            ):

                value = (
                    parts[0]
                    + "."
                    + parts[-1]
                )

            # 1,234 -> 1234
            elif (
                len(parts) == 2
                and len(parts[-1]) == 3
            ):

                value = value.replace(
                    ",",
                    "",
                )

            else:

                value = value.replace(
                    ",",
                    "",
                )

        try:

            number = Decimal(value)

            if negative:
                number = -number

            return number

        except InvalidOperation:

            return None

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
    # EXCLUSION
    # =========================================================================

    def _is_excluded(
        self,
        line: str,
    ) -> bool:

        lower = line.lower()

        return any(
            re.search(
                pattern,
                lower,
            )
            for pattern in self.EXCLUDED_PATTERNS
        )

    # =========================================================================
    # TAX NAME
    # =========================================================================

    def _find_tax_name(
        self,
        line: str,
    ) -> Optional[str]:

        lower = line.lower()

        # Longest names first.
        for alias in sorted(
            self.TAX_ALIASES,
            key=len,
            reverse=True,
        ):

            if re.search(
                rf"(?<![a-z])"
                rf"{re.escape(alias)}"
                rf"(?![a-z])",
                lower,
            ):

                return self.TAX_ALIASES[
                    alias
                ]

        return None

    # =========================================================================
    # RATE
    # =========================================================================

    def _extract_rate(
        self,
        line: str,
    ) -> Optional[Decimal]:

        match = self.RATE_PATTERN.search(
            line
        )

        if not match:
            return None

        return self._number(
            match.group(1)
        )

    # =========================================================================
    # NUMERIC CANDIDATES
    # =========================================================================

    def _extract_numeric_candidates(
        self,
        line: str,
    ) -> List[Decimal]:

        candidates: List[Decimal] = []

        for match in self.NUMBER_PATTERN.finditer(
            line
        ):

            number = self._number(
                match.group(0)
            )

            if number is not None:
                candidates.append(number)

        return candidates

    # =========================================================================
    # AMOUNT
    # =========================================================================

    def _extract_amount(
        self,
        line: str,
        rate: Optional[Decimal],
    ) -> Optional[Decimal]:
        """
        Extract only an explicitly printed tax amount.

        Example:

            VAT 7%: 549.36

        -> rate = 7
        -> amount = 549.36

        No calculation is performed.
        """

        # ---------------------------------------------------------------------
        # Currency-marked amount gets priority.
        # ---------------------------------------------------------------------

        currency_matches = list(
            self.CURRENCY_PREFIX_PATTERN.finditer(
                line
            )
        )

        if currency_matches:

            value = self._number(
                currency_matches[-1].group(
                    "number"
                )
            )

            if value is not None:
                return value

        # ---------------------------------------------------------------------
        # Generic numeric candidates.
        # ---------------------------------------------------------------------

        candidates = (
            self._extract_numeric_candidates(
                line
            )
        )

        if not candidates:
            return None

        # ---------------------------------------------------------------------
        # Remove the percentage value.
        # ---------------------------------------------------------------------

        if rate is not None:

            rate_match = (
                self.RATE_PATTERN.search(
                    line
                )
            )

            if rate_match:

                rate_token = self._number(
                    rate_match.group(1)
                )

                filtered: List[Decimal] = []

                removed = False

                for candidate in candidates:

                    if (
                        not removed
                        and rate_token is not None
                        and candidate == rate_token
                    ):

                        removed = True
                        continue

                    filtered.append(
                        candidate
                    )

                candidates = filtered

        if not candidates:
            return None

        # Last remaining number is the
        # explicit amount.
        return candidates[-1]

    # =========================================================================
    # SINGLE LINE
    # =========================================================================

    def _extract_one(
        self,
        line: str,
    ) -> Optional[TaxItem]:

        line = self._normalize_line(
            line
        )

        if not line:
            return None

        # Protect:
        #   Withholding tax
        #   Grand Total including VAT
        #   Tax ID
        #   Total Tax
        if self._is_excluded(line):
            return None

        name = self._find_tax_name(
            line
        )

        if name is None:
            return None

        rate = self._extract_rate(
            line
        )

        amount = self._extract_amount(
            line,
            rate,
        )

        confidence = 0.45

        if name:
            confidence += 0.20

        if rate is not None:
            confidence += 0.15

        if amount is not None:
            confidence += 0.20

        confidence = min(
            round(confidence, 2),
            0.99,
        )

        return TaxItem(
            name=name,
            rate=rate,
            amount=amount,
            raw_text=line,
            confidence=confidence,
        )

    # =========================================================================
    # MAIN EXTRACTION
    # =========================================================================

    def extract(
        self,
        text: str,
    ) -> List[TaxItem]:

        if not text:
            return []

        results: List[TaxItem] = []

        for raw_line in text.splitlines():

            item = self._extract_one(
                raw_line
            )

            if item is not None:
                results.append(item)

        return results


# =============================================================================
# TEST SUITE
# =============================================================================

if __name__ == "__main__":

    print("=" * 80)
    print("TAX EXTRACTOR TESTS")
    print("=" * 80)

    extractor = TaxExtractor()

    # =========================================================================
    # 1. VAT
    # =========================================================================

    vat_text = """
    Subtotal: 7,200.00
    VAT 7%: 549.36
    Grand Total: 8,397.36
    """

    result = extractor.extract(
        vat_text
    )

    print("\nTEST: VAT")

    for item in result:
        print(item.to_dict())

    assert len(result) == 1
    assert result[0].name == "VAT"
    assert result[0].rate == Decimal("7")
    assert result[0].amount == Decimal(
        "549.36"
    )

    print(
        "PASS: VAT extraction"
    )

    # =========================================================================
    # 2. HLD OCR
    # =========================================================================

    hld_text = """
    Total: 7,200.00
    Management Fee 9%: 648.00
    Total including agency fee: 7,848.00
    VAT 7%: 549.36
    Grand Total including VAT: 8,397.36
    Withholding tax: 235.44
    Total payment: 8,161.92
    """

    result = extractor.extract(
        hld_text
    )

    print("\nTEST: HLD OCR")

    for item in result:
        print(item.to_dict())

    assert len(result) == 1

    assert result[0].name == "VAT"

    assert result[0].rate == Decimal(
        "7"
    )

    assert result[0].amount == Decimal(
        "549.36"
    )

    print(
        "PASS: HLD tax extraction and "
        "withholding protection"
    )

    # =========================================================================
    # 3. CGST / SGST
    # =========================================================================

    indian_text = """
    CGST 9% 900.00
    SGST 9% 900.00
    Total invoice amount 11,800.00
    """

    result = extractor.extract(
        indian_text
    )

    print("\nTEST: Indian GST")

    for item in result:
        print(item.to_dict())

    assert len(result) == 2

    assert result[0].name == "CGST"
    assert result[0].rate == Decimal(
        "9"
    )
    assert result[0].amount == Decimal(
        "900.00"
    )

    assert result[1].name == "SGST"
    assert result[1].rate == Decimal(
        "9"
    )
    assert result[1].amount == Decimal(
        "900.00"
    )

    print(
        "PASS: CGST/SGST extraction"
    )

    # =========================================================================
    # 4. IGST
    # =========================================================================

    igst_text = """
    Taxable Value: 10,000.00
    IGST 18%: 1,800.00
    """

    result = extractor.extract(
        igst_text
    )

    assert len(result) == 1

    assert result[0].name == "IGST"

    assert result[0].rate == Decimal(
        "18"
    )

    assert result[0].amount == Decimal(
        "1800.00"
    )

    print(
        "PASS: IGST extraction"
    )

    # =========================================================================
    # 5. EUROPEAN FORMAT
    # =========================================================================

    european_text = """
    Netto: 1.234,56
    MwSt 19%: 234,57
    Brutto: 1.469,13
    """

    result = extractor.extract(
        european_text
    )

    print("\nTEST: European format")

    for item in result:
        print(item.to_dict())

    assert len(result) == 1

    assert result[0].name == "MWST"

    assert result[0].rate == Decimal(
        "19"
    )

    assert result[0].amount == Decimal(
        "234.57"
    )

    print(
        "PASS: European number format"
    )

    # =========================================================================
    # 6. CURRENCY
    # =========================================================================

    currency_text = """
    VAT 20% $250.00
    """

    result = extractor.extract(
        currency_text
    )

    assert len(result) == 1

    assert result[0].rate == Decimal(
        "20"
    )

    assert result[0].amount == Decimal(
        "250.00"
    )

    print(
        "PASS: currency-prefixed tax amount"
    )

    # =========================================================================
    # 7. NO RATE
    # =========================================================================

    no_rate_text = """
    VAT Amount: 150.00
    """

    result = extractor.extract(
        no_rate_text
    )

    assert len(result) == 1

    assert result[0].name == "VAT"

    assert result[0].rate is None

    assert result[0].amount == Decimal(
        "150.00"
    )

    print(
        "PASS: tax without explicit rate"
    )

    # =========================================================================
    # 8. NO AMOUNT
    # =========================================================================

    no_amount_text = """
    VAT Rate: 18%
    """

    result = extractor.extract(
        no_amount_text
    )

    assert len(result) == 1

    assert result[0].name == "VAT"

    assert result[0].rate == Decimal(
        "18"
    )

    assert result[0].amount is None

    print(
        "PASS: tax without explicit amount"
    )

    # =========================================================================
    # 9. TAX ID PROTECTION
    # =========================================================================

    tax_id_text = """
    Supplier Tax ID: IN123456789
    Tax Registration Number: 123456789
    """

    result = extractor.extract(
        tax_id_text
    )

    assert len(result) == 0

    print(
        "PASS: tax ID / registration protection"
    )

    # =========================================================================
    # 10. TOTAL TAX PROTECTION
    # =========================================================================

    total_tax_text = """
    VAT 10%: 100.00
    GST 5%: 50.00
    Total Tax: 150.00
    """

    result = extractor.extract(
        total_tax_text
    )

    assert len(result) == 2

    assert result[0].name == "VAT"
    assert result[1].name == "GST"

    print(
        "PASS: total-tax protection"
    )

    # =========================================================================
    # 11. NO CALCULATION / INVENTION
    # =========================================================================

    no_calculation_text = """
    Subtotal: 1,000.00
    VAT 18%
    """

    result = extractor.extract(
        no_calculation_text
    )

    assert len(result) == 1

    assert result[0].name == "VAT"

    assert result[0].rate == Decimal(
        "18"
    )

    assert result[0].amount is None

    print(
        "PASS: no tax amount invention"
    )

    # =========================================================================
    # 12. EMPTY INPUT
    # =========================================================================

    result = extractor.extract("")

    assert result == []

    print(
        "PASS: empty input"
    )

    # =========================================================================
    # FINAL
    # =========================================================================

    print()
    print("=" * 80)
    print("ALL TAX EXTRACTOR TESTS PASSED")
    print("=" * 80)