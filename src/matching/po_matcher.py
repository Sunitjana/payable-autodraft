from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional


# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class POMatchResult:
    matched: bool
    purchase_order: Optional[Dict[str, Any]]
    confidence: float
    match_type: Optional[str] = None
    evidence: List[str] = field(default_factory=list)


# =============================================================================
# PO MATCHER
# =============================================================================

class POMatcher:
    """
    Conservative purchase-order matcher.

    Matching strategy:

        1. Exact normalized PO number
        2. Validate supplier relationship
        3. If duplicate PO numbers exist, use supplier ID only
           when it uniquely identifies one PO

    Safety rules:

        - Never invent a PO.
        - Never fuzzy-match PO numbers.
        - Supplier conflicts reject the PO.
        - Ambiguous duplicate POs are rejected.
        - Missing PO number is not treated as a match.
    """

    # -------------------------------------------------------------------------
    # PO NUMBER FIELDS
    # -------------------------------------------------------------------------

    PO_KEYS = [
        "po_number",
        "poNumber",
        "purchase_order_number",
        "purchaseOrderNumber",
        "po_no",
        "poNo",
        "number",
    ]

    # -------------------------------------------------------------------------
    # PO ID FIELDS
    # -------------------------------------------------------------------------

    ID_KEYS = [
        "po_id",
        "poId",
        "purchase_order_id",
        "purchaseOrderId",
        "id",
    ]

    # -------------------------------------------------------------------------
    # SUPPLIER FIELDS
    # -------------------------------------------------------------------------

    SUPPLIER_KEYS = [
        "supplier_id",
        "supplierId",
        "vendor_id",
        "vendorId",
        "supplier_code",
        "vendor_code",
    ]

    # =========================================================================
    # CONSTRUCTOR
    # =========================================================================

    def __init__(
        self,
        purchase_orders: Optional[
            List[Dict[str, Any]]
        ] = None,
    ) -> None:

        self.purchase_orders = (
            purchase_orders or []
        )

    # =========================================================================
    # NORMALIZATION
    # =========================================================================

    @staticmethod
    def normalize(
        value: Any,
    ) -> str:
        """
        Normalize PO identifiers.

        Examples:

            PO-1001
            PO 1001
            po_1001

        all become:

            po1001
        """

        if value is None:
            return ""

        return re.sub(
            r"[^a-zA-Z0-9]+",
            "",
            str(value),
        ).casefold()

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
                and record[key] not in (
                    None,
                    "",
                )
            ):
                return record[key]

        return None

    # =========================================================================
    # FIND PO CANDIDATES
    # =========================================================================

    def _find_candidates(
        self,
        po_number: str,
    ) -> List[Dict[str, Any]]:

        normalized_po = self.normalize(
            po_number
        )

        if not normalized_po:
            return []

        candidates: List[
            Dict[str, Any]
        ] = []

        for po in self.purchase_orders:

            master_po = self._first_value(
                po,
                self.PO_KEYS,
            )

            if not master_po:
                continue

            if (
                self.normalize(master_po)
                == normalized_po
            ):
                candidates.append(po)

        return candidates

    # =========================================================================
    # SUPPLIER MATCH
    # =========================================================================

    def _supplier_matches(
        self,
        po: Dict[str, Any],
        supplier_id: str,
    ) -> bool:

        master_supplier = self._first_value(
            po,
            self.SUPPLIER_KEYS,
        )

        if not master_supplier:
            return False

        return (
            self.normalize(master_supplier)
            == self.normalize(supplier_id)
        )

    # =========================================================================
    # MAIN MATCH
    # =========================================================================

    def match(
        self,
        po_number: Optional[str],
        supplier_id: Optional[str] = None,
    ) -> POMatchResult:

        # =====================================================================
        # 1. MISSING PO
        # =====================================================================

        if po_number in (
            None,
            "",
        ):

            return POMatchResult(
                matched=False,
                purchase_order=None,
                confidence=0.0,
                match_type="missing_input",
                evidence=[
                    "No PO number was extracted."
                ],
            )

        # =====================================================================
        # 2. EXACT PO NUMBER SEARCH
        # =====================================================================

        candidates = self._find_candidates(
            po_number
        )

        # =====================================================================
        # 3. PO NOT FOUND
        # =====================================================================

        if not candidates:

            return POMatchResult(
                matched=False,
                purchase_order=None,
                confidence=0.0,
                match_type="not_found",
                evidence=[
                    (
                        "PO not found in master data: "
                        f"{po_number}"
                    )
                ],
            )

        # =====================================================================
        # 4. SINGLE PO CANDIDATE
        # =====================================================================

        if len(candidates) == 1:

            po = candidates[0]

            # -----------------------------------------------------------------
            # Validate supplier if supplied
            # -----------------------------------------------------------------

            if supplier_id:

                master_supplier = self._first_value(
                    po,
                    self.SUPPLIER_KEYS,
                )

                # Supplier exists in master and conflicts.
                if (
                    master_supplier
                    and self.normalize(
                        master_supplier
                    )
                    != self.normalize(
                        supplier_id
                    )
                ):

                    return POMatchResult(
                        matched=False,
                        purchase_order=None,
                        confidence=0.0,
                        match_type="supplier_conflict",
                        evidence=[
                            (
                                "PO exists, but supplier does "
                                "not match."
                            )
                        ],
                    )

                # Supplier is supplied but PO master does not contain
                # a supplier value. Do not invent the relationship.
                if not master_supplier:

                    return POMatchResult(
                        matched=False,
                        purchase_order=None,
                        confidence=0.0,
                        match_type="supplier_missing_in_master",
                        evidence=[
                            (
                                "PO matched, but supplier information "
                                "is missing from the PO master record."
                            )
                        ],
                    )

                return POMatchResult(
                    matched=True,
                    purchase_order=po,
                    confidence=1.0,
                    match_type="exact_po_number_supplier",
                    evidence=[
                        (
                            f"PO number matched: {po_number}"
                        ),
                        (
                            f"Supplier matched: {supplier_id}"
                        ),
                    ],
                )

            # -----------------------------------------------------------------
            # PO match without supplier
            # -----------------------------------------------------------------

            return POMatchResult(
                matched=True,
                purchase_order=po,
                confidence=1.0,
                match_type="exact_po_number",
                evidence=[
                    (
                        f"PO number matched: {po_number}"
                    )
                ],
            )

        # =====================================================================
        # 5. DUPLICATE PO NUMBER
        # =====================================================================

        if supplier_id:

            supplier_candidates = [
                po
                for po in candidates
                if self._supplier_matches(
                    po,
                    supplier_id,
                )
            ]

            # ---------------------------------------------------------------
            # Supplier uniquely identifies one duplicate PO
            # ---------------------------------------------------------------

            if len(supplier_candidates) == 1:

                return POMatchResult(
                    matched=True,
                    purchase_order=supplier_candidates[0],
                    confidence=1.0,
                    match_type="exact_po_number_supplier",
                    evidence=[
                        (
                            f"PO number matched: {po_number}"
                        ),
                        (
                            f"Supplier matched: {supplier_id}"
                        ),
                    ],
                )

            # ---------------------------------------------------------------
            # Supplier does not identify a unique PO
            # ---------------------------------------------------------------

            if len(supplier_candidates) > 1:

                return POMatchResult(
                    matched=False,
                    purchase_order=None,
                    confidence=0.0,
                    match_type="ambiguous",
                    evidence=[
                        (
                            "Multiple PO master records match "
                            "both PO number and supplier."
                        )
                    ],
                )

            # ---------------------------------------------------------------
            # PO exists but supplier conflicts
            # ---------------------------------------------------------------

            return POMatchResult(
                matched=False,
                purchase_order=None,
                confidence=0.0,
                match_type="supplier_conflict",
                evidence=[
                    (
                        "PO number exists in master data, "
                        "but no record matches the supplied supplier."
                    )
                ],
            )

        # =====================================================================
        # 6. DUPLICATE WITHOUT SUPPLIER
        # =====================================================================

        return POMatchResult(
            matched=False,
            purchase_order=None,
            confidence=0.0,
            match_type="ambiguous",
            evidence=[
                (
                    "Multiple master records matched "
                    f"PO number: {po_number}"
                )
            ],
        )


# =============================================================================
# LOCAL TEST SUITE
# =============================================================================

if __name__ == "__main__":

    print("=" * 80)
    print("PO MATCHER TESTS")
    print("=" * 80)

    purchase_orders = [
        {
            "po_id": "PO-ID-001",
            "po_number": "PO-1001",
            "supplier_id": "SUP-001",
        },
        {
            "po_id": "PO-ID-002",
            "po_number": "PO-1002",
            "supplier_id": "SUP-002",
        },
        {
            "po_id": "PO-ID-003",
            "po_number": "PO-1003",
            "supplier_id": "SUP-001",
        },
    ]

    matcher = POMatcher(
        purchase_orders=purchase_orders
    )

    # =========================================================================
    # 1. EXACT PO NUMBER
    # =========================================================================

    result = matcher.match(
        "PO-1001"
    )

    print("\nTEST: Exact PO number")
    print(result)

    assert result.matched is True
    assert result.purchase_order["po_id"] == "PO-ID-001"
    assert result.confidence == 1.0
    assert result.match_type == "exact_po_number"

    print("PASS: exact PO number")

    # =========================================================================
    # 2. NORMALIZED PO NUMBER
    # =========================================================================

    result = matcher.match(
        "PO1001",
        "SUP-001",
    )

    print("\nTEST: Normalized PO number")
    print(result)

    assert result.matched is True
    assert result.purchase_order["po_id"] == "PO-ID-001"

    print("PASS: normalized PO number")

    # =========================================================================
    # 3. PO + CORRECT SUPPLIER
    # =========================================================================

    result = matcher.match(
        "PO-1002",
        "SUP-002",
    )

    print("\nTEST: PO and correct supplier")
    print(result)

    assert result.matched is True
    assert result.purchase_order["po_id"] == "PO-ID-002"
    assert result.match_type == "exact_po_number_supplier"

    print("PASS: PO and supplier match")

    # =========================================================================
    # 4. SUPPLIER CONFLICT
    # =========================================================================

    result = matcher.match(
        "PO-1001",
        "SUP-002",
    )

    print("\nTEST: Supplier conflict")
    print(result)

    assert result.matched is False
    assert result.purchase_order is None
    assert result.match_type == "supplier_conflict"

    print("PASS: supplier conflict rejected")

    # =========================================================================
    # 5. PO NOT FOUND
    # =========================================================================

    result = matcher.match(
        "PO-9999"
    )

    print("\nTEST: Unknown PO")
    print(result)

    assert result.matched is False
    assert result.purchase_order is None
    assert result.match_type == "not_found"

    print("PASS: unknown PO rejected")

    # =========================================================================
    # 6. MISSING INPUT
    # =========================================================================

    result = matcher.match(
        None
    )

    print("\nTEST: Missing PO")
    print(result)

    assert result.matched is False
    assert result.purchase_order is None
    assert result.match_type == "missing_input"

    print("PASS: missing PO rejected")

    # =========================================================================
    # 7. EMPTY STRING
    # =========================================================================

    result = matcher.match(
        ""
    )

    print("\nTEST: Empty PO")
    print(result)

    assert result.matched is False
    assert result.match_type == "missing_input"

    print("PASS: empty PO rejected")

    # =========================================================================
    # 8. DUPLICATE PO WITHOUT SUPPLIER
    # =========================================================================

    duplicate_pos = [
        {
            "po_id": "PO-A",
            "po_number": "PO-X",
            "supplier_id": "SUP-A",
        },
        {
            "po_id": "PO-B",
            "po_number": "PO-X",
            "supplier_id": "SUP-B",
        },
    ]

    duplicate_matcher = POMatcher(
        purchase_orders=duplicate_pos
    )

    result = duplicate_matcher.match(
        "PO-X"
    )

    print("\nTEST: Duplicate PO without supplier")
    print(result)

    assert result.matched is False
    assert result.purchase_order is None
    assert result.match_type == "ambiguous"

    print("PASS: duplicate PO rejected")

    # =========================================================================
    # 9. DUPLICATE PO RESOLVED BY SUPPLIER
    # =========================================================================

    result = duplicate_matcher.match(
        "PO-X",
        "SUP-B",
    )

    print("\nTEST: Duplicate PO resolved by supplier")
    print(result)

    assert result.matched is True
    assert result.purchase_order["po_id"] == "PO-B"
    assert result.match_type == "exact_po_number_supplier"

    print("PASS: duplicate PO resolved by supplier")

    # =========================================================================
    # 10. DUPLICATE PO WRONG SUPPLIER
    # =========================================================================

    result = duplicate_matcher.match(
        "PO-X",
        "SUP-Z",
    )

    print("\nTEST: Duplicate PO wrong supplier")
    print(result)

    assert result.matched is False
    assert result.purchase_order is None
    assert result.match_type == "supplier_conflict"

    print("PASS: duplicate PO supplier conflict rejected")

    # =========================================================================
    # 11. SUPPLIER MISSING FROM MASTER
    # =========================================================================

    missing_supplier_pos = [
        {
            "po_id": "PO-ID-004",
            "po_number": "PO-2001",
        }
    ]

    missing_supplier_matcher = POMatcher(
        purchase_orders=missing_supplier_pos
    )

    result = missing_supplier_matcher.match(
        "PO-2001",
        "SUP-001",
    )

    print("\nTEST: Supplier missing from PO master")
    print(result)

    assert result.matched is False
    assert result.purchase_order is None
    assert result.match_type == "supplier_missing_in_master"

    print("PASS: missing supplier relationship rejected")

    # =========================================================================
    # 12. NO FUZZY PO MATCHING
    # =========================================================================

    result = matcher.match(
        "PO-100"
    )

    print("\nTEST: Partial PO number")
    print(result)

    assert result.matched is False
    assert result.purchase_order is None

    print("PASS: partial PO number rejected")

    # =========================================================================
    # FINAL
    # =========================================================================

    print()
    print("=" * 80)
    print("ALL PO MATCHER TESTS PASSED")
    print("=" * 80)