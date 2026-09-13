"""
Financial validation for payable autodraft payloads.

The validator is intentionally aligned with the supplied erp.py calculation
engine. It validates the canonical AUTODRAFT_SCHEMA structure and does not
invent missing financial values.

Canonical financial fields:
    subtotal
    total_tax_amount
    discount_amount
    freight_charges
    insurance_charges
    extra_charges
    excise_duties
    taxes
    line_items
    gross_total

Important:
    - gross_total represents the ERP-bookable gross amount.
    - Withholding tax / amount payable is NOT treated as gross_total.
    - Missing quantity/unit_price cannot be replaced by line "amount",
      because erp.py would calculate such a line as zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
import re


MONEY_QUANTUM = Decimal("0.01")


# ---------------------------------------------------------------------------
# Decimal helpers
# ---------------------------------------------------------------------------

def decimal(value: Any) -> Optional[Decimal]:
    """
    Convert common numeric representations to Decimal.

    Supports:
        1234.56
        1,234.56
        1234,56
        1.234,56
        ₹1,234.56
        7%
    """
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, Decimal):
        return value

    if isinstance(value, int):
        return Decimal(value)

    if isinstance(value, float):
        return Decimal(str(value))

    text = str(value).strip()

    if not text:
        return None

    text = text.replace("\u00a0", " ").strip()

    # Keep digits, decimal/group separators and sign.
    text = re.sub(r"[^\d,.\-+]", "", text)

    if not text:
        return None

    # Both separators exist:
    #
    # 1,234.56 -> US style
    # 1.234,56 -> European style
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            # European:
            # 1.234,56 -> 1234.56
            text = text.replace(".", "")
            text = text.replace(",", ".")
        else:
            # US:
            # 1,234.56 -> 1234.56
            text = text.replace(",", "")

    elif "," in text:
        parts = text.split(",")

        # 1234,56 -> 1234.56
        if len(parts) > 1 and len(parts[-1]) in (1, 2):
            text = "".join(parts[:-1]) + "." + parts[-1]
        else:
            # 1,234 -> 1234
            text = text.replace(",", "")

    try:
        return Decimal(text)

    except InvalidOperation:
        return None


def money(value: Decimal) -> Decimal:
    """
    ERP-compatible monetary rounding.

    erp.py uses ROUND_HALF_UP to two decimal places.
    """
    return Decimal(value).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class FinancialValidationResult:
    """
    Result returned by FinancialValidator.

    The public fields are kept compatible with the previous validator API.
    """

    valid: bool

    calculated_subtotal: Decimal = Decimal("0.00")
    calculated_tax: Decimal = Decimal("0.00")
    calculated_discount: Decimal = Decimal("0.00")
    calculated_charges: Decimal = Decimal("0.00")
    calculated_gross: Decimal = Decimal("0.00")

    document_gross: Optional[Decimal] = None

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Financial validator
# ---------------------------------------------------------------------------

class FinancialValidator:
    """
    Validate financial consistency of a canonical payable payload.

    The calculation intentionally mirrors erp.py:

        line subtotal
        - header discount
        + line taxes
        + header taxes
        + freight
        + insurance
        + extra charges
        + excise duties
        = gross_total
    """

    def __init__(
        self,
        tolerance: Decimal = Decimal("0.01"),
    ):
        self.tolerance = Decimal(str(tolerance))

    # ------------------------------------------------------------------
    # Basic helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _number(value: Any) -> Optional[Decimal]:
        return decimal(value)

    def _is_close(
        self,
        first: Decimal,
        second: Decimal,
    ) -> bool:
        return abs(first - second) <= self.tolerance

    def _compare(
        self,
        label: str,
        document_value: Optional[Decimal],
        calculated_value: Decimal,
        errors: List[str],
        warnings: List[str],
    ) -> None:
        """
        Compare an explicitly supplied document value with the calculated
        value.

        Missing optional totals generate warnings rather than fabricated
        values.
        """
        if document_value is None:
            warnings.append(
                f"{label} is not provided; cannot compare."
            )
            return

        if not self._is_close(
            document_value,
            calculated_value,
        ):
            errors.append(
                f"{label} mismatch: "
                f"document={document_value:.2f}, "
                f"calculated={calculated_value:.2f}."
            )

    # ------------------------------------------------------------------
    # Line calculation
    # ------------------------------------------------------------------

    def _calculate_line_base(
        self,
        line_item: Dict[str, Any],
        line_number: int,
        errors: List[str],
    ) -> Decimal:
        """
        Calculate the ERP-compatible line base.

        IMPORTANT:
        We do NOT fall back to line_item["amount"] when quantity or
        unit_price is missing.

        erp.py does:
            quantity * unit_price

        Missing values therefore become zero in the ERP calculation.
        Accepting an explicit amount here would create a false validation
        result.
        """

        quantity = self._number(
            line_item.get("quantity")
        )

        unit_price = self._number(
            line_item.get("unit_price")
        )

        if quantity is None or unit_price is None:
            errors.append(
                f"Line item {line_number}: "
                "quantity and unit_price are required "
                "for ERP-compatible calculation."
            )

            return Decimal("0.00")

        # Same basic calculation as erp.py.
        subtotal = money(
            quantity * unit_price
        )

        # --------------------------------------------------------------
        # Line discount percentage
        # --------------------------------------------------------------

        discount_percentage = self._number(
            line_item.get("discount_percentage")
        )

        # --------------------------------------------------------------
        # Line discount amount
        # --------------------------------------------------------------

        discount_amount = self._number(
            line_item.get("discount")
        )

        if (
            discount_percentage is not None
            and abs(discount_percentage) > 0
        ):
            subtotal = money(
                subtotal
                - money(
                    subtotal
                    * abs(discount_percentage)
                    / Decimal("100")
                )
            )

        elif (
            discount_amount is not None
            and abs(discount_amount) > 0
        ):
            if quantity == 0:
                subtotal = Decimal("0.00")
            else:
                item_price = (
                    unit_price
                    - (
                        abs(discount_amount)
                        / abs(quantity)
                    )
                )

                subtotal = money(
                    item_price * quantity
                )

        return subtotal

    # ------------------------------------------------------------------
    # Line taxes
    # ------------------------------------------------------------------

    def _calculate_line_taxes(
        self,
        line_item: Dict[str, Any],
        line_base: Decimal,
    ) -> Decimal:
        """
        Calculate taxes attached to a line item.

        Supports:

            line_item["taxes"] = [
                {
                    "tax_rate": "...",
                    "tax_amount": "..."
                }
            ]

        and the legacy/simple representation:

            tax_rate
            tax_amount
        """

        taxes = line_item.get("taxes") or []

        # Support direct line tax fields.
        if not taxes:
            tax_rate = str(
                line_item.get("tax_rate") or ""
            ).strip()

            tax_amount = str(
                line_item.get("tax_amount") or ""
            ).strip()

            if tax_rate or tax_amount:
                taxes = [
                    {
                        "tax_rate": line_item.get(
                            "tax_rate",
                            "",
                        ),
                        "tax_amount": line_item.get(
                            "tax_amount",
                            "",
                        ),
                    }
                ]

        total_tax = Decimal("0.00")

        for tax in taxes:

            if not isinstance(tax, dict):
                continue

            rate = self._number(
                str(
                    tax.get("tax_rate") or ""
                )
                .replace("%", "")
                .strip()
            )

            amount = self._number(
                tax.get("tax_amount")
            )

            if amount is None:
                amount = Decimal("0.00")

            # Same behavior as erp.py:
            # if amount == 0 and rate > 0,
            # calculate the tax from the rate.
            if (
                amount == 0
                and rate is not None
                and rate > 0
            ):
                amount = money(
                    line_base
                    * rate
                    / Decimal("100")
                )

            total_tax += amount

        return money(total_tax)

    # ------------------------------------------------------------------
    # Header taxes
    # ------------------------------------------------------------------

    def _calculate_header_taxes(
        self,
        taxes: Any,
        net_base: Decimal,
        errors: List[str],
    ) -> Decimal:
        """
        Calculate header-level taxes.

        erp.py applies header tax rates against:

            line subtotal - header discount
        """

        if taxes is None:
            return Decimal("0.00")

        if not isinstance(taxes, list):
            errors.append(
                "taxes must be a list."
            )
            return Decimal("0.00")

        total_tax = Decimal("0.00")

        for index, tax in enumerate(taxes, 1):

            if not isinstance(tax, dict):
                errors.append(
                    f"Tax {index} must be an object."
                )
                continue

            rate = self._number(
                str(
                    tax.get("tax_rate") or ""
                )
                .replace("%", "")
                .strip()
            )

            amount = self._number(
                tax.get("tax_amount")
            )

            if amount is None:
                amount = Decimal("0.00")

            # Same as erp.py.
            if (
                amount == 0
                and rate is not None
                and rate > 0
            ):
                amount = money(
                    net_base
                    * rate
                    / Decimal("100")
                )

            total_tax += amount

        return money(total_tax)

    # ------------------------------------------------------------------
    # Charges
    # ------------------------------------------------------------------

    def _calculate_charges(
        self,
        payload: Dict[str, Any],
    ) -> Decimal:
        """
        Calculate all ERP-supported header charges.
        """

        freight = (
            self._number(
                payload.get("freight_charges")
            )
            or Decimal("0.00")
        )

        insurance = (
            self._number(
                payload.get("insurance_charges")
            )
            or Decimal("0.00")
        )

        extra = (
            self._number(
                payload.get("extra_charges")
            )
            or Decimal("0.00")
        )

        excise = (
            self._number(
                payload.get("excise_duties")
            )
            or Decimal("0.00")
        )

        return money(
            freight
            + insurance
            + extra
            + excise
        )

    # ------------------------------------------------------------------
    # Main validation
    # ------------------------------------------------------------------

    def validate(
        self,
        payload: Dict[str, Any],
    ) -> FinancialValidationResult:
        """
        Validate a canonical AUTODRAFT_SCHEMA payable payload.
        """

        errors: List[str] = []
        warnings: List[str] = []

        if not isinstance(payload, dict):
            return FinancialValidationResult(
                valid=False,
                errors=[
                    "Financial payload must be an object."
                ],
            )

        # Normalize the older aggregate representation into the canonical
        # fields before running the ERP-aligned calculation.
        calculation_payload = dict(payload)
        amounts = payload.get("amounts")

        if "discount_amount" not in calculation_payload:
            if isinstance(amounts, dict):
                calculation_payload["discount_amount"] = (
                    amounts.get("total_discount")
                )
            elif isinstance(payload.get("discounts"), list):
                calculation_payload["discount_amount"] = sum(
                    (
                        self._number(item.get("amount"))
                        or Decimal("0.00")
                        for item in payload["discounts"]
                        if isinstance(item, dict)
                    ),
                    Decimal("0.00"),
                )

        if "extra_charges" not in calculation_payload:
            if isinstance(amounts, dict):
                calculation_payload["extra_charges"] = (
                    amounts.get("total_charges")
                )
            elif isinstance(payload.get("charges"), list):
                calculation_payload["extra_charges"] = sum(
                    (
                        self._number(item.get("amount"))
                        or Decimal("0.00")
                        for item in payload["charges"]
                        if isinstance(item, dict)
                    ),
                    Decimal("0.00"),
                )

        legacy_taxes = payload.get("taxes")
        if isinstance(legacy_taxes, list):
            calculation_payload["taxes"] = [
                {
                    **tax,
                    "tax_amount": tax.get(
                        "tax_amount",
                        tax.get("amount", ""),
                    ),
                    "tax_rate": tax.get(
                        "tax_rate",
                        tax.get("rate", ""),
                    ),
                }
                for tax in legacy_taxes
                if isinstance(tax, dict)
            ]

        # ==============================================================
        # Line items
        # ==============================================================

        line_items = payload.get(
            "line_items"
        ) or []

        if not isinstance(line_items, list):
            errors.append(
                "line_items must be a list."
            )
            line_items = []

        calculated_subtotal = Decimal("0.00")
        calculated_line_tax = Decimal("0.00")

        for line_number, line_item in enumerate(
            line_items,
            start=1,
        ):

            if not isinstance(line_item, dict):
                errors.append(
                    f"Line item {line_number} "
                    "must be an object."
                )
                continue

            line_base = self._calculate_line_base(
                line_item,
                line_number,
                errors,
            )

            calculated_subtotal += line_base

            calculated_line_tax += (
                self._calculate_line_taxes(
                    line_item,
                    line_base,
                )
            )

        calculated_subtotal = money(
            calculated_subtotal
        )

        calculated_line_tax = money(
            calculated_line_tax
        )

        # ==============================================================
        # Header discount
        # ==============================================================

        document_discount = self._number(
            calculation_payload.get("discount_amount")
        )

        if document_discount is None:
            calculated_discount = Decimal("0.00")
        else:
            # erp.py uses abs().
            calculated_discount = money(
                abs(document_discount)
            )

        # ==============================================================
        # Net base for header taxes
        # ==============================================================

        net_base = money(
            calculated_subtotal
            - calculated_discount
        )

        # ==============================================================
        # Header taxes
        # ==============================================================

        calculated_header_tax = (
            self._calculate_header_taxes(
                calculation_payload.get("taxes"),
                net_base,
                errors,
            )
        )

        calculated_tax = money(
            calculated_line_tax
            + calculated_header_tax
        )

        # ==============================================================
        # Charges
        # ==============================================================

        calculated_charges = (
            self._calculate_charges(
                calculation_payload
            )
        )

        # ==============================================================
        # ERP-compatible gross
        # ==============================================================

        calculated_gross = money(
            calculated_subtotal
            - calculated_discount
            + calculated_tax
            + calculated_charges
        )

        # ==============================================================
        # Document values
        # ==============================================================

        document_subtotal = self._number(
            payload.get("subtotal")
        )

        document_tax = self._number(
            payload.get("total_tax_amount")
        )

        document_gross = self._number(
            payload.get("gross_total")
        )

        # ==============================================================
        # Backward compatibility with old amounts.* structure
        # ==============================================================

        # The canonical schema is always preferred.
        #
        # Legacy:
        #     amounts.subtotal
        #     amounts.total_tax
        #     amounts.total_discount
        #     amounts.total_charges
        #     amounts.gross_amount
        #
        # This allows the validator to remain compatible with older
        # pipeline code while the new canonical schema is adopted.

        amounts = payload.get("amounts")

        if isinstance(amounts, dict):

            if (
                document_subtotal is None
                and amounts.get("subtotal") is not None
            ):
                document_subtotal = self._number(
                    amounts.get("subtotal")
                )

                warnings.append(
                    "Using legacy amounts.subtotal."
                )

            if (
                document_tax is None
                and amounts.get("total_tax") is not None
            ):
                document_tax = self._number(
                    amounts.get("total_tax")
                )

                warnings.append(
                    "Using legacy amounts.total_tax."
                )

            if (
                document_gross is None
                and amounts.get("gross_amount") is not None
            ):
                document_gross = self._number(
                    amounts.get("gross_amount")
                )

                warnings.append(
                    "Using legacy amounts.gross_amount."
                )

        # ==============================================================
        # Comparisons
        # ==============================================================

        self._compare(
            "subtotal",
            document_subtotal,
            calculated_subtotal,
            errors,
            warnings,
        )

        self._compare(
            "total_tax_amount",
            document_tax,
            calculated_tax,
            errors,
            warnings,
        )

        self._compare(
            "discount_amount",
            (
                document_discount
                if document_discount is None
                else calculated_discount
            ),
            calculated_discount,
            errors,
            warnings,
        )

        self._compare(
            "gross_total",
            document_gross,
            calculated_gross,
            errors,
            warnings,
        )

        # ==============================================================
        # Missing gross is a warning, not an invented value.
        # ==============================================================
        #
        # If the document does not provide gross_total, we keep
        # calculated_gross for internal validation but never pretend that
        # it was extracted from the document.

        if document_gross is None:
            warnings.append(
                "gross_total is not provided."
            )

        # ==============================================================
        # Final result
        # ==============================================================

        return FinancialValidationResult(
            valid=not errors,
            calculated_subtotal=calculated_subtotal,
            calculated_tax=calculated_tax,
            calculated_discount=calculated_discount,
            calculated_charges=calculated_charges,
            calculated_gross=calculated_gross,
            document_gross=document_gross,
            errors=errors,
            warnings=warnings,
        )


# ===========================================================================
# TESTS
# ===========================================================================

def _assert_equal(actual: Any, expected: Any) -> None:
    assert actual == expected, (
        f"Expected {expected!r}, got {actual!r}"
    )


def test_standard_invoice() -> None:
    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": "2",
                "unit_price": "100.00",
            }
        ],
        "subtotal": "200.00",
        "discount_amount": "0.00",
        "taxes": [
            {
                "tax_rate": "10%",
                "tax_amount": "20.00",
            }
        ],
        "total_tax_amount": "20.00",
        "freight_charges": "0.00",
        "insurance_charges": "0.00",
        "extra_charges": "0.00",
        "excise_duties": "0.00",
        "gross_total": "220.00",
    }

    result = validator.validate(payload)

    assert result.valid, result.errors
    _assert_equal(
        result.calculated_subtotal,
        Decimal("200.00"),
    )
    _assert_equal(
        result.calculated_tax,
        Decimal("20.00"),
    )
    _assert_equal(
        result.calculated_gross,
        Decimal("220.00"),
    )


def test_multiple_line_items() -> None:
    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": "2",
                "unit_price": "100",
            },
            {
                "quantity": "3",
                "unit_price": "50",
            },
        ],
        "subtotal": "350",
        "total_tax_amount": "0",
        "gross_total": "350",
    }

    result = validator.validate(payload)

    assert result.valid, result.errors
    _assert_equal(
        result.calculated_subtotal,
        Decimal("350.00"),
    )
    _assert_equal(
        result.calculated_gross,
        Decimal("350.00"),
    )


def test_line_discount_percentage() -> None:
    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": "2",
                "unit_price": "100",
                "discount_percentage": "10",
            }
        ],
        "subtotal": "180",
        "total_tax_amount": "0",
        "gross_total": "180",
    }

    result = validator.validate(payload)

    assert result.valid, result.errors
    _assert_equal(
        result.calculated_subtotal,
        Decimal("180.00"),
    )


def test_line_discount_amount() -> None:
    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": "2",
                "unit_price": "100",
                "discount": "20",
            }
        ],
        "subtotal": "180",
        "total_tax_amount": "0",
        "gross_total": "180",
    }

    result = validator.validate(payload)

    assert result.valid, result.errors
    _assert_equal(
        result.calculated_subtotal,
        Decimal("180.00"),
    )


def test_line_tax() -> None:
    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": "2",
                "unit_price": "100",
                "taxes": [
                    {
                        "tax_rate": "5%",
                    }
                ],
            }
        ],
        "subtotal": "200",
        "total_tax_amount": "10",
        "gross_total": "210",
    }

    result = validator.validate(payload)

    assert result.valid, result.errors
    _assert_equal(
        result.calculated_tax,
        Decimal("10.00"),
    )
    _assert_equal(
        result.calculated_gross,
        Decimal("210.00"),
    )


def test_header_tax() -> None:
    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": "2",
                "unit_price": "100",
            }
        ],
        "subtotal": "200",
        "taxes": [
            {
                "tax_rate": "10%",
            }
        ],
        "total_tax_amount": "20",
        "gross_total": "220",
    }

    result = validator.validate(payload)

    assert result.valid, result.errors
    _assert_equal(
        result.calculated_tax,
        Decimal("20.00"),
    )


def test_header_discount() -> None:
    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": "2",
                "unit_price": "100",
            }
        ],
        "subtotal": "200",
        "discount_amount": "20",
        "total_tax_amount": "0",
        "gross_total": "180",
    }

    result = validator.validate(payload)

    assert result.valid, result.errors
    _assert_equal(
        result.calculated_discount,
        Decimal("20.00"),
    )
    _assert_equal(
        result.calculated_gross,
        Decimal("180.00"),
    )


def test_all_charges() -> None:
    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": "1",
                "unit_price": "100",
            }
        ],
        "subtotal": "100",
        "total_tax_amount": "0",
        "freight_charges": "3",
        "insurance_charges": "2",
        "extra_charges": "1",
        "excise_duties": "4",
        "gross_total": "110",
    }

    result = validator.validate(payload)

    assert result.valid, result.errors
    _assert_equal(
        result.calculated_charges,
        Decimal("10.00"),
    )
    _assert_equal(
        result.calculated_gross,
        Decimal("110.00"),
    )


def test_combined_erp_calculation() -> None:
    """
    subtotal:
        2 * 100 = 200
        10% line discount = 20
        = 180

    line tax:
        5% of 180 = 9

    header discount:
        5

    header tax:
        explicitly 17

    charges:
        3 + 2 + 1 + 4 = 10

    gross:
        180 - 5 + 9 + 17 + 10
        = 211
    """

    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": "2",
                "unit_price": "100",
                "discount_percentage": "10",
                "taxes": [
                    {
                        "tax_rate": "5%",
                    }
                ],
            }
        ],
        "subtotal": "180",
        "discount_amount": "5",
        "taxes": [
            {
                "tax_rate": "10%",
                "tax_amount": "17",
            }
        ],
        "total_tax_amount": "26",
        "freight_charges": "3",
        "insurance_charges": "2",
        "extra_charges": "1",
        "excise_duties": "4",
        "gross_total": "211",
    }

    result = validator.validate(payload)

    assert result.valid, result.errors

    _assert_equal(
        result.calculated_subtotal,
        Decimal("180.00"),
    )

    _assert_equal(
        result.calculated_tax,
        Decimal("26.00"),
    )

    _assert_equal(
        result.calculated_charges,
        Decimal("10.00"),
    )

    _assert_equal(
        result.calculated_gross,
        Decimal("211.00"),
    )


def test_hld_style_invoice() -> None:
    """
    HLD-01 style:

        12 × 600 = 7200
        management fee = 648
        VAT = 549.36

        gross = 7200 + 648 + 549.36
              = 8397.36

    Withholding:
        235.44

    Amount payable:
        8161.92

    The validator must use 8397.36 as gross_total.
    """

    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "description": "Staff 2 Units X 6 Days",
                "quantity": "12",
                "unit_price": "600.00",
            }
        ],
        "subtotal": "7200.00",
        "discount_amount": "0.00",
        "taxes": [
            {
                "tax_rate": "7%",
                "tax_amount": "549.36",
            }
        ],
        "total_tax_amount": "549.36",
        "extra_charges": "648.00",
        "gross_total": "8397.36",
    }

    result = validator.validate(payload)

    assert result.valid, result.errors

    _assert_equal(
        result.calculated_subtotal,
        Decimal("7200.00"),
    )

    _assert_equal(
        result.calculated_tax,
        Decimal("549.36"),
    )

    _assert_equal(
        result.calculated_charges,
        Decimal("648.00"),
    )

    _assert_equal(
        result.calculated_gross,
        Decimal("8397.36"),
    )

    _assert_equal(
        result.document_gross,
        Decimal("8397.36"),
    )


def test_gross_mismatch() -> None:
    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": "1",
                "unit_price": "100",
            }
        ],
        "subtotal": "100",
        "total_tax_amount": "0",
        "gross_total": "101",
    }

    result = validator.validate(payload)

    assert not result.valid

    assert any(
        "gross_total mismatch" in error
        for error in result.errors
    )


def test_line_amount_cannot_replace_quantity_price() -> None:
    """
    Explicit amount must NOT be used when quantity/unit_price are absent.
    """

    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "description": "Product",
                "amount": "100",
            }
        ],
        "gross_total": "0",
    }

    result = validator.validate(payload)

    assert not result.valid

    assert any(
        "quantity and unit_price are required"
        in error
        for error in result.errors
    )


def test_missing_optional_totals() -> None:
    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": "1",
                "unit_price": "100",
            }
        ]
    }

    result = validator.validate(payload)

    # No contradiction exists, so calculation itself is valid.
    assert result.valid, result.errors

    _assert_equal(
        result.calculated_gross,
        Decimal("100.00"),
    )

    assert any(
        "gross_total is not provided"
        in warning
        for warning in result.warnings
    )


def test_european_numbers() -> None:
    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": "1",
                "unit_price": "1.234,56",
            }
        ],
        "subtotal": "1.234,56",
        "total_tax_amount": "0",
        "gross_total": "1.234,56",
    }

    result = validator.validate(payload)

    assert result.valid, result.errors

    _assert_equal(
        result.calculated_subtotal,
        Decimal("1234.56"),
    )

    _assert_equal(
        result.calculated_gross,
        Decimal("1234.56"),
    )


def test_currency_numbers() -> None:
    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": "1",
                "unit_price": "₹1,234.56",
            }
        ],
        "subtotal": "₹1,234.56",
        "total_tax_amount": "₹0",
        "gross_total": "₹1,234.56",
    }

    result = validator.validate(payload)

    assert result.valid, result.errors


def test_tolerance() -> None:
    validator = FinancialValidator(
        tolerance=Decimal("0.01")
    )

    payload = {
        "line_items": [
            {
                "quantity": "1",
                "unit_price": "100",
            }
        ],
        "subtotal": "100",
        "total_tax_amount": "0",
        "gross_total": "100.01",
    }

    result = validator.validate(payload)

    assert result.valid, result.errors


def test_outside_tolerance() -> None:
    validator = FinancialValidator(
        tolerance=Decimal("0.01")
    )

    payload = {
        "line_items": [
            {
                "quantity": "1",
                "unit_price": "100",
            }
        ],
        "subtotal": "100",
        "total_tax_amount": "0",
        "gross_total": "100.02",
    }

    result = validator.validate(payload)

    assert not result.valid


def test_legacy_amounts_support() -> None:
    """
    Old pipeline structure is accepted for backward compatibility.
    Canonical fields still have priority.
    """

    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": "1",
                "unit_price": "100",
            }
        ],
        "amounts": {
            "subtotal": "100",
            "total_tax": "0",
            "gross_amount": "100",
        },
    }

    result = validator.validate(payload)

    assert result.valid, result.errors

    _assert_equal(
        result.document_gross,
        Decimal("100.00"),
    )


def test_invalid_payload() -> None:
    validator = FinancialValidator()

    result = validator.validate(
        "invalid"
    )

    assert not result.valid
    assert any(
        "must be an object"
        in error
        for error in result.errors
    )


def test_invalid_line_items() -> None:
    validator = FinancialValidator()

    result = validator.validate(
        {
            "line_items": "invalid",
            "gross_total": "0",
        }
    )

    assert not result.valid

    assert any(
        "line_items must be a list"
        in error
        for error in result.errors
    )


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_tests() -> None:
    test_standard_invoice()
    test_multiple_line_items()
    test_line_discount_percentage()
    test_line_discount_amount()
    test_line_tax()
    test_header_tax()
    test_header_discount()
    test_all_charges()
    test_combined_erp_calculation()
    test_hld_style_invoice()
    test_gross_mismatch()
    test_line_amount_cannot_replace_quantity_price()
    test_missing_optional_totals()
    test_european_numbers()
    test_currency_numbers()
    test_tolerance()
    test_outside_tolerance()
    test_legacy_amounts_support()
    test_invalid_payload()
    test_invalid_line_items()

    print("ALL FINANCIAL VALIDATOR TESTS PASSED")


if __name__ == "__main__":
    run_tests()