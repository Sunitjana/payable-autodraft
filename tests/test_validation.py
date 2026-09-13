from decimal import Decimal

from src.validation.financial_validator import (
    FinancialValidator,
)


def test_financial_calculation():
    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": 2,
                "unit_price": "500.00",
                "amount": "1000.00",
            }
        ],
        "taxes": [
            {
                "amount": "180.00"
            }
        ],
        "discounts": [
            {
                "amount": "50.00"
            }
        ],
        "charges": [
            {
                "amount": "20.00"
            }
        ],
        "amounts": {
            "subtotal": "1000.00",
            "total_tax": "180.00",
            "total_discount": "50.00",
            "total_charges": "20.00",
            "gross_amount": "1150.00",
        },
    }

    result = validator.validate(
        payload
    )

    assert result.valid is True


def test_wrong_gross_amount_is_rejected():
    validator = FinancialValidator()

    payload = {
        "line_items": [
            {
                "quantity": 2,
                "unit_price": "500.00",
                "amount": "1000.00",
            }
        ],
        "taxes": [
            {
                "amount": "180.00"
            }
        ],
        "discounts": [],
        "charges": [],
        "amounts": {
            "subtotal": "1000.00",
            "total_tax": "180.00",
            "total_discount": "0.00",
            "total_charges": "0.00",
            "gross_amount": "1200.00",
        },
    }

    result = validator.validate(
        payload
    )

    assert result.valid is False


def test_zero_amount_invoice():
    validator = FinancialValidator()

    payload = {
        "line_items": [],
        "taxes": [],
        "discounts": [],
        "charges": [],
        "amounts": {
            "subtotal": "0.00",
            "total_tax": "0.00",
            "total_discount": "0.00",
            "total_charges": "0.00",
            "gross_amount": "0.00",
        },
    }

    result = validator.validate(
        payload
    )

    assert result.valid is True