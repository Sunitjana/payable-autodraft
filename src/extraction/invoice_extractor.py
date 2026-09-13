# src/extraction/invoice_extractor.py

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import html
import re
from typing import Any, Dict, Optional


@dataclass
class InvoiceHeader:
    """
    Header-level information extracted from a payable document.

    This class stores extracted values only.
    It does not calculate or invent missing values.
    """

    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None

    supplier_name: Optional[str] = None
    supplier_tax_id: Optional[str] = None

    currency: Optional[str] = None

    po_number: Optional[str] = None
    payment_terms: Optional[str] = None

    subtotal: Optional[Decimal] = None
    total_tax: Optional[Decimal] = None
    total_discount: Optional[Decimal] = None
    total_charges: Optional[Decimal] = None
    gross_amount: Optional[Decimal] = None

    # Extraction completeness score.
    # This is NOT OCR/model confidence.
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert the dataclass into a JSON-friendly dictionary."""

        result = asdict(self)

        for key, value in result.items():
            if isinstance(value, Decimal):
                result[key] = str(value)

        return result


class InvoiceExtractor:
    """
    Conservative invoice-header extractor.

    Extracts:
        - invoice number
        - invoice date
        - due date
        - supplier name
        - supplier tax ID
        - currency
        - PO number
        - payment terms
        - subtotal
        - total tax
        - total discount
        - total charges
        - gross invoice amount

    Does NOT:
        - calculate missing financial values
        - invent master-data IDs
        - decide whether a document is payable
        - perform ERP validation
        - extract detailed line items
        - assume withholding tax is part of gross amount

    Supports:
        - native PDF text
        - OCR text
        - OCR HTML
        - OCR tables
        - common international amount formats
    """

    # ==================================================================
    # LABELS
    # ==================================================================

    INVOICE_NUMBER_LABELS = [
        "invoice number",
        "invoice no.",
        "invoice no",
        "invoice #",
        "inv number",
        "inv no.",
        "inv no",
        "inv #",
        "bill number",
        "bill no.",
        "bill no",
    ]

    INVOICE_DATE_LABELS = [
        "invoice date",
        "date of invoice",
        "bill date",
        "issue date",
        "issued date",
    ]

    DUE_DATE_LABELS = [
        "due date",
        "payment due date",
        "payment due",
        "due",
    ]

    PO_LABELS = [
        "purchase order number",
        "purchase order no.",
        "purchase order no",
        "purchase order #",
        "po number",
        "po no.",
        "po no",
        "po #",
    ]

    TAX_ID_LABELS = [
        "supplier tax id",
        "supplier tax number",
        "supplier vat number",
        "supplier vat no",
        "supplier gstin",
        "supplier gst number",
        "tax id",
        "tax number",
        "tax no",
        "vat number",
        "vat no",
        "gstin",
        "gst number",
        "tin",
    ]

    PAYMENT_TERMS_LABELS = [
        "payment terms",
        "payment term",
        "payment condition",
        "terms of payment",
        "payment conditions",
    ]

    SUPPLIER_LABELS = [
        "supplier",
        "vendor",
        "seller",
        "bill from",
        "sold by",
    ]

    # ==================================================================
    # FINANCIAL LABELS
    # ==================================================================

    SUBTOTAL_LABELS = [
        "subtotal",
        "sub total",
        "net amount",
        "net total",
        "amount before tax",
    ]

    TOTAL_TAX_LABELS = [
        "total tax",
        "tax total",
        "total vat",
        "vat total",
        "total gst",
        "gst total",
    ]

    DISCOUNT_LABELS = [
        "total discount",
        "discount total",
        "discount amount",
    ]

    CHARGE_LABELS = [
        "total charges",
        "total additional charges",
        "additional charges",
        "other charges",
    ]

    # Do NOT include generic "total".
    #
    # Examples that should NOT automatically become gross:
    #
    #   Total Payment
    #   Amount Paid
    #   Withholding Tax
    #   Balance After Withholding
    #
    GROSS_LABELS = [
        "total(usd)",
        "total (usd)",
        "total(eur)",
        "total (eur)",
        "total(gbp)",
        "total (gbp)",
        "total incl gst",
        "total including gst",
        "grand total including vat",
        "grand total including tax",
        "grand total",
        "total invoice amount",
        "invoice total",
        "gross amount",
        "gross total",
        "total payable",
        "total due",
        "amount due",
        "balance due",
    ]

    # ==================================================================
    # CURRENCY
    # ==================================================================

    CURRENCY_CODES = {
        "USD",
        "EUR",
        "GBP",
        "INR",
        "JPY",
        "CNY",
        "AUD",
        "CAD",
        "SGD",
        "AED",
        "CHF",
        "SEK",
        "NOK",
        "DKK",
        "PLN",
        "BRL",
        "THB",
        "TRY",
        "RUB",
        "VND",
        "HKD",
        "NZD",
        "ZAR",
        "MYR",
        "IDR",
        "PHP",
        "KRW",
    }

    CURRENCY_NAMES = {
        "UNITED STATES DOLLAR": "USD",
        "US DOLLARS": "USD",
        "US DOLLAR": "USD",
        "EUROS": "EUR",
        "EURO": "EUR",
        "POUND STERLING": "GBP",
        "POUNDS": "GBP",
        "POUND": "GBP",
        "INDIAN RUPEES": "INR",
        "INDIAN RUPEE": "INR",
        "RUPEES": "INR",
        "RUPEE": "INR",
        "JAPANESE YEN": "JPY",
        "CHINESE YUAN": "CNY",
        "THAI BAHT": "THB",
        "AUSTRALIAN DOLLAR": "AUD",
        "CANADIAN DOLLAR": "CAD",
        "SINGAPORE DOLLAR": "SGD",
    }

    CURRENCY_SYMBOLS = {
        "$": "USD",
        "€": "EUR",
        "£": "GBP",
        "₹": "INR",
        "¥": "JPY",
        "฿": "THB",
        "₩": "KRW",
        "₫": "VND",
        "₽": "RUB",
    }

    # ==================================================================
    # DATE
    # ==================================================================

    DATE_PATTERN = (
        r"("
        r"\d{1,4}[./\-]\d{1,2}[./\-]\d{1,4}"
        r"|"
        r"\d{1,2}\s+[A-Za-z]{3,12}\s+\d{2,4}"
        r"|"
        r"[A-Za-z]{3,12}\s+\d{1,2},?\s+\d{2,4}"
        r")"
    )

    # ==================================================================
    # NORMALIZATION
    # ==================================================================

    @staticmethod
    def normalize(text: str) -> str:
        """
        Normalize native PDF/OCR/HTML text.

        HTML table example:

            <td>Invoice Number</td>
            <td>INV-3001</td>

        becomes:

            Invoice Number | INV-3001
        """

        text = text or ""

        if not text:
            return ""

        # Decode entities:
        # &amp; -> &
        # &nbsp; -> space
        text = html.unescape(text)

        # Remove null bytes.
        text = text.replace("\x00", " ")

        # Normalize line endings.
        text = text.replace("\r\n", "\n")
        text = text.replace("\r", "\n")

        # --------------------------------------------------------------
        # HTML TABLE ROWS
        # --------------------------------------------------------------

        # Remove opening table row tags.
        text = re.sub(
            r"<\s*tr\b[^>]*>",
            "",
            text,
            flags=re.IGNORECASE,
        )

        # End of table row -> newline.
        text = re.sub(
            r"</\s*tr\s*>",
            "\n",
            text,
            flags=re.IGNORECASE,
        )

        # --------------------------------------------------------------
        # HTML CELLS
        # --------------------------------------------------------------

        # Remove opening td/th.
        text = re.sub(
            r"<\s*(?:td|th)\b[^>]*>",
            "",
            text,
            flags=re.IGNORECASE,
        )

        # Closing td/th -> pipe separator.
        text = re.sub(
            r"</\s*(?:td|th)\s*>",
            " | ",
            text,
            flags=re.IGNORECASE,
        )

        # --------------------------------------------------------------
        # OTHER HTML STRUCTURE
        # --------------------------------------------------------------

        text = re.sub(
            r"<\s*(?:p|div|li)\b[^>]*>",
            "",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"</\s*(?:p|div|li)\s*>",
            "\n",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"<\s*br\s*/?\s*>",
            "\n",
            text,
            flags=re.IGNORECASE,
        )

        # Remove remaining HTML tags.
        text = re.sub(
            r"<[^>]+>",
            " ",
            text,
        )

        # --------------------------------------------------------------
        # WHITESPACE
        # --------------------------------------------------------------

        text = re.sub(
            r"[ \t]+",
            " ",
            text,
        )

        # Normalize table separators.
        text = re.sub(
            r"\s*\|\s*",
            " | ",
            text,
        )

        # Prevent excessive blank lines.
        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )

        return text.strip()

    # ==================================================================
    # LABEL PATTERN
    # ==================================================================

    @staticmethod
    def _label_pattern(label: str) -> str:
        """
        Create a safe label pattern.

        Example:

            invoice number

        matches:

            Invoice Number
            Invoice Number:
            Invoice Number -
            Invoice Number #
        """

        escaped = re.escape(label)

        return (
            rf"(?<![A-Za-z])"
            rf"{escaped}"
            rf"(?![A-Za-z])"
        )

    # ==================================================================
    # FIND LABEL
    # ==================================================================

    @classmethod
    def _find_label_match(
        cls,
        text: str,
        labels: list[str],
    ) -> Optional[re.Match]:
        """
        Find a label and its associated value.

        Supports both:

            Invoice Number: INV-100

        and:

            Invoice Number | INV-100
        """

        for label in sorted(
            labels,
            key=len,
            reverse=True,
        ):

            label_pattern = cls._label_pattern(
                label
            )

            # ----------------------------------------------------------
            # Normal format
            #
            # Invoice Number: INV-100
            # ----------------------------------------------------------

            pattern = (
                label_pattern
                + r"\s*[:#\-]\s*"
                + r"([^\n\r|]+)"
            )

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                return match

            # ----------------------------------------------------------
            # Table format
            #
            # Invoice Number | INV-100
            # ----------------------------------------------------------

            pattern = (
                label_pattern
                + r"\s*\|\s*"
                + r"([^\n\r|]+)"
            )

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                return match

            # ----------------------------------------------------------
            # Label followed by whitespace.
            #
            # Example:
            #
            # Invoice Number INV-100
            #
            # Only allow this if the value starts with a sensible
            # non-space token.
            # ----------------------------------------------------------

            pattern = (
                label_pattern
                + r"\s+"
                + r"([A-Za-z0-9][^\n\r|]*)"
            )

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                return match

        return None

    # ==================================================================
    # GENERIC VALUE AFTER LABEL
    # ==================================================================

    @classmethod
    def _extract_after_label(
        cls,
        text: str,
        labels: list[str],
    ) -> Optional[str]:
        """
        Extract textual value associated with a label.
        """

        match = cls._find_label_match(
            text,
            labels,
        )

        if not match:
            return None

        value = match.group(1).strip()

        value = value.strip(
            " :#-\t"
        )

        if not value:
            return None

        return value

    # ==================================================================
    # IDENTIFIER
    # ==================================================================

    @classmethod
    def _extract_identifier(
        cls,
        text: str,
        labels: list[str],
    ) -> Optional[str]:
        """
        Extract identifier-like values.

        Examples:

            Invoice Number: INV-123
            PO Number: PO-1001
            GSTIN: ABC123456

        The identifier stops at whitespace/newline/table separator.

        Therefore:

            Invoice Number: INV-123 Grand Total: 100

        returns:

            INV-123
        """

        if not text:
            return None

        for label in sorted(
            labels,
            key=len,
            reverse=True,
        ):

            label_pattern = cls._label_pattern(
                label
            )

            # ----------------------------------------------------------
            # Explicit separator
            # ----------------------------------------------------------

            pattern = (
                label_pattern
                + r"\s*[:#\-]\s*"
                + r"([A-Za-z0-9][A-Za-z0-9./_\-]*)"
            )

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                value = match.group(1).strip()

                if value:
                    return value

            # ----------------------------------------------------------
            # Pipe/table separator
            # ----------------------------------------------------------

            pattern = (
                label_pattern
                + r"\s*\|\s*"
                + r"([A-Za-z0-9][A-Za-z0-9./_\-]*)"
            )

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                value = match.group(1).strip()

                if value:
                    return value

            # ----------------------------------------------------------
            # Whitespace-only separator
            # ----------------------------------------------------------

            pattern = (
                label_pattern
                + r"\s+"
                + r"([A-Za-z0-9][A-Za-z0-9./_\-]*)"
            )

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                value = match.group(1).strip()

                if value:
                    return value

        return None

    # ==================================================================
    # DATE
    # ==================================================================

    @classmethod
    def _extract_date(
        cls,
        text: str,
        labels: list[str],
    ) -> Optional[str]:
        """
        Extract date immediately associated with a label.
        """

        for label in sorted(
            labels,
            key=len,
            reverse=True,
        ):

            label_pattern = cls._label_pattern(
                label
            )

            # ----------------------------------------------------------
            # Normal / colon / dash format
            # ----------------------------------------------------------

            pattern = (
                label_pattern
                + r"\s*[:#\-]?\s*"
                + cls.DATE_PATTERN
            )

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                return match.group(1).strip()

            # ----------------------------------------------------------
            # Table format
            # ----------------------------------------------------------

            pattern = (
                label_pattern
                + r"\s*\|\s*"
                + cls.DATE_PATTERN
            )

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                return match.group(1).strip()

        return None

    # ==================================================================
    # DECIMAL PARSER
    # ==================================================================

    @staticmethod
    def _parse_decimal(
        value: str,
    ) -> Optional[Decimal]:
        """
        Parse international financial numbers.

        Supported:

            1,234.56
            1.234,56
            1234.56
            1234,56
            (500.00)
            -500.00
            ₹1,234.50
        """

        if not value:
            return None

        value = value.strip()

        negative = False

        if (
            value.startswith("(")
            and value.endswith(")")
        ):
            negative = True

        elif value.startswith("-"):
            negative = True

        # Keep only numeric separators.
        cleaned = re.sub(
            r"[^\d.,]",
            "",
            value,
        )

        if not cleaned:
            return None

        # --------------------------------------------------------------
        # Both comma and dot
        # --------------------------------------------------------------

        if "," in cleaned and "." in cleaned:

            # Last separator is decimal separator.
            if cleaned.rfind(",") > cleaned.rfind("."):

                # 1.234,56
                cleaned = cleaned.replace(
                    ".",
                    "",
                )

                cleaned = cleaned.replace(
                    ",",
                    ".",
                )

            else:

                # 1,234.56
                cleaned = cleaned.replace(
                    ",",
                    "",
                )

        # --------------------------------------------------------------
        # Comma only
        # --------------------------------------------------------------

        elif "," in cleaned:

            parts = cleaned.split(",")

            if (
                len(parts) > 1
                and len(parts[-1]) in (1, 2)
            ):

                # 1234,56
                cleaned = (
                    "".join(parts[:-1])
                    + "."
                    + parts[-1]
                )

            else:

                # 1,234
                cleaned = cleaned.replace(
                    ",",
                    "",
                )

        # --------------------------------------------------------------
        # Decimal conversion
        # --------------------------------------------------------------

        try:

            number = Decimal(cleaned)

            if negative:
                number = -number

            return number.quantize(
                Decimal("0.01")
            )

        except InvalidOperation:
            return None

    # ==================================================================
    # AMOUNT EXTRACTION
    # ==================================================================

    @classmethod
    def _extract_amount_after_label(
        cls,
        text: str,
        labels: list[str],
    ) -> Optional[Decimal]:
        """
        Extract financial value associated with a label.

        Supports:

            Subtotal: 1,000.00

        and:

            Subtotal | 1,000.00
        """

        for label in sorted(
            labels,
            key=len,
            reverse=True,
        ):

            label_pattern = cls._label_pattern(
                label
            )

            # ----------------------------------------------------------
            # Normal line format
            # ----------------------------------------------------------

            pattern = (
                label_pattern
                + r"\s*[:#\-]\s*"
                + r"([()\-\d][\d,.\s()]*)"
                + r"(?=\s*(?:\n|\||$))"
            )

            matches = re.findall(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            for raw in reversed(matches):

                value = cls._parse_decimal(
                    raw
                )

                if value is not None:
                    return value

            # ----------------------------------------------------------
            # Pipe/table format
            # ----------------------------------------------------------

            pattern = (
                label_pattern
                + r"\s*\|\s*"
                + r"([()\-\d][\d,.\s()]*)"
                + r"(?=\s*(?:\n|\||$))"
            )

            matches = re.findall(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            for raw in reversed(matches):

                value = cls._parse_decimal(
                    raw
                )

                if value is not None:
                    return value

            # ----------------------------------------------------------
            # Inline fallback
            # ----------------------------------------------------------

            pattern = (
                label_pattern
                + r"\s*[:#\-]?\s*"
                + r"([()\-\d][\d,.\s()]*)"
            )

            matches = re.findall(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            for raw in reversed(matches):

                value = cls._parse_decimal(
                    raw
                )

                if value is not None:
                    return value

            # Labels and values are often separated into adjacent PDF
            # text blocks without punctuation, for example TOTAL(USD)
            # followed by 91,580.50 on the next line.
            pattern = (
                label_pattern
                + r"\s*\n\s*"
                + r"([()\-\d][\d,.]*)"
                + r"(?=\s*(?:\n|\||$))"
            )

            matches = re.findall(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            for raw in reversed(matches):
                value = cls._parse_decimal(raw)
                if value is not None:
                    return value

        return None

    # ==================================================================
    # CURRENCY
    # ==================================================================

    @classmethod
    def _extract_currency(
        cls,
        text: str,
    ) -> Optional[str]:
        """
        Detect currency using:

            1. ISO code
            2. currency name
            3. currency symbol
        """

        if not text:
            return None

        # --------------------------------------------------------------
        # ISO codes
        # --------------------------------------------------------------

        for code in sorted(
            cls.CURRENCY_CODES,
        ):

            if re.search(
                rf"\b{re.escape(code)}\b",
                text,
                flags=re.IGNORECASE,
            ):
                return code

        # --------------------------------------------------------------
        # Currency names
        # --------------------------------------------------------------

        upper_text = text.upper()

        for name, code in sorted(
            cls.CURRENCY_NAMES.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):

            if name in upper_text:
                return code

        # --------------------------------------------------------------
        # Currency symbols
        # --------------------------------------------------------------

        for symbol, code in cls.CURRENCY_SYMBOLS.items():

            if symbol in text:
                return code

        return None

    # ==================================================================
    # SUPPLIER
    # ==================================================================

    @classmethod
    def _extract_supplier(
        cls,
        text: str,
    ) -> Optional[str]:
        """
        Extract supplier from an explicit supplier/vendor label.

        We do not assume the first line is the supplier.
        """

        value = cls._extract_after_label(
            text,
            cls.SUPPLIER_LABELS,
        )

        if not value:
            return None

        bad_values = {
            "invoice",
            "invoice number",
            "vendor",
            "supplier",
            "seller",
        }

        if value.lower().strip() in bad_values:
            return None

        return value.strip()

    # ==================================================================
    # PAYMENT TERMS
    # ==================================================================

    @classmethod
    def _extract_payment_terms(
        cls,
        text: str,
    ) -> Optional[str]:

        value = cls._extract_after_label(
            text,
            cls.PAYMENT_TERMS_LABELS,
        )

        if not value:
            return None

        return value.strip()

    # ==================================================================
    # GROSS AMOUNT
    # ==================================================================

    @classmethod
    def _extract_gross_amount(
        cls,
        text: str,
    ) -> Optional[Decimal]:
        """
        Extract gross invoice amount.

        Generic "Total" is deliberately excluded.

        Therefore:

            Total Payment: 8,161.92

        does not automatically become gross amount.

        While:

            Grand Total including VAT: 8,397.36

        does.
        """

        return cls._extract_amount_after_label(
            text,
            cls.GROSS_LABELS,
        )

    # ==================================================================
    # PUBLIC EXTRACT
    # ==================================================================

    def extract(
        self,
        text: str,
    ) -> InvoiceHeader:
        """
        Extract header-level invoice information.
        """

        text = self.normalize(text)

        if not text:
            return InvoiceHeader()

        # --------------------------------------------------------------
        # IDENTIFIERS
        # --------------------------------------------------------------

        invoice_number = self._extract_identifier(
            text,
            self.INVOICE_NUMBER_LABELS,
        )

        po_number = self._extract_identifier(
            text,
            self.PO_LABELS,
        )

        supplier_tax_id = self._extract_identifier(
            text,
            self.TAX_ID_LABELS,
        )

        # --------------------------------------------------------------
        # DATES
        # --------------------------------------------------------------

        invoice_date = self._extract_date(
            text,
            self.INVOICE_DATE_LABELS,
        )

        due_date = self._extract_date(
            text,
            self.DUE_DATE_LABELS,
        )

        # --------------------------------------------------------------
        # SUPPLIER
        # --------------------------------------------------------------

        supplier_name = self._extract_supplier(
            text
        )

        # --------------------------------------------------------------
        # PAYMENT TERMS
        # --------------------------------------------------------------

        payment_terms = self._extract_payment_terms(
            text
        )

        # --------------------------------------------------------------
        # CURRENCY
        # --------------------------------------------------------------

        currency = self._extract_currency(
            text
        )

        # --------------------------------------------------------------
        # FINANCIAL VALUES
        # --------------------------------------------------------------

        subtotal = self._extract_amount_after_label(
            text,
            self.SUBTOTAL_LABELS,
        )

        total_tax = self._extract_amount_after_label(
            text,
            self.TOTAL_TAX_LABELS,
        )

        total_discount = self._extract_amount_after_label(
            text,
            self.DISCOUNT_LABELS,
        )

        total_charges = self._extract_amount_after_label(
            text,
            self.CHARGE_LABELS,
        )

        gross_amount = self._extract_gross_amount(
            text
        )

        # --------------------------------------------------------------
        # COMPLETENESS CONFIDENCE
        # --------------------------------------------------------------

        # This measures how many core header fields were extracted.
        #
        # It is NOT:
        #
        #   OCR confidence
        #   Paddle confidence
        #   Qwen confidence
        #   financial confidence
        #

        core_values = [
            invoice_number,
            invoice_date,
            supplier_name,
            currency,
            gross_amount,
        ]

        confidence = (
            sum(
                value is not None
                for value in core_values
            )
            / len(core_values)
        )

        return InvoiceHeader(
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            due_date=due_date,
            supplier_name=supplier_name,
            supplier_tax_id=supplier_tax_id,
            currency=currency,
            po_number=po_number,
            payment_terms=payment_terms,
            subtotal=subtotal,
            total_tax=total_tax,
            total_discount=total_discount,
            total_charges=total_charges,
            gross_amount=gross_amount,
            confidence=round(
                confidence,
                4,
            ),
        )


# ======================================================================
# STANDALONE TESTS
# ======================================================================

if __name__ == "__main__":

    extractor = InvoiceExtractor()

    tests = {

        # --------------------------------------------------------------
        # STANDARD INVOICE
        # --------------------------------------------------------------

        "standard_invoice": """
            TAX INVOICE

            Invoice Number: INV-1001
            Invoice Date: 10/09/2026
            Due Date: 10/10/2026

            Supplier: ABC Technologies Ltd
            Supplier Tax ID: GSTIN123456

            Currency: USD
            PO Number: PO-1001
            Payment Terms: Net 30

            Subtotal: 1,000.00
            Total Tax: 180.00
            Total Discount: 50.00
            Total Charges: 20.00
            Grand Total: 1,150.00
        """,

        # --------------------------------------------------------------
        # HLD-STYLE DOCUMENT
        # --------------------------------------------------------------

        "invoice_with_payment_total": """
            TAX INVOICE

            Invoice Number: INV-2001
            Invoice Date: 2026-09-10
            Supplier: ABC Ltd
            Currency: THB

            Subtotal: 7,200.00
            Management Fee: 648.00
            VAT 7%: 549.36
            Grand Total including VAT: 8,397.36

            Withholding Tax: 235.44
            Total Payment: 8,161.92
        """,

        # --------------------------------------------------------------
        # HTML TABLE
        # --------------------------------------------------------------

        "html_table_invoice": """
            <table>

                <tr>
                    <td>Invoice Number</td>
                    <td>INV-3001</td>
                </tr>

                <tr>
                    <td>Invoice Date</td>
                    <td>05.05.2026</td>
                </tr>

                <tr>
                    <td>Supplier</td>
                    <td>XYZ Ltd</td>
                </tr>

                <tr>
                    <td>Currency</td>
                    <td>INR</td>
                </tr>

                <tr>
                    <td>Subtotal</td>
                    <td>10,000.00</td>
                </tr>

                <tr>
                    <td>Grand Total</td>
                    <td>11,800.00</td>
                </tr>

            </table>
        """,

        # --------------------------------------------------------------
        # EUROPEAN FORMAT
        # --------------------------------------------------------------

        "european_amount": """
            INVOICE

            Invoice No: EU-100
            Invoice Date: 10-09-2026
            Supplier: European Supplier
            Currency: EUR

            Subtotal: 1.234,56
            Total Tax: 234,56
            Grand Total: 1.469,12
        """,

        # --------------------------------------------------------------
        # OPTIONAL VALUES MISSING
        # --------------------------------------------------------------

        "missing_optional_fields": """
            TAX INVOICE

            Invoice Number: INV-4001
            Invoice Date: 2026-09-10
            Supplier: Simple Supplier
            Currency: USD

            Grand Total: 500.00
        """,

        # --------------------------------------------------------------
        # GENERIC TOTAL PROTECTION
        # --------------------------------------------------------------

        "no_generic_total": """
            INVOICE

            Invoice Number: INV-5001
            Invoice Date: 2026-09-10
            Supplier: ABC Ltd
            Currency: INR

            Subtotal: 1000
            Tax: 180
            Withholding Tax: 30
            Total Payment: 1150
        """,

        # --------------------------------------------------------------
        # INLINE VALUES
        # --------------------------------------------------------------

        "inline_values": """
            TAX INVOICE

            Invoice Number: INV-999
            Invoice Date: 2026-09-10
            Supplier: Test Supplier
            PO Number: PO-999
            Grand Total: 999.99
        """,

        # --------------------------------------------------------------
        # SAME LINE
        # --------------------------------------------------------------

        "same_line": """
            INVOICE
            Invoice Number: INV-123 Grand Total: 100.00
        """,

        # --------------------------------------------------------------
        # PIPE TABLE
        # --------------------------------------------------------------

        "pipe_table": """
            Invoice Number | INV-7001
            Invoice Date | 2026-09-10
            Supplier | Pipe Supplier
            Currency | USD
            Subtotal | 2,000.00
            Grand Total | 2,360.00
        """,

        # --------------------------------------------------------------
        # NOISE AROUND IDENTIFIER
        # --------------------------------------------------------------

        "identifier_with_noise": """
            TAX INVOICE
            Invoice Number: INV-8888
            Invoice Date: 2026-09-10
            Supplier: Noise Test Ltd
            PO Number: PO-8888
            Currency: USD

            Notes:
            Original invoice number should not be confused
            with random numbers 12345 67890.

            Grand Total: 1,250.00
        """,
    }

    print("=" * 80)
    print("INVOICE EXTRACTOR TESTS")
    print("=" * 80)

    for name, text in tests.items():

        result = extractor.extract(text)

        print(f"\nTEST: {name}")
        print("-" * 80)

        for key, value in result.to_dict().items():
            print(f"{key:20}: {value}")

    # ==================================================================
    # TARGETED ASSERTIONS
    # ==================================================================

    print("\n" + "=" * 80)
    print("TARGETED ASSERTIONS")
    print("=" * 80)

    # --------------------------------------------------------------
    # 1. Identifier must stop correctly.
    # --------------------------------------------------------------

    result = extractor.extract(
        "INVOICE Invoice Number: INV-123 Grand Total: 100.00"
    )

    assert result.invoice_number == "INV-123", (
        f"Wrong invoice number: "
        f"{result.invoice_number}"
    )

    assert result.gross_amount == Decimal(
        "100.00"
    ), (
        f"Wrong gross amount: "
        f"{result.gross_amount}"
    )

    print(
        "PASS: invoice identifier extraction"
    )

    # --------------------------------------------------------------
    # 2. HLD-style gross amount.
    # --------------------------------------------------------------

    result = extractor.extract(
        tests["invoice_with_payment_total"]
    )

    assert result.invoice_number == "INV-2001"

    assert result.gross_amount == Decimal(
        "8397.36"
    ), (
        f"Expected 8397.36, "
        f"got {result.gross_amount}"
    )

    print(
        "PASS: HLD gross amount extraction"
    )

    # --------------------------------------------------------------
    # 3. Payment amount must not replace gross.
    # --------------------------------------------------------------

    assert result.gross_amount != Decimal(
        "8161.92"
    )

    print(
        "PASS: withholding/payment protection"
    )

    # --------------------------------------------------------------
    # 4. Generic Total Payment must not become gross.
    # --------------------------------------------------------------

    result = extractor.extract(
        tests["no_generic_total"]
    )

    assert result.gross_amount is None, (
        f"Generic Total Payment incorrectly "
        f"became gross amount: "
        f"{result.gross_amount}"
    )

    print(
        "PASS: generic total protection"
    )

    # --------------------------------------------------------------
    # 5. PO extraction.
    # --------------------------------------------------------------

    result = extractor.extract(
        tests["inline_values"]
    )

    assert result.po_number == "PO-999"
    assert result.invoice_number == "INV-999"

    print(
        "PASS: PO extraction"
    )

    # --------------------------------------------------------------
    # 6. HTML table extraction.
    # --------------------------------------------------------------

    result = extractor.extract(
        tests["html_table_invoice"]
    )

    assert result.invoice_number == "INV-3001", (
        f"HTML invoice number failed: "
        f"{result.invoice_number}"
    )

    assert result.invoice_date == "05.05.2026", (
        f"HTML invoice date failed: "
        f"{result.invoice_date}"
    )

    assert result.supplier_name == "XYZ Ltd", (
        f"HTML supplier failed: "
        f"{result.supplier_name}"
    )

    assert result.currency == "INR", (
        f"HTML currency failed: "
        f"{result.currency}"
    )

    assert result.subtotal == Decimal(
        "10000.00"
    ), (
        f"HTML subtotal failed: "
        f"{result.subtotal}"
    )

    assert result.gross_amount == Decimal(
        "11800.00"
    ), (
        f"HTML gross failed: "
        f"{result.gross_amount}"
    )

    print(
        "PASS: HTML table extraction"
    )

    # --------------------------------------------------------------
    # 7. European amount format.
    # --------------------------------------------------------------

    result = extractor.extract(
        tests["european_amount"]
    )

    assert result.subtotal == Decimal(
        "1234.56"
    )

    assert result.total_tax == Decimal(
        "234.56"
    )

    assert result.gross_amount == Decimal(
        "1469.12"
    )

    print(
        "PASS: European number format"
    )

    # --------------------------------------------------------------
    # 8. Missing optional values remain None.
    # --------------------------------------------------------------

    result = extractor.extract(
        tests["missing_optional_fields"]
    )

    assert result.invoice_number == "INV-4001"
    assert result.due_date is None
    assert result.po_number is None
    assert result.payment_terms is None
    assert result.gross_amount == Decimal(
        "500.00"
    )

    print(
        "PASS: missing optional fields"
    )

    # --------------------------------------------------------------
    # 9. Same-line extraction.
    # --------------------------------------------------------------

    result = extractor.extract(
        tests["same_line"]
    )

    assert result.invoice_number == "INV-123"
    assert result.gross_amount == Decimal(
        "100.00"
    )

    print(
        "PASS: same-line extraction"
    )

    # --------------------------------------------------------------
    # 10. Pipe-table extraction.
    # --------------------------------------------------------------

    result = extractor.extract(
        tests["pipe_table"]
    )

    assert result.invoice_number == "INV-7001"
    assert result.invoice_date == "2026-09-10"
    assert result.supplier_name == "Pipe Supplier"
    assert result.currency == "USD"
    assert result.subtotal == Decimal(
        "2000.00"
    )
    assert result.gross_amount == Decimal(
        "2360.00"
    )

    print(
        "PASS: pipe-table extraction"
    )

    # --------------------------------------------------------------
    # 11. Identifier noise protection.
    # --------------------------------------------------------------

    result = extractor.extract(
        tests["identifier_with_noise"]
    )

    assert result.invoice_number == "INV-8888"
    assert result.po_number == "PO-8888"
    assert result.gross_amount == Decimal(
        "1250.00"
    )

    print(
        "PASS: identifier noise protection"
    )

    # ==================================================================
    # FINAL
    # ==================================================================

    print("\n" + "=" * 80)
    print("ALL INVOICE EXTRACTOR TESTS PASSED")
    print("=" * 80)