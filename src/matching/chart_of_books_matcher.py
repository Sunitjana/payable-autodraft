# src/matching/chart_of_books_matcher.py

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional


@dataclass
class ChartOfBooksMatchResult:
    matched: bool
    account: Optional[Dict[str, Any]]
    confidence: float
    match_type: Optional[str] = None
    evidence: List[str] = field(default_factory=list)


class ChartOfBooksMatcher:
    """Conservative matcher for buyer/chart-of-books master data.

    Supports both flat records and nested master-data structures.
    Matching is exact/normalized only. No fuzzy matching or invention
    of company, business-unit, location, or account identifiers.
    """

    CODE_KEYS = (
        "company_code",
        "companyCode",
        "business_unit_code",
        "businessUnitCode",
        "location_code",
        "locationCode",
        "account_code",
        "accountCode",
        "gl_code",
        "glCode",
        "code",
        "account_id",
        "accountId",
    )

    NAME_KEYS = (
        # Most-specific first: once parent context (e.g.
        # company_name) is merged down into a child record (see
        # _flatten_records), the least-specific name must not
        # shadow the more specific one.
        "location_name",
        "locationName",
        "business_unit_name",
        "businessUnitName",
        "company_name",
        "companyName",
        "account_name",
        "accountName",
        "gl_name",
        "glName",
        "name",
        "description",
    )

    CATEGORY_KEYS = (
        "category",
        "account_category",
        "accountCategory",
        "type",
        "account_type",
        "accountType",
    )

    RECORD_KEYS = {
        "company_code",
        "companyCode",
        "business_unit_code",
        "businessUnitCode",
        "location_code",
        "locationCode",
        "account_code",
        "accountCode",
        "gl_code",
        "glCode",
        "code",
        "account_id",
        "accountId",
        "name",
        "description",
        "company_name",
        "companyName",
        "business_unit_name",
        "businessUnitName",
        "location_name",
        "locationName",
    }

    def __init__(self, accounts: Optional[Any] = None) -> None:
        self.accounts = self._flatten_records(accounts)

    @classmethod
    def _flatten_records(cls, value: Any) -> List[Dict[str, Any]]:
        """
        Flatten a (possibly nested) master-data structure into a
        list of matchable records.

        For nested hierarchies (companies -> business_units ->
        locations, as in the supplied chart_of_books.json), each
        child record inherits its ancestors' *scalar* fields (e.g.
        company_code/company_name) merged underneath its own
        fields. Without this, no single flattened record would
        ever carry company_code + business_unit_code +
        location_code together, and a combined-identifier lookup
        (as AUTODRAFT_SCHEMA.md requires for buyer.company_code /
        business_unit_code / location_code) could never succeed
        against real nested master data — only against an
        already-flat, single-level record list.

        A flat (non-nested) input list is unaffected: there is no
        ancestor to inherit from, so behavior for that shape is
        unchanged.
        """

        records: List[Dict[str, Any]] = []

        def scalar_fields(
            node: Dict[str, Any],
        ) -> Dict[str, Any]:
            return {
                key: val
                for key, val in node.items()
                if not isinstance(val, (dict, list))
                and not (
                    isinstance(key, str)
                    and key.startswith("_")
                )
            }

        def walk(
            node: Any,
            inherited: Dict[str, Any],
        ) -> None:

            if isinstance(node, list):
                for item in node:
                    walk(item, inherited)
                return

            if not isinstance(node, dict):
                return

            merged = {
                **inherited,
                **scalar_fields(node),
            }

            if any(
                key in merged
                for key in cls.RECORD_KEYS
            ):
                records.append(merged)

            for child in node.values():
                if isinstance(child, (dict, list)):
                    walk(child, merged)

        walk(value, {})
        return records

    @staticmethod
    def normalize(value: Any) -> str:
        if value is None:
            return ""

        return re.sub(
            r"[^a-zA-Z0-9]",
            "",
            str(value),
        ).lower()

    @staticmethod
    def _first_value(
        record: Dict[str, Any],
        keys: tuple[str, ...],
    ) -> Any:

        for key in keys:
            value = record.get(key)

            if value not in (
                None,
                "",
            ):
                return value

        return None

    @staticmethod
    def _result(
        matched: bool,
        account: Optional[Dict[str, Any]],
        confidence: float,
        match_type: Optional[str],
        evidence: List[str],
    ) -> ChartOfBooksMatchResult:

        return ChartOfBooksMatchResult(
            matched=matched,
            account=account,
            confidence=confidence,
            match_type=match_type,
            evidence=evidence,
        )

    def _unique_matches(
        self,
        predicate,
    ) -> List[Dict[str, Any]]:

        return [
            record
            for record in self.accounts
            if predicate(record)
        ]

    def match(
        self,
        account_code: Optional[str] = None,
        account_name: Optional[str] = None,
        category: Optional[str] = None,
        company_code: Optional[str] = None,
        business_unit_code: Optional[str] = None,
        location_code: Optional[str] = None,
    ) -> ChartOfBooksMatchResult:

        # -------------------------------------------------
        # 1. Organization-level identifiers
        # -------------------------------------------------

        identifier_inputs = [
            (
                "company_code",
                company_code,
                (
                    "company_code",
                    "companyCode",
                ),
            ),
            (
                "business_unit_code",
                business_unit_code,
                (
                    "business_unit_code",
                    "businessUnitCode",
                ),
            ),
            (
                "location_code",
                location_code,
                (
                    "location_code",
                    "locationCode",
                ),
            ),
        ]

        supplied_identifiers = [
            (label, value, keys)
            for label, value, keys in identifier_inputs
            if value not in (None, "")
        ]

        if supplied_identifiers:

            candidates = self.accounts

            for _, value, master_keys in supplied_identifiers:

                normalized_value = self.normalize(value)

                candidates = [
                    record
                    for record in candidates
                    if self.normalize(
                        self._first_value(
                            record,
                            master_keys,
                        )
                    )
                    == normalized_value
                ]

            if len(candidates) == 1:

                return self._result(
                    True,
                    candidates[0],
                    1.0,
                    "exact_organization_codes",
                    [
                        "All supplied organization codes "
                        "matched one master record."
                    ],
                )

            if len(candidates) > 1:

                return self._result(
                    False,
                    None,
                    0.0,
                    "ambiguous",
                    [
                        "Organization codes matched "
                        "multiple master records."
                    ],
                )

            # Critical safety rule:
            # Never ignore a supplied identifier and fall back
            # to a weaker name match.
            return self._result(
                False,
                None,
                0.0,
                "identifier_not_found",
                [
                    "Supplied organization code(s) "
                    "did not match the master data."
                ],
            )

        # -------------------------------------------------
        # 2. Exact account code
        # -------------------------------------------------

        if account_code:

            normalized_code = self.normalize(
                account_code
            )

            candidates = self._unique_matches(
                lambda record:
                    self.normalize(
                        self._first_value(
                            record,
                            self.CODE_KEYS,
                        )
                    )
                    == normalized_code
            )

            if len(candidates) == 1:

                return self._result(
                    True,
                    candidates[0],
                    1.0,
                    "exact_account_code",
                    [
                        f"Account code matched: "
                        f"{account_code}"
                    ],
                )

            if len(candidates) > 1:

                return self._result(
                    False,
                    None,
                    0.0,
                    "ambiguous",
                    [
                        "Multiple master records have "
                        "the same account code."
                    ],
                )

        # -------------------------------------------------
        # 3. Exact account name
        # -------------------------------------------------

        if account_name:

            normalized_name = self.normalize(
                account_name
            )

            candidates = self._unique_matches(
                lambda record:
                    self.normalize(
                        self._first_value(
                            record,
                            self.NAME_KEYS,
                        )
                    )
                    == normalized_name
            )

            # -------------------------------------------------
            # 3a. Name + category
            # -------------------------------------------------

            if category and candidates:

                normalized_category = self.normalize(
                    category
                )

                category_candidates = [
                    record
                    for record in candidates
                    if self.normalize(
                        self._first_value(
                            record,
                            self.CATEGORY_KEYS,
                        )
                    )
                    == normalized_category
                ]

                if len(category_candidates) == 1:

                    return self._result(
                        True,
                        category_candidates[0],
                        0.99,
                        "name_and_category",
                        [
                            "Account name and category "
                            "matched."
                        ],
                    )

                if len(category_candidates) > 1:

                    return self._result(
                        False,
                        None,
                        0.0,
                        "ambiguous",
                        [
                            "Multiple master records match "
                            "name and category."
                        ],
                    )

                # Name exists but category conflicts.
                return self._result(
                    False,
                    None,
                    0.0,
                    "category_conflict",
                    [
                        "Account name matched, but the "
                        "supplied category did not."
                    ],
                )

            # -------------------------------------------------
            # 3b. Name only
            # -------------------------------------------------

            if len(candidates) == 1:

                return self._result(
                    True,
                    candidates[0],
                    0.98,
                    "exact_account_name",
                    [
                        f"Account name matched: "
                        f"{account_name}"
                    ],
                )

            if len(candidates) > 1:

                return self._result(
                    False,
                    None,
                    0.0,
                    "ambiguous",
                    [
                        "Multiple master records have "
                        "the same name."
                    ],
                )

        # -------------------------------------------------
        # 4. No reliable match
        # -------------------------------------------------

        return self._result(
            False,
            None,
            0.0,
            "not_found",
            [
                "No reliable chart-of-books "
                "match found."
            ],
        )


# =========================================================
# TESTS
# =========================================================

def _run_tests() -> None:

    flat = [
        {
            "company_code": "C001",
            "business_unit_code": "BU01",
            "location_code": "KOL",
            "company_name": "Northwind India",
        },
        {
            "account_code": "EXP100",
            "account_name": "Office Expense",
            "category": "Expense",
        },
        {
            "account_code": "REV100",
            "account_name": "Sales",
            "category": "Revenue",
        },
    ]

    matcher = ChartOfBooksMatcher(flat)

    # 1. Exact organization codes
    result = matcher.match(
        company_code="C001",
        business_unit_code="BU01",
        location_code="KOL",
    )

    assert result.matched
    assert result.confidence == 1.0

    # 2. Normalized account code
    result = matcher.match(
        account_code="exp-100"
    )

    assert result.matched
    assert result.account["account_code"] == "EXP100"

    # 3. Exact account name
    result = matcher.match(
        account_name="Office Expense"
    )

    assert result.matched
    assert result.confidence == 0.98

    # 4. Name + category
    result = matcher.match(
        account_name="office expense",
        category="expense",
    )

    assert result.matched
    assert result.confidence == 0.99

    # 5. Category conflict must reject
    result = matcher.match(
        account_name="Office Expense",
        category="Revenue",
    )

    assert not result.matched
    assert result.match_type == "category_conflict"

    # 6. Unknown account
    result = matcher.match(
        account_code="UNKNOWN"
    )

    assert not result.matched
    assert result.account is None

    # 7. Empty input
    result = matcher.match()

    assert not result.matched

    # 8. Ambiguous account name
    duplicate = [
        {
            "account_code": "A1",
            "account_name": "Travel",
        },
        {
            "account_code": "A2",
            "account_name": "Travel",
        },
    ]

    duplicate_matcher = ChartOfBooksMatcher(
        duplicate
    )

    result = duplicate_matcher.match(
        account_name="Travel"
    )

    assert not result.matched
    assert result.match_type == "ambiguous"

    # 9. Nested master data
    nested = {
        "companies": [
            {
                "company_code": "C002",
                "company_name": "Acme",
                "business_units": [
                    {
                        "business_unit_code": "BU02",
                        "business_unit_name": "Finance",
                        "locations": [
                            {
                                "location_code": "DEL",
                                "location_name": "Delhi",
                            }
                        ],
                    }
                ],
            }
        ]
    }

    nested_matcher = ChartOfBooksMatcher(
        nested
    )

    result = nested_matcher.match(
        location_code="DEL"
    )

    assert result.matched
    assert result.account["location_code"] == "DEL"

    # 10. Identifier conflict must not fall back to name
    result = matcher.match(
        company_code="WRONG",
        account_name="Northwind India",
    )

    assert not result.matched
    assert result.match_type == "identifier_not_found"

    print(
        "ALL CHART OF BOOKS MATCHER TESTS PASSED"
    )


if __name__ == "__main__":
    _run_tests()