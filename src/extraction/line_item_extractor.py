# src/extraction/line_item_extractor.py

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
import re
from typing import Any, Dict, List, Optional


# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class LineItem:
    description: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    discount: Optional[Decimal] = None

    raw_text: Optional[str] = None
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)

        for key, value in result.items():
            if isinstance(value, Decimal):
                result[key] = str(value)

        return result


# =============================================================================
# HTML TABLE PARSER
# =============================================================================

class _HTMLTableParser(HTMLParser):
    """
    Lightweight dependency-free HTML table parser.

    Extracts:

        <tr>
            <td>Product A</td>
            <td>2</td>
            <td>100.00</td>
            <td>200.00</td>
        </tr>

    into:

        [
            "Product A",
            "2",
            "100.00",
            "200.00"
        ]
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)

        self.rows: List[List[str]] = []

        self._inside_row = False
        self._inside_cell = False

        self._current_row: List[str] = []
        self._current_cell: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()

        if tag == "tr":

            if self._inside_row and self._current_row:
                self.rows.append(
                    self._current_row
                )

            self._inside_row = True
            self._current_row = []

        elif tag in {"td", "th"}:

            if not self._inside_row:
                return

            self._inside_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag in {"td", "th"}:

            if (
                self._inside_cell
                and self._inside_row
            ):

                value = "".join(
                    self._current_cell
                )

                value = unescape(value)

                value = re.sub(
                    r"\s+",
                    " ",
                    value,
                ).strip()

                self._current_row.append(
                    value
                )

            self._inside_cell = False
            self._current_cell = []

        elif tag == "tr":

            if (
                self._inside_row
                and self._current_row
            ):
                self.rows.append(
                    self._current_row
                )

            self._inside_row = False
            self._inside_cell = False

            self._current_row = []
            self._current_cell = []

    def handle_data(self, data: str) -> None:

        if self._inside_cell:
            self._current_cell.append(data)

    def close(self) -> None:

        super().close()

        # Handle malformed HTML without </tr>.
        if self._inside_row:

            if self._inside_cell:

                value = "".join(
                    self._current_cell
                )

                value = unescape(value)

                value = re.sub(
                    r"\s+",
                    " ",
                    value,
                ).strip()

                self._current_row.append(
                    value
                )

            if self._current_row:
                self.rows.append(
                    self._current_row
                )

        self._inside_row = False
        self._inside_cell = False


# =============================================================================
# LINE ITEM EXTRACTOR
# =============================================================================

class LineItemExtractor:

    # =========================================================================
    # HEADER TERMS
    # =========================================================================

    HEADER_TERMS = {
        "description",
        "item",
        "product",
        "service",
        "quantity",
        "qty",
        "unit price",
        "price",
        "rate",
        "amount",
        "total",
    }

    # =========================================================================
    # STOP TERMS
    # =========================================================================

    STOP_TERMS = [
        "subtotal",
        "sub total",
        "total tax",
        "tax total",
        "grand total",
        "amount due",
        "balance due",
        "payment terms",
        "notes",
        "terms and conditions",
    ]

    # =========================================================================
    # NON-LINE-ITEM FINANCIAL TERMS
    # =========================================================================
    #
    # These are deliberately separate from STOP_TERMS.
    #
    # STOP_TERMS:
    #     Stop parsing the line-item section.
    #
    # NON_LINE_ITEM_PATTERNS:
    #     Skip this particular row but continue looking for valid items.
    #
    # This is important for documents such as:
    #
    #   Item A
    #   Management Fee
    #   Item B
    #   VAT
    #
    # We do not want Management Fee/VAT to become line items.
    # =========================================================================

    NON_LINE_ITEM_PATTERNS = [
        "management fee",
        "agency fee",
        "service fee",
        "handling fee",
        "processing fee",
        "administration fee",
        "administrative fee",
        "convenience fee",
        "delivery charge",
        "delivery fee",
        "shipping",
        "freight",
        "insurance",
        "withholding tax",
        "withholding",
        "vat",
        "gst",
        "sales tax",
        "tax",
        "discount",
        "surcharge",
        "excise duty",
        "excise",
        "total payment",
        "payment amount",
        "amount payable",
        "net payable",
    ]

    # =========================================================================
    # CONSTRUCTOR
    # =========================================================================

    def __init__(self) -> None:
        pass

    # =========================================================================
    # NUMBER PARSING
    # =========================================================================

    @staticmethod
    def _parse_number(
        value: str,
    ) -> Optional[Decimal]:
        """
        Parse financial numbers.

        Supported:

            1000
            1000.00
            1,000.00
            1.000,00
            500,00
            ₹1,200.00
            -100.00
            (100.00)
        """

        if not value:
            return None

        value = value.strip()

        if not value:
            return None

        negative = (
            value.startswith("-")
            or (
                value.startswith("(")
                and value.endswith(")")
            )
        )

        # Remove currency symbols / letters.
        value = re.sub(
            r"[^\d.,]",
            "",
            value,
        )

        if not value:
            return None

        # ---------------------------------------------------------------------
        # Both comma and dot
        # ---------------------------------------------------------------------

        if "," in value and "." in value:

            if value.rfind(",") > value.rfind("."):

                # European:
                # 1.234,56 -> 1234.56

                value = value.replace(
                    ".",
                    "",
                )

                value = value.replace(
                    ",",
                    ".",
                )

            else:

                # US:
                # 1,234.56 -> 1234.56

                value = value.replace(
                    ",",
                    "",
                )

        # ---------------------------------------------------------------------
        # Comma only
        # ---------------------------------------------------------------------

        elif "," in value:

            parts = value.split(",")

            if len(parts[-1]) == 2:

                # 500,00 -> 500.00

                value = (
                    "".join(parts[:-1])
                    + "."
                    + parts[-1]
                )

            else:

                # 1,000 -> 1000

                value = value.replace(
                    ",",
                    "",
                )

        # ---------------------------------------------------------------------
        # Decimal conversion
        # ---------------------------------------------------------------------

        try:

            number = Decimal(value)

            if negative:
                number = -number

            return number

        except InvalidOperation:

            return None

    # =========================================================================
    # STRICT NUMERIC CELL
    # =========================================================================

    def _parse_numeric_cell(
        self,
        value: str,
    ) -> Optional[Decimal]:
        """
        Parse a cell only when the entire cell is numeric.

        Prevents:

            Staff 2 Units X 6 Days
            Model 2025 Laptop
            Service Plan 24-Month

        from being treated as numeric cells.
        """

        if not value:
            return None

        value = value.strip()

        if not value:
            return None

        numeric_pattern = re.compile(
            r"""
            ^\s*
            [\$€£₹¥]?
            \s*
            -?
            \s*
            \(?
            \d[\d\s.,]*
            \)?
            \s*
            [\$€£₹¥]?
            \s*
            $
            """,
            re.VERBOSE,
        )

        if not numeric_pattern.match(value):
            return None

        return self._parse_number(value)

    # =========================================================================
    # GENERAL NUMBER EXTRACTION
    # =========================================================================

    @staticmethod
    def _numbers(
        text: str,
    ) -> List[Decimal]:

        matches = re.findall(
            r"(?<!\w)"
            r"-?\(?\d[\d,.]*\)?"
            r"(?!\w)",
            text,
        )

        numbers: List[Decimal] = []

        for match in matches:

            number = LineItemExtractor._parse_number(
                match
            )

            if number is not None:
                numbers.append(number)

        return numbers

    # =========================================================================
    # HTML DETECTION
    # =========================================================================

    @staticmethod
    def _is_html_table(
        text: str,
    ) -> bool:

        if not text:
            return False

        lowered = text.lower()

        return (
            "<table" in lowered
            and "<tr" in lowered
            and (
                "<td" in lowered
                or "<th" in lowered
            )
        )

    # =========================================================================
    # STOP LINE
    # =========================================================================

    def _is_stop_line(
        self,
        line: str,
    ) -> bool:

        normalized = line.lower().strip()

        return any(
            term in normalized
            for term in self.STOP_TERMS
        )

    # =========================================================================
    # NON-LINE-ITEM DETECTION
    # =========================================================================

    def _is_non_line_item(
        self,
        line: str,
    ) -> bool:
        """
        Detect explicit tax, fee, charge, discount and payment rows.

        Important:
            Numeric descriptions are NOT rejected.

        Examples that remain valid:

            Staff 2 Units X 6 Days
            Model 2025 Laptop
            Server Gen 4
            Product ABC-2025
            Service Plan 24-Month
        """

        normalized = re.sub(
            r"\s+",
            " ",
            line.lower(),
        ).strip(
            " |-:\t"
        )

        if not normalized:
            return True

        # Summary rows.
        if self._is_stop_line(
            normalized
        ):
            return True

        for pattern in self.NON_LINE_ITEM_PATTERNS:

            if re.search(
                rf"(?<![a-z])"
                rf"{re.escape(pattern)}"
                rf"(?![a-z])",
                normalized,
            ):
                return True

        return False

    # =========================================================================
    # HTML TABLE EXTRACTION
    # =========================================================================

    def _extract_html_table(
        self,
        text: str,
    ) -> List[LineItem]:

        if not self._is_html_table(text):
            return []

        parser = _HTMLTableParser()

        try:

            parser.feed(text)
            parser.close()

        except Exception:

            return []

        rows = parser.rows

        if not rows:
            return []

        # ---------------------------------------------------------------------
        # Locate header row
        # ---------------------------------------------------------------------

        header_index: Optional[int] = None

        for index, row in enumerate(rows):

            normalized_cells = [
                re.sub(
                    r"\s+",
                    " ",
                    cell.lower(),
                ).strip()
                for cell in row
            ]

            joined = " | ".join(
                normalized_cells
            )

            header_hits = 0

            if any(
                term in joined
                for term in (
                    "description",
                    "item",
                    "product",
                    "service",
                )
            ):
                header_hits += 1

            if any(
                term in joined
                for term in (
                    "quantity",
                    "qty",
                )
            ):
                header_hits += 1

            if any(
                term in joined
                for term in (
                    "unit price",
                    "price",
                    "rate",
                )
            ):
                header_hits += 1

            if any(
                term in joined
                for term in (
                    "amount",
                    "line total",
                    "total",
                )
            ):
                header_hits += 1

            if header_hits >= 2:

                header_index = index

                break

        if header_index is None:
            return []

        header = [
            re.sub(
                r"\s+",
                " ",
                cell.lower(),
            ).strip()
            for cell in rows[header_index]
        ]

        # ---------------------------------------------------------------------
        # Identify columns
        # ---------------------------------------------------------------------

        description_index: Optional[int] = None
        quantity_index: Optional[int] = None
        unit_price_index: Optional[int] = None
        amount_index: Optional[int] = None

        for index, cell in enumerate(header):

            if (
                description_index is None
                and any(
                    term in cell
                    for term in (
                        "description",
                        "item",
                        "product",
                        "service",
                    )
                )
            ):
                description_index = index

            if (
                quantity_index is None
                and cell in {
                    "quantity",
                    "qty",
                }
            ):
                quantity_index = index

            if (
                unit_price_index is None
                and any(
                    term in cell
                    for term in (
                        "unit price",
                        "price",
                        "rate",
                    )
                )
            ):
                unit_price_index = index

            if (
                amount_index is None
                and any(
                    term in cell
                    for term in (
                        "amount",
                        "line total",
                        "total",
                    )
                )
            ):
                amount_index = index

        if description_index is None:
            description_index = 0

        results: List[LineItem] = []

        # ---------------------------------------------------------------------
        # Parse rows
        # ---------------------------------------------------------------------

        for row in rows[
            header_index + 1:
        ]:

            if not row:
                continue

            cells = [
                re.sub(
                    r"\s+",
                    " ",
                    str(cell),
                ).strip()
                for cell in row
            ]

            if not cells:
                continue

            joined = " | ".join(cells)

            if not joined.strip():
                continue

            # Summary row -> stop.
            if self._is_stop_line(
                joined
            ):
                break

            # Tax/fee/charge rows -> skip.
            if self._is_non_line_item(
                joined
            ):
                continue

            if len(cells) < 2:
                continue

            description: Optional[str] = None
            quantity: Optional[Decimal] = None
            unit_price: Optional[Decimal] = None
            amount: Optional[Decimal] = None

            # -----------------------------------------------------------------
            # Description
            # -----------------------------------------------------------------

            if description_index < len(cells):

                description = cells[
                    description_index
                ].strip()

                if not description:
                    description = None

            # -----------------------------------------------------------------
            # Quantity
            # -----------------------------------------------------------------

            if (
                quantity_index is not None
                and quantity_index < len(cells)
            ):

                quantity = (
                    self._parse_numeric_cell(
                        cells[quantity_index]
                    )
                )

            # -----------------------------------------------------------------
            # Unit price
            # -----------------------------------------------------------------

            if (
                unit_price_index is not None
                and unit_price_index < len(cells)
            ):

                unit_price = (
                    self._parse_numeric_cell(
                        cells[unit_price_index]
                    )
                )

            # -----------------------------------------------------------------
            # Amount
            # -----------------------------------------------------------------

            if (
                amount_index is not None
                and amount_index < len(cells)
            ):

                amount = (
                    self._parse_numeric_cell(
                        cells[amount_index]
                    )
                )

            # -----------------------------------------------------------------
            # Numeric fallback
            # -----------------------------------------------------------------

            numeric_cells = []

            for cell_index, cell in enumerate(
                cells
            ):

                number = (
                    self._parse_numeric_cell(
                        cell
                    )
                )

                if number is not None:

                    numeric_cells.append(
                        (
                            cell_index,
                            number,
                        )
                    )

            if len(numeric_cells) >= 3:

                if quantity is None:
                    quantity = numeric_cells[-3][1]

                if unit_price is None:
                    unit_price = numeric_cells[-2][1]

                if amount is None:
                    amount = numeric_cells[-1][1]

            elif len(numeric_cells) == 2:

                if quantity is None:
                    quantity = numeric_cells[-2][1]

                if amount is None:
                    amount = numeric_cells[-1][1]

            # -----------------------------------------------------------------
            # Validation
            # -----------------------------------------------------------------

            if description is None:
                continue

            if (
                quantity is None
                and unit_price is None
                and amount is None
            ):
                continue

            # -----------------------------------------------------------------
            # Extra summary protection
            # -----------------------------------------------------------------

            description_lower = (
                description.lower()
            )

            if any(
                term in description_lower
                for term in (
                    "subtotal",
                    "grand total",
                    "amount due",
                    "balance due",
                    "total tax",
                    "tax total",
                    "vat",
                    "gst",
                    "withholding",
                    "management fee",
                    "agency fee",
                )
            ):
                continue

            # -----------------------------------------------------------------
            # Confidence
            # -----------------------------------------------------------------

            confidence = 0.40

            if description:
                confidence += 0.20

            if quantity is not None:
                confidence += 0.15

            if unit_price is not None:
                confidence += 0.10

            if amount is not None:
                confidence += 0.15

            confidence = round(
                min(
                    confidence,
                    0.99,
                ),
                2,
            )

            results.append(
                LineItem(
                    description=description,
                    quantity=quantity,
                    unit_price=unit_price,
                    amount=amount,
                    raw_text=joined,
                    confidence=confidence,
                )
            )

        return results

    # =========================================================================
    # TEXT TABLE HEADER DETECTION
    # =========================================================================

    def _find_table_start(
        self,
        lines: List[str],
    ) -> Optional[int]:

        for index, line in enumerate(
            lines
        ):

            normalized = line.lower()

            header_hits = sum(
                term in normalized
                for term in self.HEADER_TERMS
            )

            if header_hits >= 2:
                return index

        return None

    # =========================================================================
    # LINE DETECTION
    # =========================================================================

    def _looks_like_line(
        self,
        line: str,
    ) -> bool:

        numbers = self._numbers(line)

        return len(numbers) >= 2

    # =========================================================================
    # TEXT LINE PARSER
    # =========================================================================

    def _parse_line(
        self,
        line: str,
    ) -> Optional[LineItem]:

        # =====================================================================
        # PIPE-SEPARATED TABLE
        # =====================================================================

        if "|" in line:

            cells = [
                cell.strip()
                for cell in line.split("|")
                if cell.strip()
            ]

            if len(cells) >= 3:

                description = cells[0]

                numeric_cells = []

                for cell_index, cell in enumerate(
                    cells[1:],
                    start=1,
                ):

                    number = (
                        self._parse_numeric_cell(
                            cell
                        )
                    )

                    if number is not None:

                        numeric_cells.append(
                            (
                                cell_index,
                                number,
                            )
                        )

                # -------------------------------------------------------------
                # Three numeric cells:
                #
                # quantity | unit price | amount
                # -------------------------------------------------------------

                if len(numeric_cells) >= 3:

                    quantity = (
                        numeric_cells[-3][1]
                    )

                    unit_price = (
                        numeric_cells[-2][1]
                    )

                    amount = (
                        numeric_cells[-1][1]
                    )

                # -------------------------------------------------------------
                # Two numeric cells:
                #
                # quantity | amount
                #
                # Do not invent unit price.
                # -------------------------------------------------------------

                elif len(numeric_cells) == 2:

                    quantity = (
                        numeric_cells[-2][1]
                    )

                    unit_price = None

                    amount = (
                        numeric_cells[-1][1]
                    )

                else:

                    return None

                if quantity <= 0:
                    quantity = None

                if (
                    unit_price is not None
                    and unit_price < 0
                ):
                    unit_price = None

                confidence = 0.40

                if description:
                    confidence += 0.20

                if quantity is not None:
                    confidence += 0.15

                if unit_price is not None:
                    confidence += 0.10

                if amount is not None:
                    confidence += 0.15

                confidence = round(
                    min(
                        confidence,
                        0.99,
                    ),
                    2,
                )

                return LineItem(
                    description=description,
                    quantity=quantity,
                    unit_price=unit_price,
                    amount=amount,
                    raw_text=line,
                    confidence=confidence,
                )

        # =====================================================================
        # NORMAL OCR TEXT
        # =====================================================================

        numbers = self._numbers(line)

        if len(numbers) < 2:
            return None

        amount = numbers[-1]

        # ---------------------------------------------------------------------
        # Three or more numbers
        # ---------------------------------------------------------------------

        if len(numbers) >= 3:

            quantity = numbers[-3]
            unit_price = numbers[-2]

            if quantity <= 0:
                quantity = None

            if unit_price < 0:
                unit_price = None

        # ---------------------------------------------------------------------
        # Two numbers
        # ---------------------------------------------------------------------

        else:

            quantity = None
            unit_price = numbers[-2]

        # ---------------------------------------------------------------------
        # Remove numbers from description
        # ---------------------------------------------------------------------

        description = re.sub(
            r"-?\(?\d[\d,.]*\)?",
            " ",
            line,
        )

        description = re.sub(
            r"\s{2,}",
            " ",
            description,
        ).strip(
            " |-:\t"
        )

        if not description:
            description = None

        # ---------------------------------------------------------------------
        # Confidence
        # ---------------------------------------------------------------------

        confidence = 0.40

        if description:
            confidence += 0.20

        if quantity is not None:
            confidence += 0.15

        if unit_price is not None:
            confidence += 0.10

        if amount is not None:
            confidence += 0.15

        confidence = round(
            min(
                confidence,
                0.99,
            ),
            2,
        )

        return LineItem(
            description=description,
            quantity=quantity,
            unit_price=unit_price,
            amount=amount,
            raw_text=line,
            confidence=confidence,
        )

    # =========================================================================
    # MAIN EXTRACTION
    # =========================================================================

    def extract(
        self,
        text: str,
    ) -> List[LineItem]:

        if not text:
            return []

        # =====================================================================
        # HTML PATH
        # =====================================================================

        if self._is_html_table(text):

            html_results = (
                self._extract_html_table(
                    text
                )
            )

            if html_results:
                return html_results

        # =====================================================================
        # NORMAL OCR / TEXT PATH
        # =====================================================================

        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
        ]

        if not lines:
            return []

        # ---------------------------------------------------------------------
        # Locate table header
        # ---------------------------------------------------------------------

        table_start = (
            self._find_table_start(
                lines
            )
        )

        if table_start is None:

            candidate_lines = lines

        else:

            candidate_lines = lines[
                table_start + 1:
            ]

        results: List[LineItem] = []

        # ---------------------------------------------------------------------
        # Parse candidate rows
        # ---------------------------------------------------------------------

        for line in candidate_lines:

            # Summary section begins.
            if self._is_stop_line(line):
                break

            # Tax / fee / charge / discount / payment row.
            if self._is_non_line_item(line):
                continue

            if not self._looks_like_line(line):
                continue

            item = self._parse_line(line)

            if item is not None:
                results.append(item)

        return results


# =============================================================================
# TEST HELPER
# =============================================================================

def _print_results(
    name: str,
    results: List[LineItem],
) -> None:

    print()
    print(f"TEST: {name}")
    print("-" * 80)

    if not results:

        print("No line items found.")

        return

    for index, item in enumerate(
        results,
        start=1,
    ):

        print(
            f"ITEM {index}: "
            f"{item.to_dict()}"
        )


# =============================================================================
# TEST SUITE
# =============================================================================

if __name__ == "__main__":

    print("=" * 80)
    print("LINE ITEM EXTRACTOR TESTS")
    print("=" * 80)

    extractor = LineItemExtractor()

    # =========================================================================
    # 1. STANDARD TABLE
    # =========================================================================

    standard_table = """
    Description | Quantity | Unit Price | Amount
    Laptop | 2 | 500.00 | 1000.00
    Mouse | 5 | 20.00 | 100.00
    Subtotal | 1100.00
    VAT 18% | 198.00
    Grand Total | 1298.00
    """

    result = extractor.extract(
        standard_table
    )

    _print_results(
        "standard_table",
        result,
    )

    # =========================================================================
    # 2. HLD OCR
    # =========================================================================

    hld_ocr = """
    Invoice No: S16675/02/467
    Date: 05.05.2569

    Description | Quantity | Unit Price
    Staff 2 Units X 6 Days | 12 | 600.00

    Total | 7200.00
    Management Fee 9% | 648.00
    Total including agency fee | 7848.00
    VAT 7% | 549.36
    Grand Total including VAT | 8397.36
    Withholding tax | 235.44
    Total payment | 8161.92
    """

    result = extractor.extract(
        hld_ocr
    )

    _print_results(
        "hld_ocr",
        result,
    )

    # =========================================================================
    # 3. NORMAL OCR
    # =========================================================================

    normal_ocr = """
    Description Quantity Unit Price Amount
    Office Chair 3 150.00 450.00
    Desk 2 500.00 1000.00
    Subtotal 1450.00
    VAT 18% 261.00
    Grand Total 1711.00
    """

    result = extractor.extract(
        normal_ocr
    )

    _print_results(
        "normal_ocr",
        result,
    )

    # =========================================================================
    # 4. EUROPEAN FORMAT
    # =========================================================================

    european = """
    Description | Quantity | Unit Price | Amount
    Product A | 2 | 1.234,56 | 2.469,12
    Product B | 1 | 500,00 | 500,00
    Subtotal | 2.969,12
    VAT | 593,82
    Grand Total | 3.562,94
    """

    result = extractor.extract(
        european
    )

    _print_results(
        "european",
        result,
    )

    # =========================================================================
    # 5. HTML TABLE
    # =========================================================================

    html_table = """
    <table>
        <thead>
            <tr>
                <th>Description</th>
                <th>Quantity</th>
                <th>Unit Price</th>
                <th>Amount</th>
            </tr>
        </thead>

        <tbody>

            <tr>
                <td>Product A</td>
                <td>2</td>
                <td>100.00</td>
                <td>200.00</td>
            </tr>

            <tr>
                <td>Product B</td>
                <td>3</td>
                <td>50.00</td>
                <td>150.00</td>
            </tr>

            <tr>
                <td>Subtotal</td>
                <td>350.00</td>
            </tr>

            <tr>
                <td>VAT 10%</td>
                <td>35.00</td>
            </tr>

            <tr>
                <td>Grand Total</td>
                <td>385.00</td>
            </tr>

        </tbody>
    </table>
    """

    result = extractor.extract(
        html_table
    )

    _print_results(
        "html_table",
        result,
    )

    # =========================================================================
    # 6. SUMMARY ROWS
    # =========================================================================

    summary_rows = """
    Description | Quantity | Unit Price | Amount
    Consulting Service | 2 | 500.00 | 1000.00
    Subtotal | 1000.00
    Discount | -100.00
    VAT 18% | 162.00
    Grand Total | 1062.00
    """

    result = extractor.extract(
        summary_rows
    )

    _print_results(
        "summary_rows",
        result,
    )

    # =========================================================================
    # 7. MULTIPLE ITEMS
    # =========================================================================

    multiple_items = """
    Item | Qty | Price | Amount
    Item A | 10 | 25.00 | 250.00
    Item B | 5 | 100.00 | 500.00
    Item C | 1 | 750.00 | 750.00
    Subtotal | 1500.00
    """

    result = extractor.extract(
        multiple_items
    )

    _print_results(
        "multiple_items",
        result,
    )

    # =========================================================================
    # 8. NUMBERS IN DESCRIPTION
    # =========================================================================

    numbers_in_description = """
    Description | Quantity | Unit Price | Amount
    Staff 2 Units X 6 Days | 12 | 600.00 | 7200.00
    Model 2025 Laptop | 2 | 500.00 | 1000.00
    Server Gen 4 | 1 | 2500.00 | 2500.00
    Subtotal | 10700.00
    """

    result = extractor.extract(
        numbers_in_description
    )

    _print_results(
        "numbers_in_description",
        result,
    )

    # =========================================================================
    # 9. PRODUCT CODE
    # =========================================================================

    product_code = """
    Description | Quantity | Unit Price | Amount
    Product ABC-2025 | 2 | 100.00 | 200.00
    Service Plan 24-Month | 1 | 300.00 | 300.00
    Subtotal | 500.00
    """

    result = extractor.extract(
        product_code
    )

    _print_results(
        "product_code",
        result,
    )

    # =========================================================================
    # TARGETED ASSERTIONS
    # =========================================================================

    print()
    print("=" * 80)
    print("TARGETED ASSERTIONS")
    print("=" * 80)

    # =========================================================================
    # STANDARD
    # =========================================================================

    result = extractor.extract(
        standard_table
    )

    assert len(result) == 2, (
        f"Expected 2 standard items, "
        f"got {len(result)}"
    )

    assert result[0].description == "Laptop"
    assert result[0].quantity == Decimal("2")
    assert result[0].unit_price == Decimal("500.00")
    assert result[0].amount == Decimal("1000.00")

    print(
        "PASS: standard table extraction"
    )

    # =========================================================================
    # HLD
    # =========================================================================

    result = extractor.extract(
        hld_ocr
    )

    assert len(result) == 1, (
        f"Expected 1 HLD line item, "
        f"got {len(result)}: {result}"
    )

    assert (
        result[0].description
        == "Staff 2 Units X 6 Days"
    )

    assert result[0].quantity == Decimal(
        "12"
    )

    # Do not invent the missing unit price.
    assert result[0].unit_price is None

    assert result[0].amount == Decimal(
        "600.00"
    )

    print(
        "PASS: HLD OCR line extraction"
    )

    # =========================================================================
    # HLD TAX / CHARGE PROTECTION
    # =========================================================================

    assert all(
        "Management Fee" not in (
            item.description or ""
        )
        for item in result
    )

    assert all(
        "VAT" not in (
            item.description or ""
        )
        for item in result
    )

    assert all(
        "Withholding" not in (
            item.description or ""
        )
        for item in result
    )

    print(
        "PASS: HLD tax/charge protection"
    )

    # =========================================================================
    # NORMAL OCR
    # =========================================================================

    result = extractor.extract(
        normal_ocr
    )

    assert len(result) == 2, (
        f"Expected 2 OCR items, "
        f"got {len(result)}"
    )

    assert result[0].description == (
        "Office Chair"
    )

    assert result[0].quantity == Decimal(
        "3"
    )

    assert result[0].unit_price == Decimal(
        "150.00"
    )

    assert result[0].amount == Decimal(
        "450.00"
    )

    print(
        "PASS: normal OCR extraction"
    )

    # =========================================================================
    # EUROPEAN
    # =========================================================================

    result = extractor.extract(
        european
    )

    assert len(result) == 2, (
        f"Expected 2 European items, "
        f"got {len(result)}"
    )

    assert result[0].unit_price == Decimal(
        "1234.56"
    )

    assert result[0].amount == Decimal(
        "2469.12"
    )

    assert result[1].unit_price == Decimal(
        "500.00"
    )

    print(
        "PASS: European number format"
    )

    # =========================================================================
    # HTML
    # =========================================================================

    result = extractor.extract(
        html_table
    )

    assert len(result) == 2, (
        f"Expected 2 HTML line items, "
        f"got {len(result)}: {result}"
    )

    assert result[0].description == (
        "Product A"
    )

    assert result[0].quantity == Decimal(
        "2"
    )

    assert result[0].unit_price == Decimal(
        "100.00"
    )

    assert result[0].amount == Decimal(
        "200.00"
    )

    assert result[1].description == (
        "Product B"
    )

    assert result[1].quantity == Decimal(
        "3"
    )

    assert result[1].unit_price == Decimal(
        "50.00"
    )

    assert result[1].amount == Decimal(
        "150.00"
    )

    print(
        "PASS: HTML table extraction"
    )

    # =========================================================================
    # SUMMARY ROWS
    # =========================================================================

    result = extractor.extract(
        summary_rows
    )

    assert len(result) == 1, (
        f"Expected 1 summary item, "
        f"got {len(result)}"
    )

    assert result[0].description == (
        "Consulting Service"
    )

    print(
        "PASS: summary row protection"
    )

    # =========================================================================
    # MULTIPLE ITEMS
    # =========================================================================

    result = extractor.extract(
        multiple_items
    )

    assert len(result) == 3, (
        f"Expected 3 multiple items, "
        f"got {len(result)}"
    )

    print(
        "PASS: multiple line items"
    )

    # =========================================================================
    # NUMBERS IN DESCRIPTION
    # =========================================================================

    result = extractor.extract(
        numbers_in_description
    )

    assert len(result) == 3, (
        f"Expected 3 numeric-description "
        f"items, got {len(result)}"
    )

    assert result[0].description == (
        "Staff 2 Units X 6 Days"
    )

    assert result[0].quantity == Decimal(
        "12"
    )

    assert result[0].unit_price == Decimal(
        "600.00"
    )

    assert result[0].amount == Decimal(
        "7200.00"
    )

    assert result[1].description == (
        "Model 2025 Laptop"
    )

    assert result[2].description == (
        "Server Gen 4"
    )

    print(
        "PASS: numbers in description"
    )

    # =========================================================================
    # PRODUCT CODE
    # =========================================================================

    result = extractor.extract(
        product_code
    )

    assert len(result) == 2, (
        f"Expected 2 product-code items, "
        f"got {len(result)}"
    )

    assert result[0].description == (
        "Product ABC-2025"
    )

    assert result[1].description == (
        "Service Plan 24-Month"
    )

    print(
        "PASS: product code handling"
    )

    # =========================================================================
    # FINAL
    # =========================================================================

    print()
    print("=" * 80)
    print("ALL LINE ITEM EXTRACTOR TESTS PASSED")
    print("=" * 80)