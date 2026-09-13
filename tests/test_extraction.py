from decimal import Decimal

from src.extraction.invoice_extractor import (
    InvoiceExtractor,
)

from src.extraction.line_item_extractor import (
    LineItemExtractor,
)

from src.extraction.tax_extractor import (
    TaxExtractor,
)

from src.extraction.discount_extractor import (
    DiscountExtractor,
)

from src.extraction.charge_extractor import (
    ChargeExtractor,
)


INVOICE_TEXT = """
TAX INVOICE

Invoice Number: INV-2026-001
Invoice Date: 01/09/2026
Due Date: 01/10/2026

Supplier: ABC Technologies Pvt Ltd
Supplier Tax ID: GST123456

PO Number: PO-5001

Currency: INR

Item Description       Qty    Unit Price
Laptop                 2      50000.00
Mouse                  2      1000.00

Subtotal: 102000.00
Discount: 2000.00
GST 18%: 18000.00
Shipping Charges: 500.00

Gross Amount: 118500.00
"""


def test_invoice_number_extraction():
    extractor = InvoiceExtractor()

    result = extractor.extract(
        INVOICE_TEXT
    )

    assert result.invoice_number == "INV-2026-001"


def test_supplier_extraction():
    extractor = InvoiceExtractor()

    result = extractor.extract(
        INVOICE_TEXT
    )

    assert (
        result.supplier_name
        == "ABC Technologies Pvt Ltd"
    )


def test_po_number_extraction():
    extractor = InvoiceExtractor()

    result = extractor.extract(
        INVOICE_TEXT
    )

    assert result.po_number == "PO-5001"


def test_currency_extraction():
    extractor = InvoiceExtractor()

    result = extractor.extract(
        INVOICE_TEXT
    )

    assert result.currency == "INR"


def test_line_item_extraction():
    extractor = LineItemExtractor()

    result = extractor.extract(
        INVOICE_TEXT
    )

    assert len(result) >= 2


def test_tax_extraction():
    extractor = TaxExtractor()

    result = extractor.extract(
        INVOICE_TEXT
    )

    assert len(result) >= 1


def test_discount_extraction():
    extractor = DiscountExtractor()

    result = extractor.extract(
        INVOICE_TEXT
    )

    assert len(result) >= 1


def test_charge_extraction():
    extractor = ChargeExtractor()

    result = extractor.extract(
        INVOICE_TEXT
    )

    assert len(result) >= 1