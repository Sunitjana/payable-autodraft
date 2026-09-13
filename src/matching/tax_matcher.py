# src/matching/tax_matcher.py

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Dict, List, Optional


# =============================================================================
# RESULT
# =============================================================================

@dataclass
class TaxMatchResult:
    matched: bool
    tax: Optional[Dict[str, Any]]
    confidence: float
    match_type: Optional[str] = None
    evidence: List[str] = field(default_factory=list)


# =============================================================================
# TAX MATCHER
# =============================================================================

class TaxMatcher:
    """
    Conservative tax master-data matcher.

    Priority:

        1. Exact tax_type_code
        2. Exact tax name + rate
        3. Exact tax name
        4. Unique tax rate

    Safety:

        - Never invent a tax_type_code.
        - Duplicate tax codes are rejected.
        - Rate-only matching is accepted only when exactly one
          master record matches.
        - No fuzzy matching.
        - Rate tolerance is explicit.
    """

    # -------------------------------------------------------------------------
    # IMPORTANT:
    # tax_type_code is the canonical field required by AUTODRAFT_SCHEMA.md.
    # -------------------------------------------------------------------------

    CODE_KEYS = [
        "tax_type_code",
        "taxTypeCode",
        "tax_code",
        "taxCode",
        "code",
        "tax_id",
        "taxId",
    ]

    NAME_KEYS = [
        "tax_name",
        "taxName",
        "name",
        "description",
    ]

    RATE_KEYS = [
        "rate",
        "tax_rate",
        "taxRate",
        "percentage",
        "tax_percentage",
    ]

    # =========================================================================
    # CONSTRUCTOR
    # =========================================================================

    def __init__(
        self,
        taxes: Optional[List[Dict[str, Any]]] = None,
        rate_tolerance: Decimal = Decimal("0.01"),
    ) -> None:

        self.taxes = taxes or []

        self.rate_tolerance = Decimal(
            str(rate_tolerance)
        )

        if self.rate_tolerance < 0:
            raise ValueError(
                "rate_tolerance must be non-negative"
            )

    # =========================================================================
    # NORMALIZATION
    # =========================================================================

    @staticmethod
    def normalize(
        value: Any,
    ) -> str:
        """
        Normalize tax codes/names.

        Examples:

            VAT
            vat
            VAT-7
            VAT 7

        """

        if value is None:
            return ""

        return re.sub(
            r"[^a-zA-Z0-9]+",
            "",
            str(value),
        ).casefold()

    # =========================================================================
    # RATE PARSER
    # =========================================================================

    @staticmethod
    def parse_rate(
        value: Any,
    ) -> Optional[Decimal]:
        """
        Parse a tax percentage safely.

        Supported:

            7
            7.0
            7.00%
            7,5%
            " 18 % "

        For comma decimal notation:

            7,5 -> 7.5

        For mixed thousands/decimal notation:

            1,234.56 -> 1234.56

        No calculation is performed.
        """

        if value is None:
            return None

        if isinstance(value, Decimal):
            return value

        if isinstance(value, bool):
            return None

        if isinstance(
            value,
            (int, float),
        ):
            try:
                return Decimal(
                    str(value)
                )
            except InvalidOperation:
                return None

        text = str(value).strip()

        if not text:
            return None

        text = text.replace(
            "%",
            "",
        )

        text = text.replace(
            " ",
            "",
        )

        if not text:
            return None

        # -------------------------------------------------------------
        # European decimal notation
        #
        # 7,5 -> 7.5
        # 12,50 -> 12.50
        # -------------------------------------------------------------

        if (
            "," in text
            and "." not in text
        ):
            text = text.replace(
                ",",
                ".",
            )

        # -------------------------------------------------------------
        # Mixed notation
        #
        # 1,234.56 -> 1234.56
        # -------------------------------------------------------------

        elif (
            "," in text
            and "." in text
        ):
            text = text.replace(
                ",",
                "",
            )

        try:

            return Decimal(
                text
            )

        except (
            InvalidOperation,
            ValueError,
        ):

            return None

    # =========================================================================
    # FIRST VALUE
    # =========================================================================

    @staticmethod
    def _first_value(
        record: Dict[str, Any],
        keys: List[str],
    ) -> Any:

        for key in keys:

            if (
                key in record
                and record[key]
                not in (
                    None,
                    "",
                )
            ):
                return record[key]

        return None

    # =========================================================================
    # CODE CANDIDATES
    # =========================================================================

    def _find_code_candidates(
        self,
        tax_code: str,
    ) -> List[Dict[str, Any]]:

        normalized_code = self.normalize(
            tax_code
        )

        if not normalized_code:
            return []

        candidates = []

        for tax in self.taxes:

            master_code = self._first_value(
                tax,
                self.CODE_KEYS,
            )

            if not master_code:
                continue

            if (
                self.normalize(master_code)
                == normalized_code
            ):
                candidates.append(
                    tax
                )

        return candidates

    # =========================================================================
    # NAME MATCH
    # =========================================================================

    def _name_matches(
        self,
        extracted_name: Optional[str],
        master_name: Any,
    ) -> bool:

        if not extracted_name:
            return False

        if not master_name:
            return False

        return (
            self.normalize(
                extracted_name
            )
            == self.normalize(
                master_name
            )
        )

    # =========================================================================
    # RATE MATCH
    # =========================================================================

    def _rate_matches(
        self,
        extracted_rate: Optional[Decimal],
        master_rate: Optional[Decimal],
    ) -> bool:

        if (
            extracted_rate is None
            or master_rate is None
        ):
            return False

        return (
            abs(
                extracted_rate
                - master_rate
            )
            <= self.rate_tolerance
        )

    # =========================================================================
    # MAIN MATCH
    # =========================================================================

    def match(
        self,
        tax_name: Optional[str] = None,
        tax_rate: Optional[Decimal] = None,
        tax_code: Optional[str] = None,
    ) -> TaxMatchResult:

        # =====================================================================
        # NORMALIZE INPUT RATE
        # =====================================================================

        parsed_rate = self.parse_rate(
            tax_rate
        )

        # =====================================================================
        # 1. EXACT TAX TYPE CODE
        # =====================================================================

        if tax_code:

            code_candidates = (
                self._find_code_candidates(
                    tax_code
                )
            )

            # -------------------------------------------------------------
            # Exactly one master record
            # -------------------------------------------------------------

            if len(code_candidates) == 1:

                tax = code_candidates[0]

                return TaxMatchResult(
                    matched=True,
                    tax=tax,
                    confidence=1.0,
                    match_type=(
                        "exact_tax_type_code"
                    ),
                    evidence=[
                        (
                            "Tax type code matched: "
                            f"{tax_code}"
                        )
                    ],
                )

            # -------------------------------------------------------------
            # Duplicate code = unsafe
            # -------------------------------------------------------------

            if len(code_candidates) > 1:

                return TaxMatchResult(
                    matched=False,
                    tax=None,
                    confidence=0.0,
                    match_type="ambiguous_code",
                    evidence=[
                        (
                            "Multiple tax master records "
                            "share the supplied tax type code."
                        )
                    ],
                )

        # =====================================================================
        # 2. NAME / RATE MATCHING
        # =====================================================================

        candidates = []

        for tax in self.taxes:

            master_name = self._first_value(
                tax,
                self.NAME_KEYS,
            )

            master_rate = self.parse_rate(
                self._first_value(
                    tax,
                    self.RATE_KEYS,
                )
            )

            name_match = self._name_matches(
                tax_name,
                master_name,
            )

            rate_match = self._rate_matches(
                parsed_rate,
                master_rate,
            )

            # -------------------------------------------------------------
            # Name + rate
            # -------------------------------------------------------------

            if (
                name_match
                and rate_match
            ):

                candidates.append(
                    (
                        1.00,
                        tax,
                        "exact_tax_name_and_rate",
                    )
                )

            # -------------------------------------------------------------
            # Name only
            # -------------------------------------------------------------

            elif name_match:

                candidates.append(
                    (
                        0.90,
                        tax,
                        "exact_tax_name",
                    )
                )

            # -------------------------------------------------------------
            # Rate only
            # -------------------------------------------------------------

            elif rate_match:

                candidates.append(
                    (
                        0.85,
                        tax,
                        "exact_tax_rate",
                    )
                )

        # =====================================================================
        # 3. NO MATCH
        # =====================================================================

        if not candidates:

            return TaxMatchResult(
                matched=False,
                tax=None,
                confidence=0.0,
                match_type="not_found",
                evidence=[
                    (
                        "No reliable tax master "
                        "match found."
                    )
                ],
            )

        # =====================================================================
        # 4. SINGLE CANDIDATE
        # =====================================================================

        if len(candidates) == 1:

            score, tax, match_type = (
                candidates[0]
            )

            return TaxMatchResult(
                matched=True,
                tax=tax,
                confidence=score,
                match_type=match_type,
                evidence=[
                    (
                        "Unique tax master "
                        "record matched."
                    )
                ],
            )

        # =====================================================================
        # 5. MULTIPLE CANDIDATES
        # =====================================================================

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        best = candidates[0]
        second = candidates[1]

        best_score = best[0]
        second_score = second[0]

        # -------------------------------------------------------------
        # A clearly stronger match is safe.
        #
        # Example:
        #
        # VAT + 7% -> 1.00
        # VAT       -> 0.90
        #
        # -------------------------------------------------------------

        if best_score > second_score:

            score, tax, match_type = best

            return TaxMatchResult(
                matched=True,
                tax=tax,
                confidence=score,
                match_type=match_type,
                evidence=[
                    (
                        "Best unique tax master "
                        "match."
                    )
                ],
            )

        # =====================================================================
        # 6. TIE = AMBIGUOUS
        # =====================================================================

        return TaxMatchResult(
            matched=False,
            tax=None,
            confidence=0.0,
            match_type="ambiguous",
            evidence=[
                (
                    "Multiple tax master records "
                    "matched with equal confidence."
                )
            ],
        )


# =============================================================================
# LOCAL TESTS
# =============================================================================

if __name__ == "__main__":

    print("=" * 80)
    print("TAX MATCHER TESTS")
    print("=" * 80)

    tax_master = [
        {
            "tax_type_code": "VAT",
            "tax_name": "VAT",
            "rate": "7%",
        },
        {
            "tax_type_code": "GST",
            "tax_name": "GST",
            "rate": "18%",
        },
        {
            "tax_type_code": "CGST",
            "tax_name": "CGST",
            "rate": "9%",
        },
        {
            "tax_type_code": "SGST",
            "tax_name": "SGST",
            "rate": "9%",
        },
    ]

    matcher = TaxMatcher(
        taxes=tax_master
    )

    # =========================================================================
    # 1. EXACT TAX TYPE CODE
    # =========================================================================

    result = matcher.match(
        tax_code="VAT"
    )

    print("\nTEST: Exact tax type code")
    print(result)

    assert result.matched is True
    assert result.tax["tax_type_code"] == "VAT"
    assert result.confidence == 1.0
    assert (
        result.match_type
        == "exact_tax_type_code"
    )

    print("PASS: exact tax type code")

    # =========================================================================
    # 2. CASE / FORMAT NORMALIZATION
    # =========================================================================

    result = matcher.match(
        tax_code=" vat "
    )

    print("\nTEST: Normalized tax code")
    print(result)

    assert result.matched is True
    assert result.tax["tax_type_code"] == "VAT"

    print("PASS: normalized tax code")

    # =========================================================================
    # 3. NAME + RATE
    # =========================================================================

    result = matcher.match(
        tax_name="VAT",
        tax_rate="7%",
    )

    print("\nTEST: Tax name + rate")
    print(result)

    assert result.matched is True
    assert result.tax["tax_type_code"] == "VAT"
    assert result.confidence == 1.0
    assert (
        result.match_type
        == "exact_tax_name_and_rate"
    )

    print("PASS: tax name + rate")

    # =========================================================================
    # 4. NAME + EUROPEAN RATE
    # =========================================================================

    result = matcher.match(
        tax_name="VAT",
        tax_rate="7,00%",
    )

    print("\nTEST: European decimal rate")
    print(result)

    assert result.matched is True
    assert result.tax["tax_type_code"] == "VAT"

    print("PASS: European decimal rate")

    # =========================================================================
    # 5. NAME ONLY
    # =========================================================================

    result = matcher.match(
        tax_name="GST"
    )

    print("\nTEST: Tax name only")
    print(result)

    assert result.matched is True
    assert result.tax["tax_type_code"] == "GST"
    assert result.confidence == 0.90

    print("PASS: tax name only")

    # =========================================================================
    # 6. UNIQUE RATE
    # =========================================================================

    result = matcher.match(
        tax_rate="18%"
    )

    print("\nTEST: Unique tax rate")
    print(result)

    assert result.matched is True
    assert result.tax["tax_type_code"] == "GST"
    assert result.confidence == 0.85

    print("PASS: unique tax rate")

    # =========================================================================
    # 7. AMBIGUOUS RATE
    # =========================================================================

    result = matcher.match(
        tax_rate="9%"
    )

    print("\nTEST: Ambiguous rate")
    print(result)

    assert result.matched is False
    assert result.tax is None
    assert result.match_type == "ambiguous"

    print("PASS: ambiguous rate rejected")

    # =========================================================================
    # 8. WRONG RATE + CORRECT NAME
    # =========================================================================

    result = matcher.match(
        tax_name="VAT",
        tax_rate="5%",
    )

    print("\nTEST: Correct name but wrong rate")
    print(result)

    assert result.matched is True
    assert result.tax["tax_type_code"] == "VAT"
    assert result.confidence == 0.90

    print("PASS: name-only fallback")

    # =========================================================================
    # 9. UNKNOWN TAX
    # =========================================================================

    result = matcher.match(
        tax_name="UnknownTax"
    )

    print("\nTEST: Unknown tax")
    print(result)

    assert result.matched is False
    assert result.tax is None
    assert result.match_type == "not_found"

    print("PASS: unknown tax rejected")

    # =========================================================================
    # 10. UNKNOWN CODE
    # =========================================================================

    result = matcher.match(
        tax_code="UNKNOWN"
    )

    print("\nTEST: Unknown tax code")
    print(result)

    assert result.matched is False
    assert result.tax is None

    print("PASS: unknown tax code rejected")

    # =========================================================================
    # 11. DUPLICATE TAX CODE
    # =========================================================================

    duplicate_master = [
        {
            "tax_type_code": "VAT",
            "tax_name": "VAT",
            "rate": "7%",
        },
        {
            "tax_type_code": "VAT",
            "tax_name": "VAT",
            "rate": "7%",
        },
    ]

    duplicate_matcher = TaxMatcher(
        taxes=duplicate_master
    )

    result = duplicate_matcher.match(
        tax_code="VAT"
    )

    print("\nTEST: Duplicate tax code")
    print(result)

    assert result.matched is False
    assert result.tax is None
    assert (
        result.match_type
        == "ambiguous_code"
    )

    print("PASS: duplicate tax code rejected")

    # =========================================================================
    # 12. RATE TOLERANCE
    # =========================================================================

    result = matcher.match(
        tax_name="VAT",
        tax_rate="7.005%",
    )

    print("\nTEST: Rate tolerance")
    print(result)

    assert result.matched is True
    assert result.tax["tax_type_code"] == "VAT"

    print("PASS: rate tolerance")

    # =========================================================================
    # 13. OUTSIDE RATE TOLERANCE
    # =========================================================================

    result = matcher.match(
        tax_name="VAT",
        tax_rate="7.02%",
    )

    print("\nTEST: Rate outside tolerance")
    print(result)

    assert result.matched is True
    assert result.confidence == 0.90

    print("PASS: outside tolerance falls back to name")

    # =========================================================================
    # 14. EMPTY INPUT
    # =========================================================================

    result = matcher.match()

    print("\nTEST: Empty input")
    print(result)

    assert result.matched is False
    assert result.tax is None

    print("PASS: empty input rejected")

    # =========================================================================
    # 15. RATE PARSING
    # =========================================================================

    assert (
        TaxMatcher.parse_rate("7%")
        == Decimal("7")
    )

    assert (
        TaxMatcher.parse_rate("7.50%")
        == Decimal("7.50")
    )

    assert (
        TaxMatcher.parse_rate("7,50%")
        == Decimal("7.50")
    )

    assert (
        TaxMatcher.parse_rate("1,234.56")
        == Decimal("1234.56")
    )

    assert (
        TaxMatcher.parse_rate("invalid")
        is None
    )

    print(
        "PASS: rate parsing"
    )

    # =========================================================================
    # FINAL
    # =========================================================================

    print()
    print("=" * 80)
    print("ALL TAX MATCHER TESTS PASSED")
    print("=" * 80)