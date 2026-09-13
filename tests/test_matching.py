from src.matching.supplier_matcher import (
    SupplierMatcher,
)

from src.matching.po_matcher import (
    POMatcher,
)

from src.matching.tax_matcher import (
    TaxMatcher,
)

from src.matching.payment_terms_matcher import (
    PaymentTermsMatcher,
)

from src.matching.chart_of_books_matcher import (
    ChartOfBooksMatcher,
)


SUPPLIERS = [
    {
        "supplier_id": "SUP001",
        "supplier_name": "ABC Technologies Pvt Ltd",
        "tax_id": "GST123456",
    },
    {
        "supplier_id": "SUP002",
        "supplier_name": "XYZ Industries",
        "tax_id": "GST999999",
    },
]


PURCHASE_ORDERS = [
    {
        "po_number": "PO-5001",
        "supplier_id": "SUP001",
    }
]


TAX_MASTER = [
    {
        "tax_code": "GST18",
        "tax_name": "GST",
        "rate": 18,
    }
]


PAYMENT_TERMS = [
    {
        "code": "NET30",
        "description": "Net 30 Days",
        "days": 30,
    }
]


CHART_OF_BOOKS = [
    {
        "account_code": "500100",
        "account_name": "Office Equipment",
    }
]


def test_supplier_exact_match():
    matcher = SupplierMatcher(
        SUPPLIERS
    )

    result = matcher.match(
        supplier_name="ABC Technologies Pvt Ltd",
        tax_id="GST123456",
    )

    assert result.matched is True
    assert (
        result.record["supplier_id"]
        == "SUP001"
    )


def test_supplier_unknown_is_not_invented():
    matcher = SupplierMatcher(
        SUPPLIERS
    )

    result = matcher.match(
        supplier_name="Unknown Supplier Ltd",
        tax_id="UNKNOWN",
    )

    assert result.matched is False
    assert result.record is None


def test_po_match():
    matcher = POMatcher(
        PURCHASE_ORDERS
    )

    result = matcher.match(
        po_number="PO-5001",
        supplier_id="SUP001",
    )

    assert result.matched is True


def test_po_supplier_mismatch():
    matcher = POMatcher(
        PURCHASE_ORDERS
    )

    result = matcher.match(
        po_number="PO-5001",
        supplier_id="SUP002",
    )

    assert result.matched is False


def test_tax_match():
    matcher = TaxMatcher(
        TAX_MASTER
    )

    result = matcher.match(
        tax_code="GST18"
    )

    assert result.matched is True


def test_payment_terms_match():
    matcher = PaymentTermsMatcher(
        PAYMENT_TERMS
    )

    result = matcher.match(
        code="NET30"
    )

    assert result.matched is True


def test_chart_of_books_match():
    matcher = ChartOfBooksMatcher(
        CHART_OF_BOOKS
    )

    result = matcher.match(
        account_code="500100"
    )

    assert result.matched is True