from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional


# =============================================================================
# RESULT
# =============================================================================

@dataclass
class PaymentTermsMatchResult:
    matched: bool
    payment_terms: Optional[Dict[str, Any]]
    confidence: float
    match_type: Optional[str] = None
    evidence: List[str] = field(default_factory=list)


# =============================================================================
# PAYMENT TERMS MATCHER
# =============================================================================

class PaymentTermsMatcher:
    """
    Conservative payment-terms master-data matcher.

    Matching priority:

        1. Explicit payment-term code
        2. Exact payment-term description
        3. Unique payment duration

    Safety rules:

        - Never invent payment_term_id.
        - Generic master 'id' is NOT treated as payment-term code.
        - Duplicate codes are rejected.
        - Duplicate descriptions are rejected.
        - Duplicate durations are rejected.
        - No fuzzy matching.
    """

    # -------------------------------------------------------------------------
    # EXPLICIT PAYMENT TERM CODE
    # -------------------------------------------------------------------------

    CODE_KEYS = [
        "payment_term_code",
        "paymentTermCode",
        "term_code",
        "termCode",
        "code",
    ]

    # -------------------------------------------------------------------------
    # CANONICAL PAYMENT TERM ID
    # -------------------------------------------------------------------------

    ID_KEYS = [
        "payment_term_id",
        "paymentTermId",
        "term_id",
        "termId",
        "id",
    ]

    # -------------------------------------------------------------------------
    # DESCRIPTION / NAME
    # -------------------------------------------------------------------------

    NAME_KEYS = [
        "payment_terms",
        "paymentTerms",
        "term",
        "terms",
        "description",
        "name",
    ]

    # -------------------------------------------------------------------------
    # DAYS
    # -------------------------------------------------------------------------

    DAYS_KEYS = [
        "days",
        "due_days",
        "dueDays",
        "payment_days",
        "paymentDays",
    ]

    # =========================================================================
    # CONSTRUCTOR
    # =========================================================================

    def __init__(
        self,
        payment_terms: Optional[
            List[Dict[str, Any]]
        ] = None,
    ) -> None:

        self.payment_terms = (
            payment_terms or []
        )

    # =========================================================================
    # NORMALIZATION
    # =========================================================================

    @staticmethod
    def normalize(
        value: Any,
    ) -> str:

        if value is None:
            return ""

        return re.sub(
            r"[^a-z0-9]+",
            "",
            str(value).casefold(),
        )

    # =========================================================================
    # EXTRACT DAYS
    # =========================================================================

    @staticmethod
    def extract_days(
        value: Any,
    ) -> Optional[int]:

        if value is None:
            return None

        if isinstance(value, bool):
            return None

        if isinstance(value, int):
            return (
                value
                if value >= 0
                else None
            )

        text = str(value).strip().casefold()

        if not text:
            return None

        match = re.search(
            r"\b(?:net\s*)?(\d+)\s*"
            r"(?:day|days|d)?\b",
            text,
        )

        if not match:
            return None

        return int(
            match.group(1)
        )

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

    def _code_candidates(
        self,
        code: str,
    ) -> List[Dict[str, Any]]:

        normalized_code = self.normalize(
            code
        )

        if not normalized_code:
            return []

        candidates = []

        for record in self.payment_terms:

            master_code = self._first_value(
                record,
                self.CODE_KEYS,
            )

            if not master_code:
                continue

            if (
                self.normalize(master_code)
                == normalized_code
            ):
                candidates.append(
                    record
                )

        return candidates

    # =========================================================================
    # MAIN MATCH
    # =========================================================================

    def match(
        self,
        payment_terms: Optional[str] = None,
        payment_term_code: Optional[str] = None,
        code: Optional[str] = None,
    ) -> PaymentTermsMatchResult:

        if payment_term_code is None:
            payment_term_code = code

        # =====================================================================
        # 1. EXPLICIT PAYMENT TERM CODE
        # =====================================================================

        if payment_term_code:

            candidates = (
                self._code_candidates(
                    payment_term_code
                )
            )

            # -------------------------------------------------------------
            # Exactly one code match
            # -------------------------------------------------------------

            if len(candidates) == 1:

                return PaymentTermsMatchResult(
                    matched=True,
                    payment_terms=candidates[0],
                    confidence=1.0,
                    match_type="exact_code",
                    evidence=[
                        (
                            "Payment term code matched: "
                            f"{payment_term_code}"
                        )
                    ],
                )

            # -------------------------------------------------------------
            # Duplicate code
            # -------------------------------------------------------------

            if len(candidates) > 1:

                return PaymentTermsMatchResult(
                    matched=False,
                    payment_terms=None,
                    confidence=0.0,
                    match_type="ambiguous_code",
                    evidence=[
                        (
                            "Multiple payment terms "
                            "share the supplied code."
                        )
                    ],
                )

        # =====================================================================
        # 2. EXACT PAYMENT TERM DESCRIPTION
        # =====================================================================

        if payment_terms:

            normalized_terms = self.normalize(
                payment_terms
            )

            candidates = []

            for record in self.payment_terms:

                master_name = self._first_value(
                    record,
                    self.NAME_KEYS,
                )

                if not master_name:
                    continue

                if (
                    self.normalize(master_name)
                    == normalized_terms
                ):
                    candidates.append(
                        record
                    )

            # -------------------------------------------------------------
            # Unique exact description
            # -------------------------------------------------------------

            if len(candidates) == 1:

                return PaymentTermsMatchResult(
                    matched=True,
                    payment_terms=candidates[0],
                    confidence=0.98,
                    match_type="exact_terms",
                    evidence=[
                        "Payment terms matched exactly."
                    ],
                )

            # -------------------------------------------------------------
            # Duplicate description
            # -------------------------------------------------------------

            if len(candidates) > 1:

                return PaymentTermsMatchResult(
                    matched=False,
                    payment_terms=None,
                    confidence=0.0,
                    match_type="ambiguous_terms",
                    evidence=[
                        (
                            "Multiple payment terms have "
                            "the same normalized description."
                        )
                    ],
                )

        # =====================================================================
        # 3. MATCH BY NUMBER OF DAYS
        # =====================================================================

        extracted_days = self.extract_days(
            payment_terms
        )

        if extracted_days is not None:

            candidates = []

            for record in self.payment_terms:

                master_days = self._first_value(
                    record,
                    self.DAYS_KEYS,
                )

                master_days = self.extract_days(
                    master_days
                )

                if (
                    master_days is not None
                    and master_days
                    == extracted_days
                ):
                    candidates.append(
                        record
                    )

            # -------------------------------------------------------------
            # Unique duration
            # -------------------------------------------------------------

            if len(candidates) == 1:

                return PaymentTermsMatchResult(
                    matched=True,
                    payment_terms=candidates[0],
                    confidence=0.92,
                    match_type="exact_days",
                    evidence=[
                        (
                            "Payment term duration matched: "
                            f"{extracted_days} days"
                        )
                    ],
                )

            # -------------------------------------------------------------
            # Duplicate duration
            # -------------------------------------------------------------

            if len(candidates) > 1:

                return PaymentTermsMatchResult(
                    matched=False,
                    payment_terms=None,
                    confidence=0.0,
                    match_type="ambiguous_days",
                    evidence=[
                        (
                            "Multiple payment terms have "
                            "the same duration."
                        )
                    ],
                )

        # =====================================================================
        # 4. NO RELIABLE MATCH
        # =====================================================================

        return PaymentTermsMatchResult(
            matched=False,
            payment_terms=None,
            confidence=0.0,
            match_type="not_found",
            evidence=[
                "No reliable payment terms match found."
            ],
        )


# =============================================================================
# LOCAL TESTS
# =============================================================================

if __name__ == "__main__":

    print("=" * 80)
    print("PAYMENT TERMS MATCHER TESTS")
    print("=" * 80)

    master = [
        {
            "payment_term_id": "PT30",
            "payment_term_code": "N30",
            "payment_terms": "Net 30 days",
            "days": 30,
        },
        {
            "payment_term_id": "PT60",
            "payment_term_code": "N60",
            "payment_terms": "Net 60 days",
            "days": 60,
        },
        {
            "payment_term_id": "PT0",
            "payment_term_code": "IMM",
            "payment_terms": "Immediate",
            "days": 0,
        },
    ]

    matcher = PaymentTermsMatcher(
        master
    )

    # =========================================================================
    # 1. EXACT CODE
    # =========================================================================

    result = matcher.match(
        payment_term_code="N30"
    )

    print("\nTEST: Exact payment term code")
    print(result)

    assert result.matched is True
    assert (
        result.payment_terms[
            "payment_term_id"
        ]
        == "PT30"
    )
    assert result.confidence == 1.0
    assert result.match_type == "exact_code"

    print("PASS: exact code")

    # =========================================================================
    # 2. NORMALIZED CODE
    # =========================================================================

    result = matcher.match(
        payment_term_code=" n-30 "
    )

    print("\nTEST: Normalized code")
    print(result)

    assert result.matched is True
    assert (
        result.payment_terms[
            "payment_term_id"
        ]
        == "PT30"
    )

    print("PASS: normalized code")

    # =========================================================================
    # 3. EXACT TERMS
    # =========================================================================

    result = matcher.match(
        payment_terms="Net 30 days"
    )

    print("\nTEST: Exact payment terms")
    print(result)

    assert result.matched is True
    assert (
        result.payment_terms[
            "payment_term_id"
        ]
        == "PT30"
    )
    assert result.confidence == 0.98

    print("PASS: exact terms")

    # =========================================================================
    # 4. NET 30 WITHOUT DAYS WORD
    # =========================================================================

    result = matcher.match(
        payment_terms="Net 30"
    )

    print("\nTEST: Net 30")
    print(result)

    assert result.matched is True
    assert (
        result.payment_terms[
            "payment_term_id"
        ]
        == "PT30"
    )

    print("PASS: Net 30")

    # =========================================================================
    # 5. UNIQUE DAYS
    # =========================================================================

    result = matcher.match(
        payment_terms="Payment due in 60 days"
    )

    print("\nTEST: Unique duration")
    print(result)

    assert result.matched is True
    assert (
        result.payment_terms[
            "payment_term_id"
        ]
        == "PT60"
    )
    assert result.confidence == 0.92
    assert result.match_type == "exact_days"

    print("PASS: unique duration")

    # =========================================================================
    # 6. IMMEDIATE
    # =========================================================================

    result = matcher.match(
        payment_terms="Immediate"
    )

    print("\nTEST: Immediate")
    print(result)

    assert result.matched is True
    assert (
        result.payment_terms[
            "payment_term_id"
        ]
        == "PT0"
    )

    print("PASS: immediate")

    # =========================================================================
    # 7. UNKNOWN TERMS
    # =========================================================================

    result = matcher.match(
        payment_terms="Net 999"
    )

    print("\nTEST: Unknown terms")
    print(result)

    assert result.matched is False
    assert result.payment_terms is None
    assert result.match_type == "not_found"

    print("PASS: unknown terms rejected")

    # =========================================================================
    # 8. UNKNOWN CODE
    # =========================================================================

    result = matcher.match(
        payment_term_code="BAD"
    )

    print("\nTEST: Unknown code")
    print(result)

    assert result.matched is False
    assert result.payment_terms is None

    print("PASS: unknown code rejected")

    # =========================================================================
    # 9. EMPTY INPUT
    # =========================================================================

    result = matcher.match()

    print("\nTEST: Empty input")
    print(result)

    assert result.matched is False
    assert result.payment_terms is None
    assert result.match_type == "not_found"

    print("PASS: empty input rejected")

    # =========================================================================
    # 10. DUPLICATE CODE
    # =========================================================================

    duplicate_code_master = master + [
        {
            "payment_term_id": "PT30-X",
            "payment_term_code": "N30",
            "payment_terms": "Another Net 30",
            "days": 30,
        }
    ]

    duplicate_matcher = PaymentTermsMatcher(
        duplicate_code_master
    )

    result = duplicate_matcher.match(
        payment_term_code="N30"
    )

    print("\nTEST: Duplicate code")
    print(result)

    assert result.matched is False
    assert result.payment_terms is None
    assert result.match_type == "ambiguous_code"

    print("PASS: duplicate code rejected")

    # =========================================================================
    # 11. DUPLICATE DESCRIPTION
    # =========================================================================

    duplicate_terms_master = [
        {
            "payment_term_id": "A",
            "payment_term_code": "A1",
            "payment_terms": "Net 30",
            "days": 30,
        },
        {
            "payment_term_id": "B",
            "payment_term_code": "B1",
            "payment_terms": "Net 30",
            "days": 30,
        },
    ]

    duplicate_terms_matcher = (
        PaymentTermsMatcher(
            duplicate_terms_master
        )
    )

    result = duplicate_terms_matcher.match(
        payment_terms="Net 30"
    )

    print("\nTEST: Duplicate description")
    print(result)

    assert result.matched is False
    assert result.payment_terms is None
    assert (
        result.match_type
        == "ambiguous_terms"
    )

    print("PASS: duplicate description rejected")

    # =========================================================================
    # 12. DUPLICATE DAYS
    # =========================================================================

    duplicate_days_master = [
        {
            "payment_term_id": "A",
            "payment_term_code": "A1",
            "payment_terms": "Term A",
            "days": 30,
        },
        {
            "payment_term_id": "B",
            "payment_term_code": "B1",
            "payment_terms": "Term B",
            "days": 30,
        },
    ]

    duplicate_days_matcher = (
        PaymentTermsMatcher(
            duplicate_days_master
        )
    )

    result = duplicate_days_matcher.match(
        payment_terms="Payment in 30 days"
    )

    print("\nTEST: Duplicate duration")
    print(result)

    assert result.matched is False
    assert result.payment_terms is None
    assert (
        result.match_type
        == "ambiguous_days"
    )

    print("PASS: duplicate duration rejected")

    # =========================================================================
    # 13. DAYS PARSING
    # =========================================================================

    assert (
        PaymentTermsMatcher.extract_days(
            "Net 90"
        )
        == 90
    )

    assert (
        PaymentTermsMatcher.extract_days(
            "90 days"
        )
        == 90
    )

    assert (
        PaymentTermsMatcher.extract_days(
            30
        )
        == 30
    )

    assert (
        PaymentTermsMatcher.extract_days(
            "invalid"
        )
        is None
    )

    print("PASS: days parsing")

    # =========================================================================
    # 14. GENERIC ID IS NOT CODE
    # =========================================================================

    id_only_master = [
        {
            "id": "123",
            "payment_terms": "Net 30",
            "days": 30,
        }
    ]

    id_matcher = PaymentTermsMatcher(
        id_only_master
    )

    result = id_matcher.match(
        payment_term_code="123"
    )

    print("\nTEST: Generic ID is not payment code")
    print(result)

    assert result.matched is False
    assert result.payment_terms is None

    print("PASS: generic ID not treated as code")

    # =========================================================================
    # FINAL
    # =========================================================================

    print()
    print("=" * 80)
    print("ALL PAYMENT TERMS MATCHER TESTS PASSED")
    print("=" * 80)