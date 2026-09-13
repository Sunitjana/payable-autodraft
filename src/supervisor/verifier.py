"""
OCR verification between primary and fallback OCR outputs.

This module compares extracted field values only. It does not decide which
OCR result is correct; unresolved conflicts are escalated to the visual
supervisor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
import re
from typing import Any, Dict, List, Optional


@dataclass
class FieldVerification:
    field: str
    primary_value: Any
    fallback_value: Any
    status: str
    confidence: float
    reason: str = ""


@dataclass
class VerificationResult:
    fields: List[FieldVerification] = field(default_factory=list)
    agreement_score: float = 0.0
    conflict_count: int = 0
    requires_supervisor: bool = False


class OCRVerifier:
    """
    Compare primary and fallback OCR outputs.

    The verifier:
    - identifies agreement, probable agreement, missing values and conflicts;
    - never chooses a winning OCR result;
    - sends unresolved conflicts to the visual supervisor.
    """

    IMPORTANT_FIELDS = [
        "invoice_number",
        "invoice_date",
        "due_date",
        "supplier_name",
        "supplier_tax_id",
        "po_number",
        "currency",
        "subtotal",
        "gross_amount",
    ]

    def __init__(self, similarity_threshold: float = 0.92) -> None:
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError(
                "similarity_threshold must be between 0.0 and 1.0"
            )

        self.similarity_threshold = float(similarity_threshold)

    @staticmethod
    def normalize(value: Any) -> str:
        """
        Normalize a textual value for comparison.

        Original OCR values are never modified.
        """
        if value is None:
            return ""

        text = str(value).strip().lower()
        text = re.sub(r"\s+", " ", text)

        return text

    @staticmethod
    def _parse_decimal(value: Any) -> Optional[Decimal]:
        """
        Parse common monetary/number representations for comparison.

        Examples:
            1234.50
            1,234.50
            1.234,50
            ₹1,234.50
            THB 8,397.36

        This is comparison-only. It does not alter the source value.
        """
        if value is None:
            return None

        text = str(value).strip()

        if not text:
            return None

        # Keep only numeric characters and decimal separators.
        text = re.sub(r"[^\d,.\-+]", "", text)

        if not text:
            return None

        # Both comma and dot are present.
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

        # Only comma is present.
        elif "," in text:
            parts = text.split(",")

            if (
                len(parts) == 2
                and len(parts[1]) in (1, 2, 3)
            ):
                # 1234,50 -> 1234.50
                text = text.replace(",", ".")
            else:
                # 1,234,567 -> 1234567
                text = text.replace(",", "")

        try:
            return Decimal(text)

        except InvalidOperation:
            return None

    @classmethod
    def numeric_normalize(cls, value: Any) -> str:
        """
        Return canonical decimal text for numeric comparison.
        """
        parsed = cls._parse_decimal(value)

        if parsed is None:
            return ""

        return format(parsed.normalize(), "f")

    def compare_values(
        self,
        primary: Any,
        fallback: Any,
    ) -> tuple[str, float, str]:
        """
        Compare one field from primary and fallback OCR.

        Returns:
            (status, confidence, reason)
        """
        primary_normalized = self.normalize(primary)
        fallback_normalized = self.normalize(fallback)

        # ---------------------------------------------------------
        # Both missing
        # ---------------------------------------------------------
        if not primary_normalized and not fallback_normalized:
            return (
                "missing",
                0.0,
                "Both OCR outputs are missing.",
            )

        # ---------------------------------------------------------
        # Primary only
        # ---------------------------------------------------------
        if primary_normalized and not fallback_normalized:
            return (
                "primary_only",
                0.65,
                "Only primary OCR produced a value.",
            )

        # ---------------------------------------------------------
        # Fallback only
        # ---------------------------------------------------------
        if not primary_normalized and fallback_normalized:
            return (
                "fallback_only",
                0.65,
                "Only fallback OCR produced a value.",
            )

        # ---------------------------------------------------------
        # Exact textual agreement
        # ---------------------------------------------------------
        if primary_normalized == fallback_normalized:
            return (
                "agreement",
                1.0,
                "OCR outputs agree.",
            )

        # ---------------------------------------------------------
        # Numeric agreement
        # ---------------------------------------------------------
        primary_numeric = self.numeric_normalize(primary)
        fallback_numeric = self.numeric_normalize(fallback)

        if (
            primary_numeric
            and fallback_numeric
            and primary_numeric == fallback_numeric
        ):
            return (
                "agreement",
                1.0,
                "Numeric values agree after normalization.",
            )

        # ---------------------------------------------------------
        # Fuzzy textual comparison
        # ---------------------------------------------------------
        similarity = SequenceMatcher(
            None,
            primary_normalized,
            fallback_normalized,
        ).ratio()

        if similarity >= self.similarity_threshold:
            return (
                "probable_agreement",
                similarity,
                (
                    "OCR outputs are highly similar "
                    "but not identical."
                ),
            )

        # ---------------------------------------------------------
        # Conflict
        # ---------------------------------------------------------
        return (
            "conflict",
            similarity,
            "OCR outputs disagree.",
        )

    def compare(
        self,
        primary: Dict[str, Any],
        fallback: Dict[str, Any],
    ) -> VerificationResult:
        """
        Compare important fields between primary and fallback OCR.
        """
        if not isinstance(primary, dict):
            raise TypeError("primary must be a dictionary")

        if not isinstance(fallback, dict):
            raise TypeError("fallback must be a dictionary")

        results: List[FieldVerification] = []

        conflicts = 0
        agreements = 0
        comparable = 0

        for field_name in self.IMPORTANT_FIELDS:

            primary_value = primary.get(field_name)
            fallback_value = fallback.get(field_name)

            status, confidence, reason = self.compare_values(
                primary_value,
                fallback_value,
            )

            # A field is comparable only when both OCR outputs
            # supplied a value.
            if status not in {
                "missing",
                "primary_only",
                "fallback_only",
            }:
                comparable += 1

            if status in {
                "agreement",
                "probable_agreement",
            }:
                agreements += 1

            if status == "conflict":
                conflicts += 1

            results.append(
                FieldVerification(
                    field=field_name,
                    primary_value=primary_value,
                    fallback_value=fallback_value,
                    status=status,
                    confidence=confidence,
                    reason=reason,
                )
            )

        agreement_score = (
            agreements / comparable
            if comparable
            else 0.0
        )

        return VerificationResult(
            fields=results,
            agreement_score=agreement_score,
            conflict_count=conflicts,
            requires_supervisor=(
                conflicts > 0
                or agreement_score < 0.80
            ),
        )


def _run_self_tests() -> None:
    """
    Internal tests for OCR verifier.
    """

    verifier = OCRVerifier()

    # ---------------------------------------------------------
    # Exact agreement
    # ---------------------------------------------------------
    status, confidence, _ = verifier.compare_values(
        "INV-1001",
        "INV-1001",
    )

    assert status == "agreement"
    assert confidence == 1.0

    # ---------------------------------------------------------
    # Numeric agreement - US format
    # ---------------------------------------------------------
    status, _, _ = verifier.compare_values(
        "1,234.50",
        "1234.50",
    )

    assert status == "agreement"

    # ---------------------------------------------------------
    # Numeric agreement - European format
    # ---------------------------------------------------------
    status, _, _ = verifier.compare_values(
        "1.234,50",
        "1234.50",
    )

    assert status == "agreement"

    # ---------------------------------------------------------
    # Primary only
    # ---------------------------------------------------------
    status, _, _ = verifier.compare_values(
        "INV-1001",
        None,
    )

    assert status == "primary_only"

    # ---------------------------------------------------------
    # Fallback only
    # ---------------------------------------------------------
    status, _, _ = verifier.compare_values(
        None,
        "INV-1001",
    )

    assert status == "fallback_only"

    # ---------------------------------------------------------
    # Both missing
    # ---------------------------------------------------------
    status, _, _ = verifier.compare_values(
        None,
        None,
    )

    assert status == "missing"

    # ---------------------------------------------------------
    # Conflict
    # ---------------------------------------------------------
    status, confidence, _ = verifier.compare_values(
        "INV-1001",
        "INV-1002",
    )

    assert status == "conflict"
    assert 0.0 <= confidence < 0.92

    # ---------------------------------------------------------
    # Complete matching OCR payload
    # ---------------------------------------------------------
    primary = {
        "invoice_number": "INV-1001",
        "invoice_date": "2026-09-10",
        "currency": "THB",
        "subtotal": "7,200.00",
        "gross_amount": "8,397.36",
    }

    fallback = {
        "invoice_number": "INV-1001",
        "invoice_date": "2026-09-10",
        "currency": "THB",
        "subtotal": "7200",
        "gross_amount": "8397.36",
    }

    result = verifier.compare(
        primary,
        fallback,
    )

    assert result.conflict_count == 0
    assert result.agreement_score == 1.0
    assert result.requires_supervisor is False

    # ---------------------------------------------------------
    # Financial conflict
    # ---------------------------------------------------------
    conflicting = dict(fallback)

    conflicting["gross_amount"] = "8,397.63"

    result = verifier.compare(
        primary,
        conflicting,
    )

    assert result.conflict_count == 1
    assert result.requires_supervisor is True

    # ---------------------------------------------------------
    # Invalid threshold
    # ---------------------------------------------------------
    try:
        OCRVerifier(similarity_threshold=1.5)

    except ValueError:
        pass

    else:
        raise AssertionError(
            "Invalid threshold should raise ValueError"
        )

    # ---------------------------------------------------------
    # Invalid payload type
    # ---------------------------------------------------------
    try:
        verifier.compare([], {})

    except TypeError:
        pass

    else:
        raise AssertionError(
            "Non-dict primary should raise TypeError"
        )

    print("ALL OCR VERIFIER TESTS PASSED")


if __name__ == "__main__":
    _run_self_tests()