from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Dict, List, Optional


# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class ChargeItem:
    name: Optional[str] = None
    amount: Optional[Decimal] = None
    raw_text: Optional[str] = None
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)

        for key, value in result.items():
            if isinstance(value, Decimal):
                result[key] = str(value)

        return result


# =============================================================================
# CHARGE EXTRACTOR
# =============================================================================

class ChargeExtractor:
    """
    Extract explicitly printed charges from payable/invoice documents.

    Supported examples:

        Freight: 100.00
        Shipping Charge: 50.00
        Delivery Charge: 25.00
        Handling Charge: 10.00
        Insurance Charge: 20.00
        Packaging: 15.00
        Transportation: 100.00
        Service Charge: 30.00
        Surcharge: 5.00

    Important:
        - Never calculate a charge amount.
        - Never invent missing values.
        - Do not confuse taxes with charges.
        - Do not confuse discounts with charges.
        - Do not extract subtotal/grand total rows.
        - Preserve international number formats.
    """

    # =========================================================================
    # CHARGE ALIASES
    # =========================================================================

    CHARGE_ALIASES = {
        "shipping charge": "SHIPPING_CHARGE",
        "delivery charge": "DELIVERY_CHARGE",
        "handling charge": "HANDLING_CHARGE",
        "insurance charge": "INSURANCE_CHARGE",
        "packing charge": "PACKING_CHARGE",
        "transport charge": "TRANSPORT_CHARGE",
        "service charge": "SERVICE_CHARGE",

        "additional charges": "ADDITIONAL_CHARGES",
        "additional charge": "ADDITIONAL_CHARGE",

        "other charges": "OTHER_CHARGES",
        "other charge": "OTHER_CHARGE",

        "freight": "FREIGHT",
        "shipping": "SHIPPING",
        "delivery": "DELIVERY",
        "handling": "HANDLING",
        "insurance": "INSURANCE",
        "packaging": "PACKAGING",
        "transportation": "TRANSPORTATION",
        "surcharge": "SURCHARGE",
    }

    CHARGE_TERMS = tuple(
        CHARGE_ALIASES.keys()
    )

    # =========================================================================
    # EXCLUSION PATTERNS
    # =========================================================================

    EXCLUDE_PATTERNS = [
        r"\bsubtotal\b",
        r"\bgrand\s+total\b",
        r"\btotal\b",

        r"\btax\b",
        r"\bvat\b",
        r"\bgst\b",
        r"\bcgst\b",
        r"\bsgst\b",
        r"\bigst\b",

        r"\bdiscount\b",
        r"\brebate\b",
        r"\ballowance\b",

        r"\bwithholding\b",
    ]

    # =========================================================================
    # NUMBER PATTERNS
    # =========================================================================

    NUMBER_PATTERN = re.compile(
        r"(?<![A-Za-z0-9])"
        r"-?\d[\d.,]*"
        r"(?![A-Za-z0-9])"
    )

    CURRENCY_PATTERN = re.compile(
        r"(?:"
        r"[$€£¥₹]"
        r"|"
        r"(?:USD|EUR|GBP|JPY|INR|THB|CNY|CAD|AUD|SGD|HKD)"
        r")"
        r"\s*"
        r"(?P<number>-?(?:\d[\d.,]*))",
        re.IGNORECASE,
    )

    # =========================================================================
    # CONSTRUCTOR
    # =========================================================================

    def __init__(self) -> None:
        pass

    # =========================================================================
    # NUMBER PARSER
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
            # 1.234,56
            #
            # -> 1234.56

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
            # 1,234.56
            #
            # -> 1234.56

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

            # 500,00
            if (
                len(parts) == 2
                and len(parts[-1]) in (1, 2)
            ):

                value = (
                    parts[0]
                    + "."
                    + parts[-1]
                )

            # 1,234
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
    # NORMALIZE
    # =========================================================================

    @staticmethod
    def _normalize(
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
            for pattern in self.EXCLUDE_PATTERNS
        )

    # =========================================================================
    # CHARGE NAME
    # =========================================================================

    def _find_charge_name(
        self,
        line: str,
    ) -> Optional[str]:

        lower = line.lower()

        # Longest aliases first.

        for alias in sorted(
            self.CHARGE_ALIASES,
            key=len,
            reverse=True,
        ):

            if re.search(
                rf"(?<![a-z])"
                rf"{re.escape(alias)}"
                rf"(?![a-z])",
                lower,
            ):

                return self.CHARGE_ALIASES[
                    alias
                ]

        return None

    # =========================================================================
    # AMOUNT EXTRACTION
    # =========================================================================

    def _extract_amount(
        self,
        line: str,
    ) -> Optional[Decimal]:
        """
        Extract only an explicitly printed amount.

        Examples:

            Freight: 100.00
            Shipping ₹250.00
            Insurance USD 75.50

        No calculation is performed.
        """

        # ---------------------------------------------------------------------
        # Currency-prefixed amount
        # ---------------------------------------------------------------------

        currency_matches = list(
            self.CURRENCY_PATTERN.finditer(
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
        # Generic numbers
        # ---------------------------------------------------------------------

        candidates: List[Decimal] = []

        for match in self.NUMBER_PATTERN.finditer(
            line
        ):

            number = self._number(
                match.group(0)
            )

            if number is not None:
                candidates.append(
                    number
                )

        if not candidates:
            return None

        return candidates[-1]

    # =========================================================================
    # SINGLE LINE
    # =========================================================================

    def _extract_one(
        self,
        line: str,
    ) -> Optional[ChargeItem]:

        line = self._normalize(line)

        if not line:
            return None

        # Protect totals, taxes,
        # discounts and withholding.

        if self._is_excluded(line):
            return None

        name = self._find_charge_name(
            line
        )

        if name is None:
            return None

        amount = self._extract_amount(
            line
        )

        confidence = 0.55

        if amount is not None:
            confidence += 0.44

        confidence = min(
            round(
                confidence,
                2,
            ),
            0.99,
        )

        return ChargeItem(
            name=name,
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
    ) -> List[ChargeItem]:

        if not text:
            return []

        results: List[ChargeItem] = []

        for line in text.splitlines():

            item = self._extract_one(
                line
            )

            if item is not None:
                results.append(item)

        return results


# =============================================================================
# TEST SUITE
# =============================================================================

if __name__ == "__main__":

    print("=" * 80)
    print("CHARGE EXTRACTOR TESTS")
    print("=" * 80)

    extractor = ChargeExtractor()

    # =========================================================================
    # 1. STANDARD
    # =========================================================================

    standard = """
    Subtotal: 1000.00
    Freight: 50.00
    Grand Total: 1050.00
    """

    result = extractor.extract(
        standard
    )

    print("\nTEST: standard")

    for item in result:
        print(item.to_dict())

    assert len(result) == 1

    assert result[0].name == "FREIGHT"

    assert result[0].amount == Decimal(
        "50.00"
    )

    print(
        "PASS: standard charge extraction"
    )

    # =========================================================================
    # 2. HLD FINANCIAL ROWS
    # =========================================================================

    hld = """
    Staff 2 Units X 6 Days 12 600.00
    Management Fee 9% 648.00
    Freight 100.00
    VAT 7% 549.36
    Withholding tax 235.44
    Total payment 8161.92
    """

    result = extractor.extract(
        hld
    )

    print("\nTEST: HLD")

    for item in result:
        print(item.to_dict())

    assert len(result) == 1

    assert result[0].name == "FREIGHT"

    assert result[0].amount == Decimal(
        "100.00"
    )

    print(
        "PASS: HLD charge isolation"
    )

    # =========================================================================
    # 3. NAMED CHARGES
    # =========================================================================

    named = """
    Shipping Charge: 25.00
    Delivery Charge: 30.00
    Handling Charge: 15.00
    Insurance Charge: 40.00
    """

    result = extractor.extract(
        named
    )

    print("\nTEST: named charges")

    for item in result:
        print(item.to_dict())

    assert len(result) == 4

    assert result[0].name == (
        "SHIPPING_CHARGE"
    )

    assert result[1].name == (
        "DELIVERY_CHARGE"
    )

    assert result[2].name == (
        "HANDLING_CHARGE"
    )

    assert result[3].name == (
        "INSURANCE_CHARGE"
    )

    assert result[0].amount == Decimal(
        "25.00"
    )

    assert result[1].amount == Decimal(
        "30.00"
    )

    print(
        "PASS: named charges"
    )

    # =========================================================================
    # 4. EUROPEAN FORMAT
    # =========================================================================

    european = """
    Transportation: 1.234,56
    """

    result = extractor.extract(
        european
    )

    assert len(result) == 1

    assert result[0].name == (
        "TRANSPORTATION"
    )

    assert result[0].amount == Decimal(
        "1234.56"
    )

    print(
        "PASS: European number format"
    )

    # =========================================================================
    # 5. CURRENCY
    # =========================================================================

    currency = """
    Surcharge: ₹150.00
    """

    result = extractor.extract(
        currency
    )

    assert len(result) == 1

    assert result[0].name == (
        "SURCHARGE"
    )

    assert result[0].amount == Decimal(
        "150.00"
    )

    print(
        "PASS: currency-prefixed amount"
    )

    # =========================================================================
    # 6. NO AMOUNT
    # =========================================================================

    no_amount = """
    Freight
    """

    result = extractor.extract(
        no_amount
    )

    assert len(result) == 1

    assert result[0].name == "FREIGHT"

    assert result[0].amount is None

    print(
        "PASS: charge without explicit amount"
    )

    # =========================================================================
    # 7. NO INVENTION
    # =========================================================================

    no_invention = """
    Subtotal: 1000.00
    Freight
    """

    result = extractor.extract(
        no_invention
    )

    assert len(result) == 1

    assert result[0].amount is None

    print(
        "PASS: no charge amount invention"
    )

    # =========================================================================
    # 8. IGNORE TAX / DISCOUNT / TOTAL
    # =========================================================================

    ignored = """
    Tax 18%: 180.00
    VAT 18%: 180.00
    Discount 10%: 100.00
    Subtotal: 1000.00
    Grand Total: 1260.00
    """

    result = extractor.extract(
        ignored
    )

    assert result == []

    print(
        "PASS: tax/discount/total protection"
    )

    # =========================================================================
    # 9. MULTIPLE CHARGES
    # =========================================================================

    multiple = """
    Freight: 50.00
    Shipping: 25.00
    Packaging: 10.00
    Surcharge: 5.00
    """

    result = extractor.extract(
        multiple
    )

    assert len(result) == 4

    assert result[0].amount == Decimal(
        "50.00"
    )

    assert result[1].amount == Decimal(
        "25.00"
    )

    assert result[2].amount == Decimal(
        "10.00"
    )

    assert result[3].amount == Decimal(
        "5.00"
    )

    print(
        "PASS: multiple charges"
    )

    # =========================================================================
    # 10. EMPTY INPUT
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
    print("ALL CHARGE EXTRACTOR TESTS PASSED")
    print("=" * 80)