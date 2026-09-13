from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Dict, List, Optional


@dataclass
class DiscountItem:
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


class DiscountExtractor:
    """
    Extract explicitly printed discounts from invoice/payable text.

    Important:
    - Never calculate a discount amount from a rate.
    - Never calculate a rate from an amount.
    - Preserve explicitly printed amounts.
    - Support common international number formats.
    - Ignore discount identifiers/codes.
    - Ignore unrelated financial rows.
    """

    DISCOUNT_ALIASES = {
        "less discount": "LESS_DISCOUNT",
        "trade discount": "TRADE_DISCOUNT",
        "volume discount": "VOLUME_DISCOUNT",
        "cash discount": "CASH_DISCOUNT",
        "discounts": "DISCOUNT",
        "discount": "DISCOUNT",
        "rebate": "REBATE",
        "allowance": "ALLOWANCE",
    }

    DISCOUNT_TERMS = tuple(DISCOUNT_ALIASES.keys())

    EXCLUDED_PATTERNS = [
        r"\bdiscount\s+(?:code|id|number|no\.?|reference|ref)\b",
        r"\bdiscounted\s+(?:price|amount|total)\b",
    ]

    RATE_PATTERN = re.compile(
        r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*%"
    )

    NUMBER_PATTERN = re.compile(
        r"(?<![A-Za-z0-9])-?\d[\d.,]*(?![A-Za-z0-9])"
    )

    CURRENCY_PREFIX_PATTERN = re.compile(
        r"(?:[$€£¥₹]|"
        r"(?:USD|EUR|GBP|JPY|INR|THB|CNY|CAD|AUD|SGD|HKD))"
        r"\s*(?P<number>-?(?:\d[\d.,]*))",
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
    # DISCOUNT NAME
    # =========================================================================

    def _find_discount_name(
        self,
        line: str,
    ) -> Optional[str]:

        lower = line.lower()

        # Longest terms first.
        #
        # Example:
        # "Trade Discount"
        #
        # should match TRADE_DISCOUNT,
        # not generic DISCOUNT.

        for alias in sorted(
            self.DISCOUNT_ALIASES,
            key=len,
            reverse=True,
        ):

            if re.search(
                rf"(?<![a-z])"
                rf"{re.escape(alias)}"
                rf"(?![a-z])",
                lower,
            ):

                return self.DISCOUNT_ALIASES[
                    alias
                ]

        return None

    # =========================================================================
    # EXCLUDED ROWS
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
    # AMOUNT
    # =========================================================================

    def _extract_amount(
        self,
        line: str,
        rate: Optional[Decimal],
    ) -> Optional[Decimal]:
        """
        Extract only an amount explicitly printed in the document.

        Examples:

            Discount 10%: 100.00
                -> amount = 100.00

            Discount: 100.00
                -> amount = 100.00

            Discount 100.00 (10%)
                -> amount = 100.00

            Discount 10%
                -> amount = None

        No calculation is performed.
        """

        # ---------------------------------------------------------------------
        # Currency-prefixed amount
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
        # Generic numeric values
        # ---------------------------------------------------------------------

        candidates: List[Decimal] = []

        for match in self.NUMBER_PATTERN.finditer(
            line
        ):

            number = self._number(
                match.group(0)
            )

            if number is not None:
                candidates.append(number)

        if not candidates:
            return None

        # ---------------------------------------------------------------------
        # Remove percentage value.
        # ---------------------------------------------------------------------

        if rate is not None:

            rate_match = (
                self.RATE_PATTERN.search(
                    line
                )
            )

            if rate_match:

                rate_value = self._number(
                    rate_match.group(1)
                )

                filtered: List[Decimal] = []

                removed = False

                for candidate in candidates:

                    if (
                        not removed
                        and rate_value is not None
                        and candidate == rate_value
                    ):

                        removed = True
                        continue

                    filtered.append(
                        candidate
                    )

                candidates = filtered

        if not candidates:
            return None

        # Last remaining explicit numeric
        # value is treated as the amount.
        return candidates[-1]

    # =========================================================================
    # SINGLE LINE
    # =========================================================================

    def _extract_one(
        self,
        line: str,
    ) -> Optional[DiscountItem]:

        line = self._normalize_line(
            line
        )

        if not line:
            return None

        if self._is_excluded(line):
            return None

        name = self._find_discount_name(
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

        # ---------------------------------------------------------------------
        # Confidence
        # ---------------------------------------------------------------------

        confidence = 0.50

        if name:
            confidence += 0.20

        if rate is not None:
            confidence += 0.10

        if amount is not None:
            confidence += 0.20

        confidence = min(
            round(
                confidence,
                2,
            ),
            0.99,
        )

        return DiscountItem(
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
    ) -> List[DiscountItem]:

        if not text:
            return []

        results: List[DiscountItem] = []

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
    print("DISCOUNT EXTRACTOR TESTS")
    print("=" * 80)

    extractor = DiscountExtractor()

    # =========================================================================
    # 1. STANDARD DISCOUNT
    # =========================================================================

    standard = """
    Subtotal: 1000.00
    Discount 10%: 100.00
    Tax: 162.00
    Grand Total: 1062.00
    """

    result = extractor.extract(
        standard
    )

    for item in result:
        print(item.to_dict())

    assert len(result) == 1

    assert result[0].name == "DISCOUNT"

    assert result[0].rate == Decimal(
        "10"
    )

    assert result[0].amount == Decimal(
        "100.00"
    )

    print(
        "PASS: standard discount extraction"
    )

    # =========================================================================
    # 2. HLD-STYLE FINANCIAL ROWS
    # =========================================================================

    hld = """
    Staff 2 Units X 6 Days 12 600.00
    Management Fee 9% 648.00
    Discount 5% 360.00
    VAT 7% 549.36
    Withholding tax 235.44
    Total payment 8161.92
    """

    result = extractor.extract(
        hld
    )

    assert len(result) == 1

    assert result[0].name == "DISCOUNT"

    assert result[0].rate == Decimal(
        "5"
    )

    assert result[0].amount == Decimal(
        "360.00"
    )

    print(
        "PASS: HLD discount isolation"
    )

    # =========================================================================
    # 3. NAMED DISCOUNT TYPES
    # =========================================================================

    named = """
    Trade Discount 12%: 120.00
    Volume Discount 5%: 50.00
    Cash Discount: 25.00
    Rebate: 30.00
    Allowance: 15.00
    """

    result = extractor.extract(
        named
    )

    assert len(result) == 5

    assert result[0].name == (
        "TRADE_DISCOUNT"
    )

    assert result[1].name == (
        "VOLUME_DISCOUNT"
    )

    assert result[2].name == (
        "CASH_DISCOUNT"
    )

    assert result[3].name == "REBATE"

    assert result[4].name == "ALLOWANCE"

    print(
        "PASS: named discount types"
    )

    # =========================================================================
    # 4. UNSUPPORTED LANGUAGE
    # =========================================================================

    european_unsupported = """
    Zwischensumme: 1.234,56
    Rabatt 10%: 123,46
    """

    result = extractor.extract(
        european_unsupported
    )

    # "Rabatt" is not part of the current
    # supported terminology, therefore
    # nothing is invented.
    assert result == []

    print(
        "PASS: unsupported language is not invented"
    )

    # =========================================================================
    # 5. EUROPEAN NUMBER FORMAT
    # =========================================================================

    european_supported = """
    Subtotal: 1.234,56
    Discount 10%: 123,46
    """

    result = extractor.extract(
        european_supported
    )

    assert len(result) == 1

    assert result[0].rate == Decimal(
        "10"
    )

    assert result[0].amount == Decimal(
        "123.46"
    )

    print(
        "PASS: European number format"
    )

    # =========================================================================
    # 6. CURRENCY
    # =========================================================================

    currency = """
    Discount 15%: ₹150.00
    """

    result = extractor.extract(
        currency
    )

    assert len(result) == 1

    assert result[0].rate == Decimal(
        "15"
    )

    assert result[0].amount == Decimal(
        "150.00"
    )

    print(
        "PASS: currency-prefixed amount"
    )

    # =========================================================================
    # 7. NO RATE
    # =========================================================================

    no_rate = """
    Discount Amount: 75.00
    """

    result = extractor.extract(
        no_rate
    )

    assert len(result) == 1

    assert result[0].rate is None

    assert result[0].amount == Decimal(
        "75.00"
    )

    print(
        "PASS: discount without explicit rate"
    )

    # =========================================================================
    # 8. NO AMOUNT
    # =========================================================================

    no_amount = """
    Discount Rate: 10%
    """

    result = extractor.extract(
        no_amount
    )

    assert len(result) == 1

    assert result[0].rate == Decimal(
        "10"
    )

    assert result[0].amount is None

    print(
        "PASS: discount without explicit amount"
    )

    # =========================================================================
    # 9. NO CALCULATION / INVENTION
    # =========================================================================

    no_calculation = """
    Subtotal: 2000.00
    Discount 10%
    """

    result = extractor.extract(
        no_calculation
    )

    assert len(result) == 1

    assert result[0].rate == Decimal(
        "10"
    )

    assert result[0].amount is None

    print(
        "PASS: no discount amount invention"
    )

    # =========================================================================
    # 10. OTHER FINANCIAL ROWS
    # =========================================================================

    financial = """
    Subtotal: 1000.00
    Tax 18%: 180.00
    Freight: 50.00
    Grand Total: 1230.00
    """

    result = extractor.extract(
        financial
    )

    assert result == []

    print(
        "PASS: non-discount financial rows ignored"
    )

    # =========================================================================
    # 11. DISCOUNT CODE PROTECTION
    # =========================================================================

    code = """
    Discount Code: SAVE10
    Discount Reference: DISC-100
    """

    result = extractor.extract(
        code
    )

    assert result == []

    print(
        "PASS: discount identifier protection"
    )

    # =========================================================================
    # 12. MULTIPLE DISCOUNTS
    # =========================================================================

    multiple = """
    Trade Discount 10%: 100.00
    Cash Discount 2%: 18.00
    """

    result = extractor.extract(
        multiple
    )

    assert len(result) == 2

    assert result[0].amount == Decimal(
        "100.00"
    )

    assert result[1].amount == Decimal(
        "18.00"
    )

    print(
        "PASS: multiple discounts"
    )

    # =========================================================================
    # 13. EMPTY INPUT
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
    print("ALL DISCOUNT EXTRACTOR TESTS PASSED")
    print("=" * 80)