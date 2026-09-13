"""
Master-data validation for payable autodraft payloads.

Validates references against:
    - suppliers.json
    - po_master.json
    - tax_master.json
    - payment_terms.json
    - chart_of_books.json

The validator does NOT perform fuzzy matching or invent master-data IDs.

Matcher output may be a normalized/subset record, so validation is based
on authoritative identifiers rather than full dictionary equality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ============================================================================
# RESULT
# ============================================================================

@dataclass
class MasterValidationResult:
    """
    Result returned by MasterValidator.

    Public fields are preserved from the old validator for compatibility.
    """

    valid: bool

    supplier_valid: bool = True
    po_valid: bool = True
    taxes_valid: bool = True
    payment_terms_valid: bool = True
    account_valid: bool = True

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ============================================================================
# VALIDATOR
# ============================================================================

class MasterValidator:
    """
    Validate master-data references in the canonical payable schema.

    Important:
        This class validates already-selected matcher results.

        It does NOT:
            - perform fuzzy matching
            - select a best candidate
            - invent IDs
            - modify master data
    """

    def __init__(
        self,
        suppliers: Optional[List[Dict[str, Any]]] = None,
        po_master: Optional[List[Dict[str, Any]]] = None,
        tax_master: Optional[List[Dict[str, Any]]] = None,
        payment_terms: Optional[List[Dict[str, Any]]] = None,
        chart_of_books: Optional[Any] = None,
    ):
        self.suppliers = suppliers or []
        self.po_master = po_master or []
        self.tax_master = tax_master or []
        self.payment_terms = payment_terms or []
        self.chart_of_books = chart_of_books or []

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    @staticmethod
    def _norm(value: Any) -> str:
        """
        Normalize human-readable text.
        """
        if value is None:
            return ""

        return " ".join(
            str(value)
            .strip()
            .casefold()
            .split()
        )

    @staticmethod
    def _compact(value: Any) -> str:
        """
        Normalize identifiers.

        Example:
            PO-100
            po 100
            PO100

        become equivalent.
        """
        return "".join(
            ch
            for ch in MasterValidator._norm(value)
            if ch.isalnum()
        )

    @staticmethod
    def _get(
        record: Dict[str, Any],
        *keys: str,
    ) -> Any:
        """
        Return the first non-empty value among candidate keys.
        """
        for key in keys:
            if (
                key in record
                and record[key] not in (None, "")
            ):
                return record[key]

        return None

    # ------------------------------------------------------------------
    # Master-record flattening
    # ------------------------------------------------------------------

    def _records(
        self,
        data: Any,
    ) -> List[Dict[str, Any]]:
        """
        Flatten common list/dict master-data structures.

        Supports both:

            [
                {...},
                {...}
            ]

        and nested structures such as:

            {
                "companies": [
                    {...}
                ]
            }

        A direct master record is preserved as one record.
        """

        if isinstance(data, list):
            return [
                item
                for item in data
                if isinstance(item, dict)
            ]

        if isinstance(data, dict):

            known_record_keys = {
                "supplier_id",
                "supplier_name",
                "supplier_code",
                "tax_id",
                "po_id",
                "po_number",
                "tax_type_code",
                "payment_term_id",
                "term_code",
                "company_code",
                "business_unit_code",
                "location_code",
                "account_code",
                "account_name",
            }

            # Direct record.
            if any(
                key in data
                for key in known_record_keys
            ):
                return [data]

            records: List[Dict[str, Any]] = []

            for value in data.values():
                records.extend(
                    self._records(value)
                )

            return records

        return []

    # ------------------------------------------------------------------
    # Supplier validation
    # ------------------------------------------------------------------

    def _validate_supplier(
        self,
        supplier: Any,
        errors: List[str],
    ) -> bool:

        # Supplier is optional.
        if supplier in (None, ""):
            return True

        if not isinstance(supplier, dict):
            errors.append(
                "Supplier master reference must be an object."
            )
            return False

        supplier_id = self._get(
            supplier,
            "supplier_id",
            "supplier_code",
            "vendor_id",
            "vendor_code",
        )

        supplier_tax_id = self._get(
            supplier,
            "tax_id",
            "supplier_tax_id",
            "tax_registration_number",
        )

        supplier_name = self._get(
            supplier,
            "supplier_name",
            "name",
            "vendor_name",
        )

        records = self._records(
            self.suppliers
        )

        # --------------------------------------------------------------
        # Supplier ID
        # --------------------------------------------------------------

        if supplier_id is not None:

            needle = self._compact(
                supplier_id
            )

            matches = [
                record
                for record in records
                if self._compact(
                    self._get(
                        record,
                        "supplier_id",
                        "supplier_code",
                        "vendor_id",
                        "vendor_code",
                    )
                ) == needle
            ]

            if len(matches) == 0:
                errors.append(
                    "Supplier reference could not be "
                    "matched by supplier ID."
                )
                return False

            if len(matches) > 1:
                errors.append(
                    "Supplier reference is ambiguous "
                    "by supplier ID."
                )
                return False

            matched = matches[0]

            # ----------------------------------------------------------
            # Tax ID consistency
            # ----------------------------------------------------------

            master_tax_id = self._get(
                matched,
                "tax_id",
                "supplier_tax_id",
                "tax_registration_number",
            )

            if (
                supplier_tax_id is not None
                and master_tax_id is not None
                and self._compact(
                    supplier_tax_id
                )
                != self._compact(
                    master_tax_id
                )
            ):
                errors.append(
                    "Supplier tax ID conflicts with "
                    "the matched supplier master record."
                )
                return False

            return True

        # --------------------------------------------------------------
        # Supplier tax ID
        # --------------------------------------------------------------

        if supplier_tax_id is not None:

            needle = self._compact(
                supplier_tax_id
            )

            matches = [
                record
                for record in records
                if self._compact(
                    self._get(
                        record,
                        "tax_id",
                        "supplier_tax_id",
                        "tax_registration_number",
                    )
                ) == needle
            ]

            if len(matches) == 0:
                errors.append(
                    "Supplier reference could not be "
                    "matched by tax ID."
                )
                return False

            if len(matches) > 1:
                errors.append(
                    "Supplier reference is ambiguous "
                    "by tax ID."
                )
                return False

            return True

        # --------------------------------------------------------------
        # Supplier name
        # --------------------------------------------------------------

        if supplier_name is not None:

            needle = self._norm(
                supplier_name
            )

            matches = [
                record
                for record in records
                if self._norm(
                    self._get(
                        record,
                        "supplier_name",
                        "name",
                        "vendor_name",
                    )
                ) == needle
            ]

            if len(matches) == 0:
                errors.append(
                    "Supplier reference could not be "
                    "matched by name."
                )
                return False

            if len(matches) > 1:
                errors.append(
                    "Supplier reference is ambiguous "
                    "by name."
                )
                return False

            return True

        errors.append(
            "Supplier reference contains no usable "
            "master identifier."
        )

        return False

    # ------------------------------------------------------------------
    # PO validation
    # ------------------------------------------------------------------

    def _validate_po(
        self,
        payload: Dict[str, Any],
        errors: List[str],
    ) -> bool:

        po_number = payload.get(
            "po_number"
        )

        po_id = payload.get(
            "po_id"
        )

        # PO is optional.
        if (
            po_number in (None, "")
            and po_id in (None, "")
        ):
            return True

        records = self._records(
            self.po_master
        )

        candidates = records

        # --------------------------------------------------------------
        # Validate PO ID if supplied
        # --------------------------------------------------------------

        if po_id not in (None, ""):

            needle = self._compact(
                po_id
            )

            candidates = [
                record
                for record in candidates
                if self._compact(
                    self._get(
                        record,
                        "po_id",
                        "id",
                    )
                ) == needle
            ]

        # --------------------------------------------------------------
        # Validate PO number if supplied
        # --------------------------------------------------------------

        if po_number not in (None, ""):

            needle = self._compact(
                po_number
            )

            candidates = [
                record
                for record in candidates
                if self._compact(
                    self._get(
                        record,
                        "po_number",
                        "po_no",
                        "number",
                    )
                ) == needle
            ]

        # --------------------------------------------------------------
        # Unique PO required
        # --------------------------------------------------------------

        if len(candidates) == 0:
            errors.append(
                "PO reference could not be "
                "matched in PO master."
            )
            return False

        if len(candidates) > 1:
            errors.append(
                "PO reference is ambiguous "
                "in PO master."
            )
            return False

        matched_po = candidates[0]

        # --------------------------------------------------------------
        # Supplier consistency
        # --------------------------------------------------------------

        supplier = payload.get(
            "supplier"
        )

        if isinstance(
            supplier,
            dict,
        ):

            supplied_supplier_id = self._get(
                supplier,
                "supplier_id",
                "supplier_code",
                "vendor_id",
                "vendor_code",
            )

            po_supplier_id = self._get(
                matched_po,
                "supplier_id",
                "supplier_code",
                "vendor_id",
                "vendor_code",
            )

            if (
                supplied_supplier_id is not None
                and po_supplier_id is not None
                and self._compact(
                    supplied_supplier_id
                )
                != self._compact(
                    po_supplier_id
                )
            ):
                errors.append(
                    "PO supplier conflicts with "
                    "the supplied supplier master reference."
                )
                return False

        return True

    # ------------------------------------------------------------------
    # Tax validation
    # ------------------------------------------------------------------

    def _validate_taxes(
        self,
        payload: Dict[str, Any],
        errors: List[str],
    ) -> bool:

        taxes = payload.get(
            "taxes"
        ) or []

        if not isinstance(
            taxes,
            list,
        ):
            errors.append(
                "taxes must be a list."
            )
            return False

        records = self._records(
            self.tax_master
        )

        valid = True

        for index, tax in enumerate(
            taxes,
            start=1,
        ):

            if not isinstance(
                tax,
                dict,
            ):
                errors.append(
                    f"Tax {index} must be an object."
                )
                valid = False
                continue

            tax_code = self._get(
                tax,
                "tax_type_code",
                "code",
                "tax_code",
            )

            tax_name = self._get(
                tax,
                "tax_type_name",
                "name",
                "tax_name",
                "description",
            )

            # No master identifier.
            if (
                tax_code is None
                and tax_name is None
            ):
                errors.append(
                    f"Tax {index} has no usable "
                    "master identifier."
                )
                valid = False
                continue

            # ----------------------------------------------------------
            # Canonical tax_type_code has priority
            # ----------------------------------------------------------

            if tax_code is not None:

                needle = self._compact(
                    tax_code
                )

                matches = [
                    record
                    for record in records
                    if self._compact(
                        self._get(
                            record,
                            "tax_type_code",
                            "code",
                            "tax_code",
                        )
                    ) == needle
                ]

                if len(matches) == 0:
                    errors.append(
                        f"Tax {index} could not be "
                        "matched by tax_type_code."
                    )
                    valid = False
                    continue

                if len(matches) > 1:
                    errors.append(
                        f"Tax {index} has an ambiguous "
                        "tax_type_code."
                    )
                    valid = False
                    continue

                # Code itself is authoritative.
                continue

            # ----------------------------------------------------------
            # Name fallback
            # ----------------------------------------------------------

            needle = self._norm(
                tax_name
            )

            matches = [
                record
                for record in records
                if self._norm(
                    self._get(
                        record,
                        "tax_type_name",
                        "name",
                        "tax_name",
                        "description",
                    )
                ) == needle
            ]

            if len(matches) == 0:
                errors.append(
                    f"Tax {index} could not be "
                    "matched by tax name."
                )
                valid = False
                continue

            if len(matches) > 1:
                errors.append(
                    f"Tax {index} is ambiguous "
                    "by tax name."
                )
                valid = False

        return valid

    # ------------------------------------------------------------------
    # Payment-term validation
    # ------------------------------------------------------------------

    def _validate_payment_terms(
        self,
        payload: Dict[str, Any],
        errors: List[str],
    ) -> bool:

        payment_term_id = payload.get(
            "payment_term_id"
        )

        # Optional.
        if payment_term_id in (
            None,
            "",
        ):
            return True

        records = self._records(
            self.payment_terms
        )

        needle = self._compact(
            payment_term_id
        )

        matches = [
            record
            for record in records
            if self._compact(
                self._get(
                    record,
                    "payment_term_id",
                    "term_id",
                    "id",
                )
            ) == needle
        ]

        if len(matches) == 0:
            errors.append(
                "Payment term reference could not "
                "be matched."
            )
            return False

        if len(matches) > 1:
            errors.append(
                "Payment term reference is ambiguous."
            )
            return False

        return True

    # ------------------------------------------------------------------
    # Chart-of-books / buyer validation
    # ------------------------------------------------------------------

    def _validate_account(
        self,
        payload: Dict[str, Any],
        errors: List[str],
    ) -> bool:

        buyer = payload.get(
            "buyer"
        )

        # Buyer is optional.
        if buyer in (
            None,
            "",
        ):
            return True

        if not isinstance(
            buyer,
            dict,
        ):
            errors.append(
                "Buyer/account master reference "
                "must be an object."
            )
            return False

        fields = (
            "company_code",
            "business_unit_code",
            "location_code",
        )

        supplied = {
            field_name: buyer.get(field_name)
            for field_name in fields
            if buyer.get(field_name)
            not in (
                None,
                "",
            )
        }

        # No identifiers supplied.
        if not supplied:
            return True

        records = self._records(
            self.chart_of_books
        )

        matches: List[
            Dict[str, Any]
        ] = []

        for record in records:

            # Every supplied identifier must:
            # 1. exist in master
            # 2. match exactly after normalization

            match = True

            for (
                field_name,
                supplied_value,
            ) in supplied.items():

                master_value = record.get(
                    field_name
                )

                if (
                    master_value in (
                        None,
                        "",
                    )
                    or self._compact(
                        master_value
                    )
                    != self._compact(
                        supplied_value
                    )
                ):
                    match = False
                    break

            if match:
                matches.append(
                    record
                )

        if len(matches) == 0:
            errors.append(
                "Buyer/company reference could not "
                "be matched in chart of books."
            )
            return False

        if len(matches) > 1:
            errors.append(
                "Buyer/company reference is ambiguous "
                "in chart of books."
            )
            return False

        return True

    # ------------------------------------------------------------------
    # Main validation
    # ------------------------------------------------------------------

    def validate(
        self,
        payload: Dict[str, Any],
    ) -> MasterValidationResult:

        errors: List[str] = []
        warnings: List[str] = []

        if not isinstance(
            payload,
            dict,
        ):
            return MasterValidationResult(
                valid=False,
                supplier_valid=False,
                po_valid=False,
                taxes_valid=False,
                payment_terms_valid=False,
                account_valid=False,
                errors=[
                    "Master validation payload "
                    "must be an object."
                ],
            )

        supplier_valid = (
            self._validate_supplier(
                payload.get("supplier"),
                errors,
            )
        )

        po_valid = (
            self._validate_po(
                payload,
                errors,
            )
        )

        taxes_valid = (
            self._validate_taxes(
                payload,
                errors,
            )
        )

        payment_terms_valid = (
            self._validate_payment_terms(
                payload,
                errors,
            )
        )

        account_valid = (
            self._validate_account(
                payload,
                errors,
            )
        )

        return MasterValidationResult(
            valid=not errors,
            supplier_valid=supplier_valid,
            po_valid=po_valid,
            taxes_valid=taxes_valid,
            payment_terms_valid=payment_terms_valid,
            account_valid=account_valid,
            errors=errors,
            warnings=warnings,
        )


# ============================================================================
# TESTS
# ============================================================================

def run_tests() -> None:

    suppliers = [
        {
            "supplier_id": "SUP001",
            "supplier_name": "ABC Ltd",
            "tax_id": "GST123",
        },
        {
            "supplier_id": "SUP002",
            "supplier_name": "XYZ Ltd",
            "tax_id": "GST456",
        },
    ]

    po_master = [
        {
            "po_id": "POID1",
            "po_number": "PO-100",
            "supplier_id": "SUP001",
        },
        {
            "po_id": "POID2",
            "po_number": "PO-200",
            "supplier_id": "SUP002",
        },
    ]

    tax_master = [
        {
            "tax_type_code": "VAT7",
            "tax_type_name": "VAT",
            "rate": 7,
        },
        {
            "tax_type_code": "GST18",
            "tax_type_name": "GST",
            "rate": 18,
        },
    ]

    payment_terms = [
        {
            "payment_term_id": "PT30",
            "description": "Net 30",
            "days": 30,
        }
    ]

    chart_of_books = {
        "companies": [
            {
                "company_code": "C001",
                "business_unit_code": "BU01",
                "location_code": "KOL",
            }
        ]
    }

    validator = MasterValidator(
        suppliers=suppliers,
        po_master=po_master,
        tax_master=tax_master,
        payment_terms=payment_terms,
        chart_of_books=chart_of_books,
    )

    # ----------------------------------------------------------------
    # 1. Complete valid payload
    # ----------------------------------------------------------------

    payload = {
        "supplier": {
            "supplier_id": "SUP001",
            "supplier_name": "ABC Ltd",
        },
        "po_number": "PO-100",
        "po_id": "POID1",
        "payment_term_id": "PT30",
        "buyer": {
            "company_code": "C001",
            "business_unit_code": "BU01",
            "location_code": "KOL",
        },
        "taxes": [
            {
                "tax_type_code": "VAT7",
                "tax_type_name": "VAT",
            }
        ],
    }

    result = validator.validate(payload)

    assert result.valid, result.errors
    assert result.supplier_valid
    assert result.po_valid
    assert result.taxes_valid
    assert result.payment_terms_valid
    assert result.account_valid

    # ----------------------------------------------------------------
    # 2. Supplier tax conflict
    # ----------------------------------------------------------------

    payload = {
        **payload,
        "supplier": {
            "supplier_id": "SUP001",
            "supplier_name": "ABC Ltd",
            "tax_id": "WRONG",
        },
    }

    result = validator.validate(payload)

    assert not result.valid
    assert not result.supplier_valid

    # ----------------------------------------------------------------
    # 3. Unknown PO
    # ----------------------------------------------------------------

    payload = {
        **payload,
        "supplier": {
            "supplier_id": "SUP001",
        },
        "po_number": "PO-999",
        "po_id": "POID1",
    }

    result = validator.validate(payload)

    assert not result.valid
    assert not result.po_valid

    # ----------------------------------------------------------------
    # 4. PO supplier conflict
    # ----------------------------------------------------------------

    payload = {
        **payload,
        "supplier": {
            "supplier_id": "SUP001",
        },
        "po_number": "PO-200",
        "po_id": "POID2",
    }

    result = validator.validate(payload)

    assert not result.valid
    assert not result.po_valid

    # ----------------------------------------------------------------
    # 5. Unknown tax code
    # ----------------------------------------------------------------

    payload = {
        "taxes": [
            {
                "tax_type_code": "UNKNOWN",
            }
        ]
    }

    result = validator.validate(payload)

    assert not result.valid
    assert not result.taxes_valid

    # ----------------------------------------------------------------
    # 6. Unknown payment term
    # ----------------------------------------------------------------

    payload = {
        "payment_term_id": "UNKNOWN"
    }

    result = validator.validate(payload)

    assert not result.valid
    assert not result.payment_terms_valid

    # ----------------------------------------------------------------
    # 7. Unknown chart-of-books company
    # ----------------------------------------------------------------

    payload = {
        "buyer": {
            "company_code": "BAD",
            "business_unit_code": "BU01",
            "location_code": "KOL",
        }
    }

    result = validator.validate(payload)

    assert not result.valid
    assert not result.account_valid

    # ----------------------------------------------------------------
    # 8. Optional references
    # ----------------------------------------------------------------

    payload = {
        "supplier": {
            "supplier_id": "SUP001",
        }
    }

    result = validator.validate(payload)

    assert result.valid, result.errors

    # ----------------------------------------------------------------
    # 9. Empty master data must not validate supplied references
    # ----------------------------------------------------------------

    empty_validator = MasterValidator(
        suppliers=[],
        po_master=[],
        tax_master=[],
        payment_terms=[],
        chart_of_books=[],
    )

    payload = {
        **payload,
        "taxes": [
            {
                "tax_type_code": "VAT7"
            }
        ],
    }

    result = empty_validator.validate(
        payload
    )

    assert not result.valid

    # ----------------------------------------------------------------
    # 10. Normalized PO identifiers
    # ----------------------------------------------------------------

    payload = {
        "po_number": "po 100",
        "po_id": "poid1",
    }

    result = validator.validate(
        payload
    )

    assert result.valid, result.errors

    # ----------------------------------------------------------------
    # 11. Matcher subset records
    #
    # Old validator used full dictionary equality.
    # This must now pass because supplier matcher output may contain
    # only authoritative fields.
    # ----------------------------------------------------------------

    payload = {
        "supplier": {
            "supplier_id": "SUP001",
        },
        "taxes": [
            {
                "tax_type_code": "VAT7",
            }
        ],
        "buyer": {
            "company_code": "C001",
            "business_unit_code": "BU01",
            "location_code": "KOL",
        },
    }

    result = validator.validate(
        payload
    )

    assert result.valid, result.errors

    # ----------------------------------------------------------------
    # 12. Ambiguous tax code
    # ----------------------------------------------------------------

    ambiguous_tax_master = [
        {
            "tax_type_code": "VAT7",
            "tax_type_name": "VAT",
            "rate": 7,
        },
        {
            "tax_type_code": "VAT7",
            "tax_type_name": "VAT",
            "rate": 7,
        },
    ]

    ambiguous_validator = MasterValidator(
        suppliers=suppliers,
        po_master=po_master,
        tax_master=ambiguous_tax_master,
        payment_terms=payment_terms,
        chart_of_books=chart_of_books,
    )

    result = ambiguous_validator.validate(
        {
            "taxes": [
                {
                    "tax_type_code": "VAT7"
                }
            ]
        }
    )

    assert not result.valid
    assert not result.taxes_valid

    # ----------------------------------------------------------------
    # 13. Ambiguous supplier name
    # ----------------------------------------------------------------

    duplicate_suppliers = [
        {
            "supplier_id": "SUP001",
            "supplier_name": "ABC Ltd",
        },
        {
            "supplier_id": "SUP002",
            "supplier_name": "ABC Ltd",
        },
    ]

    duplicate_supplier_validator = MasterValidator(
        suppliers=duplicate_suppliers,
        po_master=po_master,
        tax_master=tax_master,
        payment_terms=payment_terms,
        chart_of_books=chart_of_books,
    )

    result = duplicate_supplier_validator.validate(
        {
            "supplier": {
                "supplier_name": "ABC Ltd"
            }
        }
    )

    assert not result.valid
    assert not result.supplier_valid

    # ----------------------------------------------------------------
    # 14. Invalid payload type
    # ----------------------------------------------------------------

    result = validator.validate(
        "invalid"
    )

    assert not result.valid

    # ----------------------------------------------------------------
    # 15. Invalid tax structure
    # ----------------------------------------------------------------

    result = validator.validate(
        {
            "taxes": "invalid"
        }
    )

    assert not result.valid
    assert not result.taxes_valid

    print(
        "ALL MASTER VALIDATOR TESTS PASSED"
    )


if __name__ == "__main__":
    run_tests()