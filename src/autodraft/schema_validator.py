"""
Autodraft schema and payload validation.

Validates the final canonical AUTODRAFT_SCHEMA structure used by
src.autodraft.builder and erp.py.

Supported payload forms:

1. Single payable:
       {
           "invoice_number": ...,
           ...
       }

2. Batch output:
       {
           "file": "...",
           "payables": [...],
           "declined": [...]
       }

If a real JSON Schema file is supplied, it is additionally validated.

The challenge's AUTODRAFT_SCHEMA.md is documentation rather than a JSON
Schema, so it should not be passed as schema_path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================================
# RESULT
# ============================================================================

@dataclass
class SchemaValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ============================================================================
# VALIDATOR
# ============================================================================

class SchemaValidator:
    """
    Validate the canonical payable/autodraft structure.

    The validator is deliberately conservative:
        - missing required fields are errors
        - malformed numeric values are errors
        - no financial values are invented
        - line quantity and unit price must be present
        - financial calculation is delegated to FinancialValidator
        - optional JSON Schema validation can be enabled
    """

    REQUIRED_PAYABLE_FIELDS = (
        "invoice_number",
        "invoice_date",
        "due_date",
        "invoice_type",
        "currency",
        "supplier",
        "buyer",
        "payment_term_id",
        "po_number",
        "po_id",
        "gross_total",
        "subtotal",
        "total_tax_amount",
        "discount_amount",
        "freight_charges",
        "insurance_charges",
        "extra_charges",
        "excise_duties",
        "taxes",
        "line_items",
    )

    OBJECT_FIELDS = (
        "supplier",
        "buyer",
    )

    LIST_FIELDS = (
        "taxes",
        "line_items",
    )

    NUMERIC_FIELDS = (
        "gross_total",
        "subtotal",
        "total_tax_amount",
        "discount_amount",
        "freight_charges",
        "insurance_charges",
        "extra_charges",
        "excise_duties",
    )

    STRING_FIELDS = (
        "invoice_number",
        "invoice_date",
        "due_date",
        "invoice_type",
        "currency",
        "payment_term_id",
        "po_number",
        "po_id",
    )

    def __init__(
        self,
        schema_path: Optional[str | Path] = None,
        amount_tolerance: Decimal = Decimal("0.01"),
    ) -> None:

        self.schema_path = (
            Path(schema_path)
            if schema_path
            else None
        )

        self.amount_tolerance = Decimal(
            str(amount_tolerance)
        )

        if self.amount_tolerance < 0:
            raise ValueError(
                "amount_tolerance must be non-negative."
            )

        self.schema: Optional[
            Dict[str, Any]
        ] = None

        if self.schema_path is not None:
            self.schema = self._load_schema(
                self.schema_path
            )

    # ========================================================================
    # SCHEMA LOADING
    # ========================================================================
    @staticmethod
    def _load_schema(
        path: Path,
    ) -> Optional[Dict[str, Any]]:
        """
        Load an actual JSON Schema.

        AUTODRAFT_SCHEMA.md is the challenge's Markdown
        output-contract documentation, not a JSON Schema.
        Therefore Markdown is intentionally treated as
        documentation and structural validation remains
        authoritative.
        """

        if not path.exists():
            return None

        # AUTODRAFT_SCHEMA.md is Markdown containing
        # JSONC examples and explanatory text. It is NOT
        # itself a JSON Schema.
        if path.suffix.lower() in {".md", ".markdown"}:
            return None

        try:
            with path.open(
                "r",
                encoding="utf-8-sig",
            ) as file:
                schema = json.load(file)

            if not isinstance(schema, dict):
                return None

            return schema

        except (
            OSError,
            json.JSONDecodeError,
        ):
            return None

    # ========================================================================
    # DECIMAL
    # ========================================================================

    @staticmethod
    def _decimal(
        value: Any,
    ) -> Optional[Decimal]:
        """
        Convert common financial representations into Decimal.

        Supported examples:

            1234.56
            1,234.56
            1.234,56
            ₹1,234.56
            $1,234.56
        """

        if value is None:
            return None

        if isinstance(value, bool):
            return None

        if isinstance(value, Decimal):
            return value

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
            "\u00a0",
            " ",
        )

        # Keep only number-related characters.
        text = re.sub(
            r"[^\d,.\-+]",
            "",
            text,
        )

        if not text:
            return None

        # --------------------------------------------------------------
        # Both separators.
        # --------------------------------------------------------------

        if "," in text and "." in text:

            # European:
            # 1.234,56
            if text.rfind(",") > text.rfind("."):

                text = text.replace(
                    ".",
                    "",
                )

                text = text.replace(
                    ",",
                    ".",
                )

            # US:
            # 1,234.56
            else:

                text = text.replace(
                    ",",
                    "",
                )

        # --------------------------------------------------------------
        # Only comma.
        # --------------------------------------------------------------

        elif "," in text:

            parts = text.split(",")

            # 1234,56
            if (
                len(parts) > 1
                and len(parts[-1]) in (1, 2)
            ):

                text = (
                    "".join(parts[:-1])
                    + "."
                    + parts[-1]
                )

            # 1,234
            else:

                text = text.replace(
                    ",",
                    "",
                )

        try:
            return Decimal(text)

        except InvalidOperation:
            return None

    # ========================================================================
    # JSON SCHEMA
    # ========================================================================

    def _validate_json_schema(
        self,
        payload: Dict[str, Any],
    ) -> List[str]:

        if self.schema is None:
            return []

        try:

            from jsonschema import (
                Draft7Validator,
            )

        except ImportError:

            return [
                (
                    "jsonschema package is not installed; "
                    "JSON Schema validation was skipped."
                )
            ]

        validator = Draft7Validator(
            self.schema
        )

        errors: List[str] = []

        for error in sorted(
            validator.iter_errors(payload),
            key=lambda item: list(
                item.absolute_path
            ),
        ):

            path = ".".join(
                str(part)
                for part in error.absolute_path
            )

            if path:

                errors.append(
                    f"{path}: {error.message}"
                )

            else:

                errors.append(
                    error.message
                )

        return errors

    # ========================================================================
    # LINE ITEM VALIDATION
    # ========================================================================

    def _validate_line_items(
        self,
        line_items: Any,
        prefix: str,
    ) -> List[str]:

        errors: List[str] = []

        if not isinstance(
            line_items,
            list,
        ):

            return [
                f"{prefix} must be an array."
            ]

        for index, item in enumerate(
            line_items,
            start=1,
        ):

            path = (
                f"{prefix}[{index}]"
            )

            if not isinstance(
                item,
                dict,
            ):

                errors.append(
                    f"{path} must be an object."
                )

                continue

            # ----------------------------------------------------------
            # Quantity
            # ----------------------------------------------------------

            if "quantity" not in item:

                errors.append(
                    f"{path}.quantity is missing."
                )

            elif item.get(
                "quantity"
            ) in (
                None,
                "",
            ):

                errors.append(
                    f"{path}.quantity is missing."
                )

            elif self._decimal(
                item.get("quantity")
            ) is None:

                errors.append(
                    f"{path}.quantity must be numeric."
                )

            # ----------------------------------------------------------
            # Unit price
            # ----------------------------------------------------------

            if "unit_price" not in item:

                errors.append(
                    f"{path}.unit_price is missing."
                )

            elif item.get(
                "unit_price"
            ) in (
                None,
                "",
            ):

                errors.append(
                    f"{path}.unit_price is missing."
                )

            elif self._decimal(
                item.get("unit_price")
            ) is None:

                errors.append(
                    f"{path}.unit_price must be numeric."
                )

            # ----------------------------------------------------------
            # Optional numeric line fields
            # ----------------------------------------------------------

            for field_name in (
                "discount",
                "discount_percentage",
                "tax_rate",
                "tax_amount",
            ):

                if (
                    field_name in item
                    and item[field_name]
                    not in (
                        None,
                        "",
                    )
                ):

                    if self._decimal(
                        item[field_name]
                    ) is None:

                        errors.append(
                            f"{path}.{field_name} "
                            "must be numeric."
                        )

            # ----------------------------------------------------------
            # Line taxes
            # ----------------------------------------------------------

            taxes = item.get(
                "taxes"
            )

            if taxes is not None:

                if not isinstance(
                    taxes,
                    list,
                ):

                    errors.append(
                        f"{path}.taxes must be an array."
                    )

                else:

                    for tax_index, tax in enumerate(
                        taxes,
                        start=1,
                    ):

                        tax_path = (
                            f"{path}.taxes[{tax_index}]"
                        )

                        if not isinstance(
                            tax,
                            dict,
                        ):

                            errors.append(
                                f"{tax_path} "
                                "must be an object."
                            )

                            continue

                        if (
                            tax.get(
                                "tax_amount"
                            )
                            not in (
                                None,
                                "",
                            )
                            and self._decimal(
                                tax.get(
                                    "tax_amount"
                                )
                            )
                            is None
                        ):

                            errors.append(
                                f"{tax_path}.tax_amount "
                                "must be numeric."
                            )

                        if (
                            tax.get(
                                "tax_rate"
                            )
                            not in (
                                None,
                                "",
                            )
                            and self._decimal(
                                tax.get(
                                    "tax_rate"
                                )
                            )
                            is None
                        ):

                            errors.append(
                                f"{tax_path}.tax_rate "
                                "must be numeric."
                            )

        return errors

    # ========================================================================
    # HEADER TAX VALIDATION
    # ========================================================================

    def _validate_taxes(
        self,
        taxes: Any,
        prefix: str,
    ) -> List[str]:

        errors: List[str] = []

        if not isinstance(
            taxes,
            list,
        ):

            return [
                f"{prefix} must be an array."
            ]

        for index, tax in enumerate(
            taxes,
            start=1,
        ):

            path = (
                f"{prefix}[{index}]"
            )

            if not isinstance(
                tax,
                dict,
            ):

                errors.append(
                    f"{path} must be an object."
                )

                continue

            if (
                tax.get(
                    "tax_amount"
                )
                not in (
                    None,
                    "",
                )
                and self._decimal(
                    tax.get(
                        "tax_amount"
                    )
                )
                is None
            ):

                errors.append(
                    f"{path}.tax_amount "
                    "must be numeric."
                )

            if (
                tax.get(
                    "tax_rate"
                )
                not in (
                    None,
                    "",
                )
                and self._decimal(
                    tax.get(
                        "tax_rate"
                    )
                )
                is None
            ):

                errors.append(
                    f"{path}.tax_rate "
                    "must be numeric."
                )

        return errors

    # ========================================================================
    # PAYABLE STRUCTURE
    # ========================================================================

    def _validate_payable_structure(
        self,
        payload: Any,
        prefix: str = "payable",
    ) -> Tuple[
        List[str],
        List[str],
    ]:

        errors: List[str] = []
        warnings: List[str] = []

        if not isinstance(
            payload,
            dict,
        ):

            return (
                [
                    f"{prefix} must be a JSON object."
                ],
                warnings,
            )

        # --------------------------------------------------------------
        # Required canonical fields
        # --------------------------------------------------------------

        for field_name in (
            self.REQUIRED_PAYABLE_FIELDS
        ):

            if field_name not in payload:

                errors.append(
                    f"{prefix}.{field_name} "
                    "is missing."
                )

        # --------------------------------------------------------------
        # Numeric fields
        # --------------------------------------------------------------

        for field_name in (
            self.NUMERIC_FIELDS
        ):

            if field_name not in payload:
                continue

            value = payload.get(
                field_name
            )

            if value in (
                None,
                "",
            ):

                if field_name == "gross_total":

                    errors.append(
                        f"{prefix}.gross_total "
                        "is missing."
                    )

                else:

                    warnings.append(
                        f"{prefix}.{field_name} "
                        "is empty."
                    )

                continue

            if self._decimal(
                value
            ) is None:

                errors.append(
                    f"{prefix}.{field_name} "
                    "must be numeric."
                )

        # --------------------------------------------------------------
        # Object fields
        # --------------------------------------------------------------

        for field_name in (
            self.OBJECT_FIELDS
        ):

            if field_name not in payload:
                continue

            if not isinstance(
                payload[field_name],
                dict,
            ):

                errors.append(
                    f"{prefix}.{field_name} "
                    "must be an object."
                )

        # --------------------------------------------------------------
        # Tax list
        # --------------------------------------------------------------

        if "taxes" in payload:

            errors.extend(
                self._validate_taxes(
                    payload["taxes"],
                    f"{prefix}.taxes",
                )
            )

        # --------------------------------------------------------------
        # Line items
        # --------------------------------------------------------------

        if "line_items" in payload:

            errors.extend(
                self._validate_line_items(
                    payload["line_items"],
                    f"{prefix}.line_items",
                )
            )

        # --------------------------------------------------------------
        # Scalar/string-like fields
        # --------------------------------------------------------------

        for field_name in (
            self.STRING_FIELDS
        ):

            if field_name not in payload:
                continue

            value = payload.get(
                field_name
            )

            if value is None:
                continue

            if not isinstance(
                value,
                (
                    str,
                    int,
                    float,
                ),
            ):

                errors.append(
                    f"{prefix}.{field_name} "
                    "must be a scalar value."
                )

        return (
            errors,
            warnings,
        )

    # ========================================================================
    # BATCH VALIDATION
    # ========================================================================

    def _validate_batch(
        self,
        payload: Dict[str, Any],
    ) -> Tuple[
        List[str],
        List[str],
    ]:

        errors: List[str] = []
        warnings: List[str] = []

        # --------------------------------------------------------------
        # File
        # --------------------------------------------------------------

        if (
            not isinstance(
                payload.get("file"),
                str,
            )
            or not payload.get(
                "file"
            ).strip()
        ):

            errors.append(
                "batch.file is missing or empty."
            )

        # --------------------------------------------------------------
        # Payables
        # --------------------------------------------------------------

        payables = payload.get(
            "payables"
        )

        if not isinstance(
            payables,
            list,
        ):

            errors.append(
                "batch.payables must be an array."
            )

        else:

            for index, payable in enumerate(
                payables,
                start=1,
            ):

                payable_errors, payable_warnings = (
                    self._validate_payable_structure(
                        payable,
                        f"batch.payables[{index}]",
                    )
                )

                errors.extend(
                    payable_errors
                )

                warnings.extend(
                    payable_warnings
                )

        # --------------------------------------------------------------
        # Declined
        # --------------------------------------------------------------

        declined = payload.get(
            "declined"
        )

        if not isinstance(
            declined,
            list,
        ):

            errors.append(
                "batch.declined must be an array."
            )

        else:

            for index, item in enumerate(
                declined,
                start=1,
            ):

                path = (
                    f"batch.declined[{index}]"
                )

                if not isinstance(
                    item,
                    dict,
                ):

                    errors.append(
                        f"{path} must be an object."
                    )

                    continue

                if not item.get(
                    "doc_type"
                ):

                    errors.append(
                        f"{path}.doc_type "
                        "is missing."
                    )

                if not item.get(
                    "reason"
                ):

                    errors.append(
                        f"{path}.reason "
                        "is missing."
                    )

        return (
            errors,
            warnings,
        )

    # ========================================================================
    # PAYLOAD TYPE
    # ========================================================================

    @staticmethod
    def _is_batch(
        payload: Dict[str, Any],
    ) -> bool:

        return (
            "payables" in payload
            or "declined" in payload
            or (
                "file" in payload
                and "invoice_number"
                not in payload
            )
        )

    # ========================================================================
    # FINANCIAL VALIDATION
    # ========================================================================

    def validate_financials(
        self,
        payload: Dict[str, Any],
    ) -> Tuple[
        bool,
        List[str],
        List[str],
    ]:
        """
        Delegate financial validation to the canonical FinancialValidator.

        This is intentional: schema_validator.py must not maintain a second
        ERP calculation implementation.
        """

        try:

            from src.validation.financial_validator import (
                FinancialValidator,
            )

        except ImportError:

            try:

                from ..validation.financial_validator import (
                    FinancialValidator,
                )

            except ImportError:

                return (
                    False,
                    [
                        (
                            "FinancialValidator "
                            "could not be imported."
                        )
                    ],
                    [],
                )

        result = FinancialValidator(
            tolerance=self.amount_tolerance
        ).validate(
            payload
        )

        return (
            result.valid,
            list(result.errors),
            list(result.warnings),
        )

    # ========================================================================
    # PUBLIC VALIDATION
    # ========================================================================

    def validate(
        self,
        payload: Dict[str, Any],
        validate_financials: bool = True,
    ) -> SchemaValidationResult:

        errors: List[str] = []
        warnings: List[str] = []

        # --------------------------------------------------------------
        # Root object
        # --------------------------------------------------------------

        if not isinstance(
            payload,
            dict,
        ):

            return SchemaValidationResult(
                valid=False,
                errors=[
                    "Autodraft must be a JSON object."
                ],
            )

        # --------------------------------------------------------------
        # Determine single payable vs batch
        # --------------------------------------------------------------

        if self._is_batch(
            payload
        ):

            structural_errors, structural_warnings = (
                self._validate_batch(
                    payload
                )
            )

        else:

            structural_errors, structural_warnings = (
                self._validate_payable_structure(
                    payload
                )
            )

        errors.extend(
            structural_errors
        )

        warnings.extend(
            structural_warnings
        )

        # --------------------------------------------------------------
        # Optional external JSON Schema
        # --------------------------------------------------------------

        errors.extend(
            self._validate_json_schema(
                payload
            )
        )

        # --------------------------------------------------------------
        # Financial validation
        # --------------------------------------------------------------

        if (
            validate_financials
            and not structural_errors
        ):

            if self._is_batch(
                payload
            ):

                for index, payable in enumerate(
                    payload["payables"],
                    start=1,
                ):

                    (
                        financial_valid,
                        financial_errors,
                        financial_warnings,
                    ) = self.validate_financials(
                        payable
                    )

                    if not financial_valid:

                        errors.extend(
                            (
                                f"batch.payables[{index}]: "
                                f"{error}"
                            )
                            for error in financial_errors
                        )

                    warnings.extend(
                        (
                            f"batch.payables[{index}]: "
                            f"{warning}"
                        )
                        for warning in financial_warnings
                    )

            else:

                (
                    financial_valid,
                    financial_errors,
                    financial_warnings,
                ) = self.validate_financials(
                    payload
                )

                if not financial_valid:

                    errors.extend(
                        financial_errors
                    )

                warnings.extend(
                    financial_warnings
                )

        return SchemaValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
        )


# ============================================================================
# TEST DATA
# ============================================================================

def _valid_payable() -> Dict[str, Any]:

    return {
        "invoice_number": "INV-1001",
        "invoice_date": "2026-09-10",
        "due_date": "2026-10-10",
        "invoice_type": "invoice",
        "currency": "USD",

        "supplier": {
            "supplier_id": "SUP001",
        },

        "buyer": {
            "company_code": "C001",
            "business_unit_code": "BU01",
            "location_code": "KOL",
        },

        "payment_term_id": "PT30",
        "po_number": "PO-100",
        "po_id": "POID1",

        "gross_total": "220.00",
        "subtotal": "200.00",
        "total_tax_amount": "20.00",
        "discount_amount": "0.00",
        "freight_charges": "0.00",
        "insurance_charges": "0.00",
        "extra_charges": "0.00",
        "excise_duties": "0.00",

        "taxes": [
            {
                "tax_type_code": "VAT10",
                "tax_rate": "10%",
                "tax_amount": "20.00",
            }
        ],

        "line_items": [
            {
                "description": "Product",
                "quantity": "2",
                "unit_price": "100.00",
            }
        ],
    }


# ============================================================================
# TESTS
# ============================================================================

def run_tests() -> None:

    validator = SchemaValidator()

    # ------------------------------------------------------------------------
    # 1. Valid canonical payable
    # ------------------------------------------------------------------------

    result = validator.validate(
        _valid_payable(),
        validate_financials=False,
    )

    assert result.valid, result.errors

    # ------------------------------------------------------------------------
    # 2. Missing canonical field
    # ------------------------------------------------------------------------

    payload = _valid_payable()

    del payload["gross_total"]

    result = validator.validate(
        payload,
        validate_financials=False,
    )

    assert not result.valid

    assert any(
        "gross_total" in error
        for error in result.errors
    )

    # ------------------------------------------------------------------------
    # 3. Old intermediate structure must fail
    # ------------------------------------------------------------------------

    payload = {
        "document": {
            "invoice_number": "INV-1",
        },
        "amounts": {
            "gross_amount": "100",
        },
        "line_items": [],
    }

    result = validator.validate(
        payload,
        validate_financials=False,
    )

    assert not result.valid

    # ------------------------------------------------------------------------
    # 4. Non-numeric gross
    # ------------------------------------------------------------------------

    payload = _valid_payable()

    payload["gross_total"] = "abc"

    result = validator.validate(
        payload,
        validate_financials=False,
    )

    assert not result.valid

    # ------------------------------------------------------------------------
    # 5. Non-numeric quantity
    # ------------------------------------------------------------------------

    payload = _valid_payable()

    payload["line_items"][0][
        "quantity"
    ] = "two"

    result = validator.validate(
        payload,
        validate_financials=False,
    )

    assert not result.valid

    # ------------------------------------------------------------------------
    # 6. Missing unit price
    # ------------------------------------------------------------------------

    payload = _valid_payable()

    del payload["line_items"][0][
        "unit_price"
    ]

    result = validator.validate(
        payload,
        validate_financials=False,
    )

    assert not result.valid

    # ------------------------------------------------------------------------
    # 7. Invalid tax structure
    # ------------------------------------------------------------------------

    payload = _valid_payable()

    payload["taxes"] = {
        "tax_amount": "20"
    }

    result = validator.validate(
        payload,
        validate_financials=False,
    )

    assert not result.valid

    # ------------------------------------------------------------------------
    # 8. Valid batch
    # ------------------------------------------------------------------------

    batch = {
        "file": "sample.pdf",
        "payables": [
            _valid_payable()
        ],
        "declined": [],
    }

    result = validator.validate(
        batch,
        validate_financials=False,
    )

    assert result.valid, result.errors

    # ------------------------------------------------------------------------
    # 9. Valid declined document
    # ------------------------------------------------------------------------

    batch = {
        "file": "sample.pdf",
        "payables": [],
        "declined": [
            {
                "doc_type": "purchase_order",
                "reason": (
                    "Not a payable document."
                ),
            }
        ],
    }

    result = validator.validate(
        batch,
        validate_financials=False,
    )

    assert result.valid, result.errors

    # ------------------------------------------------------------------------
    # 10. Invalid declined document
    # ------------------------------------------------------------------------

    batch["declined"] = [
        {
            "doc_type": "purchase_order"
        }
    ]

    result = validator.validate(
        batch,
        validate_financials=False,
    )

    assert not result.valid

    # ------------------------------------------------------------------------
    # 11. European numbers
    # ------------------------------------------------------------------------

    payload = _valid_payable()

    payload["gross_total"] = "1.234,56"
    payload["subtotal"] = "1.234,56"
    payload["total_tax_amount"] = "0"

    payload["line_items"][0][
        "unit_price"
    ] = "1.234,56"

    payload["line_items"][0][
        "quantity"
    ] = "1"

    result = validator.validate(
        payload,
        validate_financials=False,
    )

    assert result.valid, result.errors

    # ------------------------------------------------------------------------
    # 12. Empty optional monetary value
    # ------------------------------------------------------------------------

    payload = _valid_payable()

    payload["extra_charges"] = ""

    result = validator.validate(
        payload,
        validate_financials=False,
    )

    assert result.valid, result.errors

    assert any(
        "extra_charges is empty"
        in warning
        for warning in result.warnings
    )

    # ------------------------------------------------------------------------
    # 13. Missing gross cannot be silently invented
    # ------------------------------------------------------------------------

    payload = _valid_payable()

    payload["gross_total"] = None

    result = validator.validate(
        payload,
        validate_financials=False,
    )

    assert not result.valid

    # ------------------------------------------------------------------------
    # 14. Invalid root type
    # ------------------------------------------------------------------------

    result = validator.validate(
        [],
        validate_financials=False,
    )

    assert not result.valid

    # ------------------------------------------------------------------------
    # 15. JSON Schema loading
    # ------------------------------------------------------------------------

    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:

        schema_file = (
            Path(temp_dir)
            / "schema.json"
        )

        schema_file.write_text(
            json.dumps(
                {
                    "type": "object",
                    "required": [
                        "invoice_number"
                    ],
                }
            ),
            encoding="utf-8",
        )

        schema_validator = (
            SchemaValidator(
                schema_file
            )
        )

        result = schema_validator.validate(
            {
                "invoice_number": "INV-1"
            },
            validate_financials=False,
        )

        # External mini-schema passes, but project-level canonical
        # schema validation correctly fails.
        assert not result.valid

    print(
        "ALL SCHEMA VALIDATOR TESTS PASSED"
    )


if __name__ == "__main__":
    run_tests()