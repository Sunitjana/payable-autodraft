from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SupplierMatchResult:
    matched: bool
    supplier: Optional[Dict[str, Any]]
    confidence: float
    match_type: Optional[str] = None
    evidence: List[str] = field(default_factory=list)

    @property
    def record(self) -> Optional[Dict[str, Any]]:
        return self.supplier


class SupplierMatcher:
    """
    Conservative matcher for supplier master data.

    Matching priority:
        1. Exact supplier ID
        2. Exact supplier tax ID
        3. Exact normalized supplier name
        4. High-confidence fuzzy supplier name

    Safety rules:
        - Never invent a supplier.
        - Ambiguous matches are rejected.
        - If multiple supplied identifiers conflict, reject the match.
        - Fuzzy matching requires both a high score and a clear margin.
    """

    ID_KEYS = [
        "supplier_id",
        "supplierId",
        "vendor_id",
        "vendorId",
        "id",
        "code",
        "supplier_code",
        "vendor_code",
    ]

    NAME_KEYS = [
        "supplier_name",
        "supplierName",
        "vendor_name",
        "vendorName",
        "name",
        "company_name",
        "companyName",
    ]

    TAX_KEYS = [
        "tax_id",
        "taxId",
        "tax_number",
        "taxNumber",
        "vat_number",
        "vatNumber",
        "gstin",
        "gst_number",
        "tin",
    ]

    def __init__(
        self,
        suppliers: Optional[List[Dict[str, Any]]] = None,
        fuzzy_threshold: float = 0.90,
        fuzzy_margin: float = 0.03,
    ) -> None:

        if not 0.0 <= fuzzy_threshold <= 1.0:
            raise ValueError(
                "fuzzy_threshold must be between 0 and 1."
            )

        if fuzzy_margin < 0.0:
            raise ValueError(
                "fuzzy_margin must be non-negative."
            )

        self.suppliers = suppliers or []
        self.fuzzy_threshold = fuzzy_threshold
        self.fuzzy_margin = fuzzy_margin

    # =========================================================================
    # NORMALIZATION
    # =========================================================================

    @staticmethod
    def normalize(value: Any) -> str:
        """
        Normalize identifiers/names for exact comparison.
        """

        if value is None:
            return ""

        return re.sub(
            r"[^a-z0-9]+",
            "",
            str(value).casefold(),
        )

    @staticmethod
    def display_normalize(value: Any) -> str:
        """
        Normalize human-readable names for fuzzy comparison.
        """

        if value is None:
            return ""

        value = str(value).casefold()

        value = re.sub(
            r"[^\w\s]",
            " ",
            value,
            flags=re.UNICODE,
        )

        value = re.sub(
            r"\s+",
            " ",
            value,
        )

        return value.strip()

    # =========================================================================
    # MASTER VALUE HELPER
    # =========================================================================

    @staticmethod
    def _first_value(
        record: Dict[str, Any],
        keys: List[str],
    ) -> Any:

        for key in keys:

            if (
                key in record
                and record[key] not in (
                    None,
                    "",
                )
            ):
                return record[key]

        return None

    # =========================================================================
    # FUZZY SIMILARITY
    # =========================================================================

    def _similarity(
        self,
        a: str,
        b: str,
    ) -> float:

        a = self.display_normalize(a)
        b = self.display_normalize(b)

        if not a or not b:
            return 0.0

        return SequenceMatcher(
            None,
            a,
            b,
        ).ratio()

    # =========================================================================
    # EXACT MATCH HELPER
    # =========================================================================

    def _find_by_exact(
        self,
        value: str,
        records: List[Dict[str, Any]],
        keys: List[str],
    ) -> List[Dict[str, Any]]:

        normalized = self.normalize(value)

        if not normalized:
            return []

        matches: List[Dict[str, Any]] = []

        for record in records:

            master_value = self._first_value(
                record,
                keys,
            )

            if (
                master_value is not None
                and self.normalize(master_value)
                == normalized
            ):
                matches.append(record)

        return matches

    # =========================================================================
    # IDENTIFIER CONSISTENCY
    # =========================================================================

    def _identifier_conflict(
        self,
        supplier: Dict[str, Any],
        supplier_name: Optional[str],
        supplier_tax_id: Optional[str],
        supplier_id: Optional[str],
    ) -> Optional[str]:
        """
        Check supplied identifiers against the selected master record.

        Supplier ID and tax ID are hard identifiers.
        Supplier name is intentionally not treated as a hard conflict because
        legal names, trading names and abbreviated names may differ.
        """

        if supplier_id:

            master_id = self._first_value(
                supplier,
                self.ID_KEYS,
            )

            if (
                master_id
                and self.normalize(master_id)
                != self.normalize(supplier_id)
            ):

                return (
                    "Supplier ID conflicts with "
                    "the selected master record."
                )

        if supplier_tax_id:

            master_tax = self._first_value(
                supplier,
                self.TAX_KEYS,
            )

            if (
                master_tax
                and self.normalize(master_tax)
                != self.normalize(supplier_tax_id)
            ):

                return (
                    "Supplier tax ID conflicts with "
                    "the selected master record."
                )

        # Name is intentionally soft.

        _ = supplier_name

        return None

    # =========================================================================
    # RESULT BUILDER
    # =========================================================================

    def _result(
        self,
        supplier: Dict[str, Any],
        confidence: float,
        match_type: str,
        evidence: List[str],
        supplier_name: Optional[str] = None,
        supplier_tax_id: Optional[str] = None,
        supplier_id: Optional[str] = None,
    ) -> SupplierMatchResult:

        conflict = self._identifier_conflict(
            supplier,
            supplier_name,
            supplier_tax_id,
            supplier_id,
        )

        if conflict:

            return SupplierMatchResult(
                matched=False,
                supplier=None,
                confidence=0.0,
                match_type="identifier_conflict",
                evidence=[
                    conflict
                ],
            )

        return SupplierMatchResult(
            matched=True,
            supplier=supplier,
            confidence=confidence,
            match_type=match_type,
            evidence=evidence,
        )

    # =========================================================================
    # MAIN MATCH
    # =========================================================================

    def match(
        self,
        supplier_name: Optional[str] = None,
        supplier_tax_id: Optional[str] = None,
        supplier_id: Optional[str] = None,
        tax_id: Optional[str] = None,
    ) -> SupplierMatchResult:

        if supplier_tax_id is None:
            supplier_tax_id = tax_id

        # =====================================================================
        # INPUT CHECK
        # =====================================================================

        if not any(
            value not in (
                None,
                "",
            )
            for value in (
                supplier_name,
                supplier_tax_id,
                supplier_id,
            )
        ):

            return SupplierMatchResult(
                matched=False,
                supplier=None,
                confidence=0.0,
                match_type="missing_input",
                evidence=[
                    "No supplier identifier or supplier name "
                    "was extracted."
                ],
            )

        # =====================================================================
        # 1. EXACT SUPPLIER ID
        # =====================================================================

        if supplier_id:

            candidates = self._find_by_exact(
                supplier_id,
                self.suppliers,
                self.ID_KEYS,
            )

            if len(candidates) == 1:

                return self._result(
                    supplier=candidates[0],
                    confidence=1.0,
                    match_type="exact_supplier_id",
                    evidence=[
                        f"Supplier ID matched: {supplier_id}"
                    ],
                    supplier_name=supplier_name,
                    supplier_tax_id=supplier_tax_id,
                    supplier_id=supplier_id,
                )

            if len(candidates) > 1:

                return SupplierMatchResult(
                    matched=False,
                    supplier=None,
                    confidence=0.0,
                    match_type="ambiguous",
                    evidence=[
                        (
                            "Multiple suppliers have supplier ID: "
                            f"{supplier_id}"
                        )
                    ],
                )

        # =====================================================================
        # 2. EXACT TAX ID
        # =====================================================================

        if supplier_tax_id:

            candidates = self._find_by_exact(
                supplier_tax_id,
                self.suppliers,
                self.TAX_KEYS,
            )

            if len(candidates) == 1:

                return self._result(
                    supplier=candidates[0],
                    confidence=1.0,
                    match_type="exact_tax_id",
                    evidence=[
                        (
                            "Supplier tax ID matched: "
                            f"{supplier_tax_id}"
                        )
                    ],
                    supplier_name=supplier_name,
                    supplier_tax_id=supplier_tax_id,
                    supplier_id=supplier_id,
                )

            if len(candidates) > 1:

                return SupplierMatchResult(
                    matched=False,
                    supplier=None,
                    confidence=0.0,
                    match_type="ambiguous",
                    evidence=[
                        (
                            "Multiple suppliers have tax ID: "
                            f"{supplier_tax_id}"
                        )
                    ],
                )

        # =====================================================================
        # 3. EXACT NORMALIZED SUPPLIER NAME
        # =====================================================================

        if supplier_name:

            candidates = self._find_by_exact(
                supplier_name,
                self.suppliers,
                self.NAME_KEYS,
            )

            if len(candidates) == 1:

                return self._result(
                    supplier=candidates[0],
                    confidence=0.98,
                    match_type="exact_supplier_name",
                    evidence=[
                        (
                            "Supplier name matched: "
                            f"{supplier_name}"
                        )
                    ],
                    supplier_name=supplier_name,
                    supplier_tax_id=supplier_tax_id,
                    supplier_id=supplier_id,
                )

            if len(candidates) > 1:

                return SupplierMatchResult(
                    matched=False,
                    supplier=None,
                    confidence=0.0,
                    match_type="ambiguous",
                    evidence=[
                        (
                            "Multiple suppliers match name: "
                            f"{supplier_name}"
                        )
                    ],
                )

        # =====================================================================
        # 4. HIGH-CONFIDENCE FUZZY NAME
        # =====================================================================

        if supplier_name:

            candidates: List[
                Tuple[
                    float,
                    Dict[str, Any],
                ]
            ] = []

            for supplier in self.suppliers:

                master_name = self._first_value(
                    supplier,
                    self.NAME_KEYS,
                )

                if not master_name:
                    continue

                score = self._similarity(
                    supplier_name,
                    str(master_name),
                )

                candidates.append(
                    (
                        score,
                        supplier,
                    )
                )

            candidates.sort(
                key=lambda item: item[0],
                reverse=True,
            )

            if candidates:

                best_score, best_supplier = (
                    candidates[0]
                )

                second_score = (
                    candidates[1][0]
                    if len(candidates) > 1
                    else 0.0
                )

                # -------------------------------------------------------------
                # Reliable fuzzy match
                # -------------------------------------------------------------

                if (
                    best_score >= self.fuzzy_threshold
                    and (
                        best_score - second_score
                        >= self.fuzzy_margin
                    )
                ):

                    return self._result(
                        supplier=best_supplier,
                        confidence=round(
                            best_score,
                            4,
                        ),
                        match_type="fuzzy_supplier_name",
                        evidence=[
                            (
                                "Supplier name similarity: "
                                f"{best_score:.3f}"
                            )
                        ],
                        supplier_name=supplier_name,
                        supplier_tax_id=supplier_tax_id,
                        supplier_id=supplier_id,
                    )

                # -------------------------------------------------------------
                # Ambiguous fuzzy match
                # -------------------------------------------------------------

                if (
                    best_score >= self.fuzzy_threshold
                    and (
                        best_score - second_score
                        < self.fuzzy_margin
                    )
                ):

                    return SupplierMatchResult(
                        matched=False,
                        supplier=None,
                        confidence=round(
                            best_score,
                            4,
                        ),
                        match_type="ambiguous_fuzzy",
                        evidence=[
                            (
                                "Fuzzy supplier match is ambiguous; "
                                f"best={best_score:.3f}, "
                                f"second={second_score:.3f}."
                            )
                        ],
                    )

        # =====================================================================
        # NO RELIABLE MATCH
        # =====================================================================

        return SupplierMatchResult(
            matched=False,
            supplier=None,
            confidence=0.0,
            match_type="not_found",
            evidence=[
                "No sufficiently reliable supplier match found."
            ],
        )


# =============================================================================
# LOCAL TEST SUITE
# =============================================================================

if __name__ == "__main__":

    print("=" * 80)
    print("SUPPLIER MATCHER TESTS")
    print("=" * 80)

    suppliers = [
        {
            "supplier_id": "SUP-001",
            "supplier_name": "ABC Corporation Ltd",
            "tax_id": "GSTIN12345",
        },
        {
            "supplier_id": "SUP-002",
            "supplier_name": "Global Technologies Pvt Ltd",
            "tax_id": "GSTIN67890",
        },
        {
            "supplier_id": "SUP-003",
            "supplier_name": "Acme Services Ltd",
            "tax_id": "GSTIN11111",
        },
    ]

    matcher = SupplierMatcher(
        suppliers=suppliers,
        fuzzy_threshold=0.90,
        fuzzy_margin=0.03,
    )

    # =========================================================================
    # 1. EXACT SUPPLIER ID
    # =========================================================================

    result = matcher.match(
        supplier_id="SUP-001"
    )

    print("\nTEST: Exact supplier ID")
    print(result)

    assert result.matched is True
    assert result.supplier["supplier_id"] == "SUP-001"
    assert result.confidence == 1.0
    assert result.match_type == "exact_supplier_id"

    print("PASS: exact supplier ID")

    # =========================================================================
    # 2. EXACT TAX ID
    # =========================================================================

    result = matcher.match(
        supplier_tax_id="GSTIN67890"
    )

    print("\nTEST: Exact tax ID")
    print(result)

    assert result.matched is True
    assert result.supplier["supplier_id"] == "SUP-002"
    assert result.confidence == 1.0
    assert result.match_type == "exact_tax_id"

    print("PASS: exact tax ID")

    # =========================================================================
    # 3. EXACT NAME
    # =========================================================================

    result = matcher.match(
        supplier_name="ABC Corporation Ltd"
    )

    print("\nTEST: Exact supplier name")
    print(result)

    assert result.matched is True
    assert result.supplier["supplier_id"] == "SUP-001"
    assert result.confidence == 0.98
    assert result.match_type == "exact_supplier_name"

    print("PASS: exact supplier name")

    # =========================================================================
    # 4. NORMALIZED NAME
    # =========================================================================

    result = matcher.match(
        supplier_name="ABC-Corporation, Ltd."
    )

    print("\nTEST: Normalized supplier name")
    print(result)

    assert result.matched is True
    assert result.supplier["supplier_id"] == "SUP-001"
    assert result.match_type == "exact_supplier_name"

    print("PASS: normalized supplier name")

    # =========================================================================
    # 5. FUZZY NAME
    # =========================================================================

    result = matcher.match(
        supplier_name="ABC Corporation Limited"
    )

    print("\nTEST: Fuzzy supplier name")
    print(result)

    assert result.matched is True
    assert result.supplier["supplier_id"] == "SUP-001"
    assert result.match_type in (
        "exact_supplier_name",
        "fuzzy_supplier_name",
    )
    assert result.confidence >= 0.90

    print("PASS: fuzzy supplier name")

    # =========================================================================
    # 6. ID + NAME CONSISTENCY
    # =========================================================================

    result = matcher.match(
        supplier_id="SUP-001",
        supplier_name="ABC Corporation Ltd",
    )

    print("\nTEST: ID and name consistency")
    print(result)

    assert result.matched is True
    assert result.supplier["supplier_id"] == "SUP-001"

    print("PASS: ID and name consistency")

    # =========================================================================
    # 7. ID + TAX ID CONSISTENCY
    # =========================================================================

    result = matcher.match(
        supplier_id="SUP-001",
        supplier_tax_id="GSTIN12345",
    )

    print("\nTEST: ID and tax ID consistency")
    print(result)

    assert result.matched is True
    assert result.supplier["supplier_id"] == "SUP-001"

    print("PASS: ID and tax ID consistency")

    # =========================================================================
    # 8. CONFLICTING TAX ID
    # =========================================================================

    result = matcher.match(
        supplier_id="SUP-001",
        supplier_tax_id="GSTIN67890",
    )

    print("\nTEST: Conflicting tax ID")
    print(result)

    assert result.matched is False
    assert result.supplier is None
    assert result.match_type == "identifier_conflict"

    print("PASS: conflicting tax ID rejected")

    # =========================================================================
    # 9. UNKNOWN SUPPLIER
    # =========================================================================

    result = matcher.match(
        supplier_name="Completely Unknown Supplier"
    )

    print("\nTEST: Unknown supplier")
    print(result)

    assert result.matched is False
    assert result.supplier is None

    print("PASS: unknown supplier rejected")

    # =========================================================================
    # 10. NO INPUT
    # =========================================================================

    result = matcher.match()

    print("\nTEST: No input")
    print(result)

    assert result.matched is False
    assert result.supplier is None
    assert result.match_type == "missing_input"

    print("PASS: missing input rejected")

    # =========================================================================
    # 11. AMBIGUOUS EXACT NAME
    # =========================================================================

    ambiguous_suppliers = [
        {
            "supplier_id": "SUP-A",
            "supplier_name": "Same Supplier",
            "tax_id": "TAX-A",
        },
        {
            "supplier_id": "SUP-B",
            "supplier_name": "Same Supplier",
            "tax_id": "TAX-B",
        },
    ]

    ambiguous_matcher = SupplierMatcher(
        suppliers=ambiguous_suppliers
    )

    result = ambiguous_matcher.match(
        supplier_name="Same Supplier"
    )

    print("\nTEST: Ambiguous exact name")
    print(result)

    assert result.matched is False
    assert result.supplier is None
    assert result.match_type == "ambiguous"

    print("PASS: ambiguous exact name rejected")

    # =========================================================================
    # 12. EMPTY MASTER DATA
    # =========================================================================

    empty_matcher = SupplierMatcher(
        suppliers=[]
    )

    result = empty_matcher.match(
        supplier_name="ABC Corporation Ltd"
    )

    print("\nTEST: Empty master data")
    print(result)

    assert result.matched is False
    assert result.supplier is None

    print("PASS: empty master data handled")

    # =========================================================================
    # FINAL
    # =========================================================================

    print()
    print("=" * 80)
    print("ALL SUPPLIER MATCHER TESTS PASSED")
    print("=" * 80)