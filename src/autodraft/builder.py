# src/autodraft/builder.py

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional


@dataclass
class AutoDraftResult:
    success: bool
    payload: Optional[Dict[str, Any]]
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class AutoDraftBuilder:
    """
    Build the canonical payable autodraft.

    The output of this builder is intended to match
    AUTODRAFT_SCHEMA.md and the supplied erp.py contract.

    Design principles:
    - Never invent master-data IDs/codes.
    - Preserve raw/extracted values where available.
    - Populate master-data IDs/codes only after a genuine match.
    - Keep financial fields explicit.
    - Keep tax/discount/charge placement intact.
    - Do not perform ERP calculations here.
    - ERP validation is handled separately by ERPValidator.
    """

    def __init__(
        self,
        allow_unresolved_master_data: bool = False,
    ) -> None:
        self.allow_unresolved_master_data = (
            allow_unresolved_master_data
        )

    # ============================================================
    # Generic utilities
    # ============================================================

    @staticmethod
    def _decimal(
        value: Any,
    ) -> Optional[Decimal]:
        """Safely convert a value to Decimal."""

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

            text = text.replace(",", "")
            text = text.replace("₹", "")
            text = text.replace("$", "")
            text = text.replace("€", "")
            text = text.replace("£", "")

            return Decimal(text)

        except (
            InvalidOperation,
            ValueError,
            TypeError,
        ):
            return None

    @staticmethod
    def _serialize(
        value: Any,
    ) -> Any:
        """Convert Decimal values recursively to JSON-safe values."""

        if isinstance(value, Decimal):
            return str(value)

        if isinstance(value, dict):
            return {
                key: AutoDraftBuilder._serialize(val)
                for key, val in value.items()
            }

        if isinstance(value, list):
            return [
                AutoDraftBuilder._serialize(item)
                for item in value
            ]

        if isinstance(value, tuple):
            return [
                AutoDraftBuilder._serialize(item)
                for item in value
            ]

        return value

    @staticmethod
    def _to_dict(
        value: Any,
    ) -> Dict[str, Any]:
        """Convert dataclass/object/dict into a dictionary."""

        if value is None:
            return {}

        if isinstance(value, dict):
            return dict(value)

        if hasattr(value, "to_dict"):
            result = value.to_dict()

            if isinstance(result, dict):
                return dict(result)

        if hasattr(value, "__dict__"):
            return {
                key: val
                for key, val in vars(value).items()
                if not key.startswith("_")
            }

        return {}

    @staticmethod
    def _matched_record(
        result: Any,
    ) -> Optional[Dict[str, Any]]:
        """
        Return the actual master-data record from a matcher result.

        Different matchers may expose the record under different
        attribute names.
        """

        if result is None:
            return None

        if isinstance(result, dict):
            # Some matcher implementations may return:
            # {"matched": True, "supplier": {...}}
            for key in (
                "supplier",
                "purchase_order",
                "po",
                "tax",
                "payment_terms",
                "account",
                "buyer",
                "record",
                "data",
            ):
                value = result.get(key)

                if isinstance(value, dict) and value:
                    return value

            # A direct record can also be returned.
            if result:
                return result

        for attribute in (
            "supplier",
            "purchase_order",
            "po",
            "tax",
            "payment_terms",
            "account",
            "buyer",
            "record",
            "data",
        ):
            if hasattr(result, attribute):
                value = getattr(
                    result,
                    attribute,
                )

                if value:
                    return AutoDraftBuilder._to_dict(
                        value
                    )

        return None

    @staticmethod
    def _is_matched(
        result: Any,
    ) -> bool:
        """Determine whether a matcher result represents a real match."""

        if result is None:
            return False

        if isinstance(result, dict):
            if "matched" in result:
                return bool(result["matched"])

            if "success" in result:
                return bool(result["success"])

            # A non-empty direct record is considered usable.
            return bool(result)

        return bool(
            getattr(
                result,
                "matched",
                False,
            )
        )

    @staticmethod
    def _first(
        data: Dict[str, Any],
        *keys: str,
    ) -> Any:
        """Return the first non-empty value."""

        for key in keys:
            value = data.get(key)

            if value is not None and value != "":
                return value

        return None

    # ============================================================
    # Master-data mapping helpers
    # ============================================================

    def _build_supplier(
        self,
        header: Dict[str, Any],
        supplier_result: Any,
    ) -> Dict[str, Any]:
        """
        Build supplier section.

        Supplier master-data ID is populated only on a genuine
        matcher result.
        """

        matched = self._matched_record(
            supplier_result
        )

        header_name = self._first(
            header,
            "supplier_name",
            "vendor_name",
            "supplier",
            "vendor",
        )

        header_tax_id = self._first(
            header,
            "supplier_tax_id",
            "vendor_tax_id",
            "tax_id",
        )

        result: Dict[str, Any] = {
            "supplier_id": None,
            "supplier_name": header_name,
            "supplier_tax_id": header_tax_id,
        }

        if matched:
            result["supplier_id"] = self._first(
                matched,
                "supplier_id",
                "id",
                "supplierId",
            )

            result["supplier_name"] = self._first(
                matched,
                "supplier_name",
                "name",
                "supplierName",
            ) or header_name

            result["supplier_tax_id"] = self._first(
                matched,
                "supplier_tax_id",
                "tax_id",
                "taxId",
                "supplierTaxId",
            ) or header_tax_id

        return result

    def _build_buyer(
        self,
        header: Dict[str, Any],
        account_result: Any,
    ) -> Dict[str, Any]:
        """
        Build buyer section.

        IMPORTANT:
        No buyer/company/account code is invented when there is
        no genuine match.
        """

        matched = self._matched_record(
            account_result
        )

        buyer: Dict[str, Any] = {
            "company_code": None,
            "business_unit_code": None,
            "location_code": None,
        }

        # First use extracted values if they genuinely exist.
        buyer["company_code"] = self._first(
            header,
            "company_code",
            "buyer_company_code",
        )

        buyer["business_unit_code"] = self._first(
            header,
            "business_unit_code",
            "buyer_business_unit_code",
        )

        buyer["location_code"] = self._first(
            header,
            "location_code",
            "buyer_location_code",
        )

        # Override with master data only when matched.
        if matched:
            buyer["company_code"] = (
                self._first(
                    matched,
                    "company_code",
                    "companyCode",
                )
                or buyer["company_code"]
            )

            buyer["business_unit_code"] = (
                self._first(
                    matched,
                    "business_unit_code",
                    "businessUnitCode",
                )
                or buyer["business_unit_code"]
            )

            buyer["location_code"] = (
                self._first(
                    matched,
                    "location_code",
                    "locationCode",
                )
                or buyer["location_code"]
            )

        return buyer

    def _build_payment_term(
        self,
        header: Dict[str, Any],
        payment_terms_result: Any,
    ) -> Optional[str]:
        """Return payment term ID only when available."""

        matched = self._matched_record(
            payment_terms_result
        )

        if matched:
            return self._first(
                matched,
                "payment_term_id",
                "payment_terms_id",
                "term_id",
                "id",
                "code",
            )

        # Preserve an explicitly extracted code/value.
        return self._first(
            header,
            "payment_term_id",
            "payment_terms_id",
            "payment_term_code",
        )

    def _build_po(
        self,
        header: Dict[str, Any],
        po_result: Any,
    ) -> tuple[Optional[str], Optional[str]]:
        """Return PO number and matched PO ID."""

        po_number = self._first(
            header,
            "po_number",
            "purchase_order_number",
            "purchase_order",
        )

        po_id = None

        matched = self._matched_record(
            po_result
        )

        if matched:
            po_id = self._first(
                matched,
                "po_id",
                "purchase_order_id",
                "id",
            )

            po_number = (
                self._first(
                    matched,
                    "po_number",
                    "purchase_order_number",
                    "number",
                )
                or po_number
            )

        return po_number, po_id

    # ============================================================
    # Line items
    # ============================================================

    def _build_line_items(
        self,
        line_items: List[Any],
    ) -> List[Dict[str, Any]]:
        """
        Build ERP-compatible line items.

        Only extraction metadata is removed. Financial and
        classification fields are preserved.
        """

        result: List[Dict[str, Any]] = []

        for item in line_items:
            data = self._to_dict(item)

            if not data:
                continue

            # Internal metadata must not reach ERP.
            for key in (
                "raw_text",
                "confidence",
                "source",
                "page_number",
                "bbox",
                "block_bbox",
                "extraction_method",
            ):
                data.pop(key, None)

            # Normalize common names.
            if "quantity" not in data:
                data["quantity"] = self._first(
                    data,
                    "qty",
                )

            if "unit_price" not in data:
                data["unit_price"] = self._first(
                    data,
                    "price",
                    "rate",
                )

            if "description" not in data:
                data["description"] = self._first(
                    data,
                    "item_description",
                    "name",
                    "item",
                )

            result.append(data)

        return result

    # ============================================================
    # Taxes
    # ============================================================

    def _build_taxes(
        self,
        taxes: List[Any],
        tax_results: Optional[List[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Build tax entries.

        tax_type_code is populated only when the tax matcher
        produces a genuine match.
        """

        result: List[Dict[str, Any]] = []

        tax_results = tax_results or []

        for index, tax in enumerate(taxes):
            data = self._to_dict(tax)

            if not data:
                continue

            for key in (
                "raw_text",
                "confidence",
                "source",
                "page_number",
                "bbox",
                "block_bbox",
                "extraction_method",
            ):
                data.pop(key, None)

            matcher_result = (
                tax_results[index]
                if index < len(tax_results)
                else None
            )

            matched = self._matched_record(
                matcher_result
            )

            if matched:
                tax_code = self._first(
                    matched,
                    "tax_type_code",
                    "tax_code",
                    "code",
                    "id",
                )

                if tax_code is not None:
                    data["tax_type_code"] = tax_code

            # Never invent a tax code.
            data.setdefault(
                "tax_type_code",
                None,
            )

            result.append(data)

        return result

    # ============================================================
    # Discounts
    # ============================================================

    def _build_discounts(
        self,
        discounts: List[Any],
    ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []

        for discount in discounts:
            data = self._to_dict(discount)

            if not data:
                continue

            for key in (
                "raw_text",
                "confidence",
                "source",
                "page_number",
                "bbox",
                "block_bbox",
                "extraction_method",
            ):
                data.pop(key, None)

            result.append(data)

        return result

    # ============================================================
    # Charges
    # ============================================================

    def _build_charges(
        self,
        charges: List[Any],
    ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []

        for charge in charges:
            data = self._to_dict(charge)

            if not data:
                continue

            for key in (
                "raw_text",
                "confidence",
                "source",
                "page_number",
                "bbox",
                "block_bbox",
                "extraction_method",
            ):
                data.pop(key, None)

            result.append(data)

        return result

    # ============================================================
    # Financial values
    # ============================================================

    def _financial_value(
        self,
        header: Dict[str, Any],
        *keys: str,
    ) -> Optional[Any]:
        """
        Return an extracted financial value without calculating
        anything.

        Financial calculations belong to the supplied ERP.
        """

        value = self._first(
            header,
            *keys,
        )

        if value is None:
            return None

        decimal_value = self._decimal(value)

        if decimal_value is None:
            return value

        return decimal_value

    # ============================================================
    # Master-data validation
    # ============================================================

    def _validate_master_matches(
        self,
        supplier_result: Any = None,
        po_result: Any = None,
        tax_results: Optional[List[Any]] = None,
        payment_terms_result: Any = None,
        account_result: Any = None,
    ) -> tuple[List[str], List[str]]:
        """
        Return master-data errors and warnings.

        Supplier and tax failures are warnings here rather than
        fabricated values or automatic hard failures.

        The final confidence/review engine can decide whether the
        payable needs manual review.
        """

        errors: List[str] = []
        warnings: List[str] = []

        if supplier_result is not None:
            if not self._is_matched(
                supplier_result
            ):
                warnings.append(
                    "Supplier could not be reliably matched; "
                    "supplier_id will remain blank."
                )

        if po_result is not None:
            if not self._is_matched(
                po_result
            ):
                warnings.append(
                    "PO could not be reliably matched; "
                    "po_id will remain blank."
                )

        for index, result in enumerate(
            tax_results or [],
            start=1,
        ):
            if not self._is_matched(result):
                warnings.append(
                    f"Tax {index} could not be reliably matched; "
                    "tax_type_code will remain blank."
                )

        if payment_terms_result is not None:
            if not self._is_matched(
                payment_terms_result
            ):
                warnings.append(
                    "Payment terms could not be reliably matched; "
                    "payment_term_id will remain blank."
                )

        if account_result is not None:
            if not self._is_matched(
                account_result
            ):
                warnings.append(
                    "Buyer/chart-of-books mapping could not be "
                    "reliably matched; unmatched codes remain blank."
                )

        return errors, warnings

    # ============================================================
    # Build
    # ============================================================

    def build(
        self,
        header: Any,
        line_items: Optional[List[Any]] = None,
        taxes: Optional[List[Any]] = None,
        discounts: Optional[List[Any]] = None,
        charges: Optional[List[Any]] = None,
        supplier_result: Any = None,
        po_result: Any = None,
        tax_results: Optional[List[Any]] = None,
        payment_terms_result: Any = None,
        account_result: Any = None,
        supervisor_result: Optional[Dict[str, Any]] = None,
    ) -> AutoDraftResult:

        errors: List[str] = []
        warnings: List[str] = []

        line_items = line_items or []
        taxes = taxes or []
        discounts = discounts or []
        charges = charges or []
        tax_results = tax_results or []

        # --------------------------------------------------------
        # Header
        # --------------------------------------------------------

        if header is None:
            return AutoDraftResult(
                success=False,
                payload=None,
                errors=[
                    "Invoice header is missing."
                ],
            )

        header_data = self._to_dict(header)

        if not header_data:
            return AutoDraftResult(
                success=False,
                payload=None,
                errors=[
                    "Invoice header could not be converted "
                    "to a usable dictionary."
                ],
            )

        invoice_number = self._first(
            header_data,
            "invoice_number",
            "invoice_no",
            "invoice_id",
        )

        invoice_date = self._first(
            header_data,
            "invoice_date",
            "date",
        )

        invoice_type = self._first(
            header_data,
            "invoice_type",
            "document_type",
        ) or "invoice"

        currency = self._first(
            header_data,
            "currency",
            "currency_code",
        )

        # --------------------------------------------------------
        # Required payable fields
        # --------------------------------------------------------

        if not invoice_number:
            errors.append(
                "Invoice number is missing."
            )

        gross_total = self._financial_value(
            header_data,
            "gross_total",
            "gross_amount",
            "grand_total",
            "total_amount",
        )

        if gross_total is None:
            errors.append(
                "Gross total is missing."
            )

        if invoice_date is None:
            warnings.append(
                "Invoice date is missing."
            )

        if currency is None:
            warnings.append(
                "Currency is missing."
            )

        # --------------------------------------------------------
        # Master data
        # --------------------------------------------------------

        master_errors, master_warnings = (
            self._validate_master_matches(
                supplier_result=supplier_result,
                po_result=po_result,
                tax_results=tax_results,
                payment_terms_result=payment_terms_result,
                account_result=account_result,
            )
        )

        errors.extend(master_errors)
        warnings.extend(master_warnings)

        # --------------------------------------------------------
        # Strict mode
        # --------------------------------------------------------

        # Only actual structural/financial errors block building.
        # An unresolved master match must NOT result in invented
        # values. It remains blank and is surfaced as a warning.
        if errors:
            return AutoDraftResult(
                success=False,
                payload=None,
                errors=errors,
                warnings=warnings,
            )

        # --------------------------------------------------------
        # Master mappings
        # --------------------------------------------------------

        supplier = self._build_supplier(
            header_data,
            supplier_result,
        )

        buyer = self._build_buyer(
            header_data,
            account_result,
        )

        payment_term_id = (
            self._build_payment_term(
                header_data,
                payment_terms_result,
            )
        )

        po_number, po_id = self._build_po(
            header_data,
            po_result,
        )

        # --------------------------------------------------------
        # Financial fields
        # --------------------------------------------------------

        subtotal = self._financial_value(
            header_data,
            "subtotal",
            "sub_total",
        )

        total_tax_amount = self._financial_value(
            header_data,
            "total_tax_amount",
            "total_tax",
            "tax_total",
        )

        discount_amount = self._financial_value(
            header_data,
            "discount_amount",
            "total_discount",
            "discount_total",
        )

        freight_charges = self._financial_value(
            header_data,
            "freight_charges",
            "freight",
        )

        insurance_charges = self._financial_value(
            header_data,
            "insurance_charges",
            "insurance",
        )

        extra_charges = self._financial_value(
            header_data,
            "extra_charges",
            "total_charges",
            "other_charges",
        )

        excise_duties = self._financial_value(
            header_data,
            "excise_duties",
            "excise_duty",
        )

        # --------------------------------------------------------
        # Canonical autodraft payload
        # --------------------------------------------------------

        payload: Dict[str, Any] = {
            "invoice_number": invoice_number,
            "invoice_date": invoice_date,
            "due_date": self._first(
                header_data,
                "due_date",
            ),
            "invoice_type": invoice_type,
            "currency": currency,

            "supplier": supplier,

            "buyer": buyer,

            "payment_term_id": payment_term_id,

            "po_number": po_number,
            "po_id": po_id,

            "gross_total": gross_total,
            "subtotal": subtotal,

            "total_tax_amount": total_tax_amount,
            "discount_amount": discount_amount,

            "freight_charges": freight_charges,
            "insurance_charges": insurance_charges,
            "extra_charges": extra_charges,
            "excise_duties": excise_duties,

            "taxes": self._build_taxes(
                taxes,
                tax_results,
            ),

            "line_items": self._build_line_items(
                line_items
            ),

            # These are useful internally but should be removed
            # before final schema serialization if the schema does
            # not permit additional fields.
        }

        # --------------------------------------------------------
        # Optional supervisor information
        # --------------------------------------------------------

        # Do NOT place supervisor metadata inside the ERP payload.
        # It belongs to the pipeline/result metadata rather than
        # the canonical ERP schema.

        # --------------------------------------------------------
        # Remove None-only optional master IDs/codes carefully
        # --------------------------------------------------------

        # Keep the schema keys themselves. The schema/consumer can
        # represent unresolved mappings as null/blank according to
        # the project's exact schema rules.
        #
        # Do not replace unresolved IDs with guessed values.

        payload = self._serialize(
            payload
        )

        return AutoDraftResult(
            success=True,
            payload=payload,
            errors=errors,
            warnings=warnings,
        )