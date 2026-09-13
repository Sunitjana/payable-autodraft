# src/validation/erp_validator.py

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ERPValidationResult:
    """Result of validating a generated payable against the supplied ERP."""

    valid: bool
    erp_available: bool

    document_gross: Optional[Decimal]
    erp_gross: Optional[Decimal]

    erp_result: Any = None

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class ERPValidator:
    """
    Validate an autodraft payload using the supplied erp.py.

    Important:
    - This class does NOT implement ERP calculations.
    - The supplied erp.py remains unchanged.
    - The generated payable is passed directly to the ERP.
    - The document gross_total must match the ERP's will_book_gross.
    """

    DEFAULT_FUNCTION_NAMES = (
        "validate",
        "calculate",
        "calculate_payable",
        "calculate_invoice",
        "book",
        "book_payable",
        "process",
    )

    def __init__(
        self,
        erp_path: str | Path,
        function_name: Optional[str] = None,
        tolerance: Decimal = Decimal("0.01"),
    ) -> None:
        self.erp_path = Path(erp_path)
        self.function_name = function_name
        self.tolerance = Decimal(str(tolerance))

        self.erp_module: Any = None
        self.erp_function: Any = None

    # ============================================================
    # Decimal helpers
    # ============================================================

    @staticmethod
    def decimal(value: Any) -> Optional[Decimal]:
        """
        Safely convert a value to Decimal.

        Handles:
        - Decimal
        - int
        - float
        - numeric strings
        - comma-formatted financial strings
        """
        if value is None:
            return None

        if isinstance(value, Decimal):
            return value

        if isinstance(value, bool):
            return None

        try:
            text = str(value).strip()

            if not text:
                return None

            # Handle common financial formatting.
            text = text.replace(",", "")
            text = text.replace("₹", "")
            text = text.replace("$", "")
            text = text.replace("€", "")
            text = text.replace("£", "")

            return Decimal(text)

        except (InvalidOperation, ValueError, TypeError):
            return None

    # ============================================================
    # ERP loading
    # ============================================================

    def load(self) -> None:
        """
        Dynamically load the supplied erp.py.

        erp.py itself is never modified.
        """

        if self.erp_function is not None:
            return

        if not self.erp_path.exists():
            raise FileNotFoundError(
                f"ERP file not found: {self.erp_path}"
            )

        spec = importlib.util.spec_from_file_location(
            "supplied_erp",
            str(self.erp_path),
        )

        if spec is None or spec.loader is None:
            raise ImportError(
                f"Unable to load supplied ERP module: {self.erp_path}"
            )

        module = importlib.util.module_from_spec(spec)

        spec.loader.exec_module(module)

        self.erp_module = module

        # --------------------------------------------------------
        # Explicit function
        # --------------------------------------------------------

        if self.function_name:
            function = getattr(
                module,
                self.function_name,
                None,
            )

            if not callable(function):
                raise AttributeError(
                    f"ERP function '{self.function_name}' "
                    f"was not found in {self.erp_path}."
                )

            self.erp_function = function
            return

        # --------------------------------------------------------
        # Automatic discovery
        # --------------------------------------------------------

        for name in self.DEFAULT_FUNCTION_NAMES:
            function = getattr(module, name, None)

            if callable(function):
                self.function_name = name
                self.erp_function = function
                return

        raise AttributeError(
            "Could not find a supported public calculation "
            "function in erp.py. Set function_name explicitly."
        )

    # ============================================================
    # ERP response extraction
    # ============================================================

    def extract_erp_gross(
        self,
        result: Any,
    ) -> Optional[Decimal]:
        """
        Extract the ERP-calculated booking gross.

        The supplied ERP is expected to return something like:

            {
                "will_book_gross": Decimal("8397.36"),
                "currency": "THB"
            }

        We intentionally prioritize will_book_gross and do not
        recursively search arbitrary numeric values because doing
        so can accidentally interpret tax, subtotal, or another
        amount as the gross amount.
        """

        if result is None:
            return None

        # Direct numeric result.
        if isinstance(result, (int, float, Decimal)):
            return self.decimal(result)

        # Dictionary response.
        if isinstance(result, dict):
            for key in (
                "will_book_gross",
                "will_book_gross_amount",
            ):
                if key in result:
                    return self.decimal(result[key])

            # Some compatible ERP implementations may use these
            # explicit gross names.
            for key in (
                "gross_total",
                "gross_amount",
                "grossAmount",
            ):
                if key in result:
                    return self.decimal(result[key])

            return None

        # Object response.
        for attribute in (
            "will_book_gross",
            "will_book_gross_amount",
            "gross_total",
            "gross_amount",
            "grossAmount",
        ):
            if hasattr(result, attribute):
                return self.decimal(
                    getattr(result, attribute)
                )

        return None

    # ============================================================
    # Payload validation
    # ============================================================

    @staticmethod
    def _validate_payload_structure(
        payload: Dict[str, Any],
    ) -> List[str]:
        """
        Basic structural checks before calling ERP.

        This is deliberately not a replacement for schema
        validation. AUTODRAFT_SCHEMA validation should also run
        elsewhere in the pipeline.
        """

        errors: List[str] = []

        if not isinstance(payload, dict):
            return ["ERP payload must be a dictionary."]

        required_fields = (
            "invoice_number",
            "invoice_date",
            "invoice_type",
            "currency",
            "supplier",
            "buyer",
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

        for field_name in required_fields:
            if field_name not in payload:
                errors.append(
                    f"ERP payload is missing required field "
                    f"'{field_name}'."
                )

        return errors

    # ============================================================
    # Gross extraction from document
    # ============================================================

    def extract_document_gross(
        self,
        payload: Dict[str, Any],
    ) -> Optional[Decimal]:
        """
        Extract the document gross from the canonical autodraft
        schema.

        IMPORTANT:
        gross_total is the canonical field.

        Do NOT use:
            payload["amounts"]["gross_amount"]
        """

        if not isinstance(payload, dict):
            return None

        return self.decimal(
            payload.get("gross_total")
        )

    # ============================================================
    # Currency check
    # ============================================================

    @staticmethod
    def _extract_currency(
        payload: Dict[str, Any],
        erp_result: Any,
    ) -> Optional[str]:
        """Extract currency from payload or ERP response."""

        payload_currency = payload.get("currency")

        if payload_currency:
            return str(payload_currency).strip()

        if isinstance(erp_result, dict):
            currency = erp_result.get("currency")

            if currency:
                return str(currency).strip()

        return None

    # ============================================================
    # Validate
    # ============================================================

    def validate(
        self,
        payload: Dict[str, Any],
    ) -> ERPValidationResult:
        """
        Run the generated payable through the supplied ERP and
        verify that the ERP booking gross equals gross_total.

        Returns a structured ERPValidationResult.
        """

        errors: List[str] = []
        warnings: List[str] = []

        # --------------------------------------------------------
        # 1. Basic payload validation
        # --------------------------------------------------------

        structure_errors = self._validate_payload_structure(
            payload
        )

        if structure_errors:
            return ERPValidationResult(
                valid=False,
                erp_available=False,
                document_gross=self.extract_document_gross(
                    payload
                ),
                erp_gross=None,
                errors=structure_errors,
                warnings=warnings,
            )

        # --------------------------------------------------------
        # 2. Extract document gross
        # --------------------------------------------------------

        document_gross = self.extract_document_gross(
            payload
        )

        if document_gross is None:
            return ERPValidationResult(
                valid=False,
                erp_available=False,
                document_gross=None,
                erp_gross=None,
                errors=[
                    "Document gross_total is missing or invalid."
                ],
                warnings=warnings,
            )

        # --------------------------------------------------------
        # 3. Validate gross is non-negative
        # --------------------------------------------------------

        if document_gross < Decimal("0"):
            return ERPValidationResult(
                valid=False,
                erp_available=False,
                document_gross=document_gross,
                erp_gross=None,
                errors=[
                    "Document gross_total cannot be negative."
                ],
                warnings=warnings,
            )

        # --------------------------------------------------------
        # 4. Load ERP
        # --------------------------------------------------------

        try:
            self.load()

        except Exception as exc:
            return ERPValidationResult(
                valid=False,
                erp_available=False,
                document_gross=document_gross,
                erp_gross=None,
                errors=[
                    f"Unable to load ERP: {exc}"
                ],
                warnings=warnings,
            )

        # --------------------------------------------------------
        # 5. Call supplied ERP
        # --------------------------------------------------------

        try:
            erp_result = self.erp_function(payload)

        except Exception as exc:
            return ERPValidationResult(
                valid=False,
                erp_available=True,
                document_gross=document_gross,
                erp_gross=None,
                erp_result=None,
                errors=[
                    f"ERP calculation failed: {exc}"
                ],
                warnings=warnings,
            )

        # --------------------------------------------------------
        # 6. Extract ERP gross
        # --------------------------------------------------------

        erp_gross = self.extract_erp_gross(
            erp_result
        )

        if erp_gross is None:
            return ERPValidationResult(
                valid=False,
                erp_available=True,
                document_gross=document_gross,
                erp_gross=None,
                erp_result=erp_result,
                errors=[
                    "ERP returned a result, but "
                    "'will_book_gross' could not be identified."
                ],
                warnings=warnings,
            )

        # --------------------------------------------------------
        # 7. Currency check
        # --------------------------------------------------------

        document_currency = payload.get("currency")

        if isinstance(erp_result, dict):
            erp_currency = erp_result.get("currency")

            if (
                document_currency
                and erp_currency
                and str(document_currency).strip().upper()
                != str(erp_currency).strip().upper()
            ):
                errors.append(
                    f"ERP currency '{erp_currency}' does not match "
                    f"document currency '{document_currency}'."
                )

        # --------------------------------------------------------
        # 8. Gross comparison
        # --------------------------------------------------------

        difference = abs(
            document_gross - erp_gross
        )

        if difference > self.tolerance:
            errors.append(
                "ERP gross amount does not match document gross_total: "
                f"document={document_gross}, "
                f"erp={erp_gross}, "
                f"difference={difference}, "
                f"tolerance={self.tolerance}."
            )

        # --------------------------------------------------------
        # 9. Final result
        # --------------------------------------------------------

        return ERPValidationResult(
            valid=len(errors) == 0,
            erp_available=True,
            document_gross=document_gross,
            erp_gross=erp_gross,
            erp_result=erp_result,
            errors=errors,
            warnings=warnings,
        )