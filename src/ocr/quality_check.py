from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# ============================================================
# Configuration
# ============================================================

DEFAULT_QUALITY_THRESHOLD = 0.90
DEFAULT_MIN_TEXT_LENGTH = 30

# Whole-page sanity thresholds.
MIN_NATIVE_WORDS = 5
MIN_OCR_WORDS = 3

MIN_ALPHANUMERIC_RATIO = 0.20
MIN_PRINTABLE_RATIO = 0.70


# ============================================================
# Result model
# ============================================================

@dataclass
class QualityAssessment:
    """Quality assessment for extracted document text."""

    score: float
    accepted: bool

    character_count: int
    word_count: int

    alphanumeric_ratio: float
    printable_ratio: float

    reasons: list[str]

    # OCR confidence metadata.
    confidence_available: bool = False
    recognition_confidence: float | None = None
    confidence_source: str | None = None


OCRResultAssessment = QualityAssessment


# ============================================================
# Basic text statistics
# ============================================================

def text_statistics(text: str) -> dict[str, Any]:
    """
    Calculate basic text-quality statistics.

    This function performs no semantic interpretation.

    Important:
        Short values such as:
            1
            5%
            PO12
            ₹99
            0.5
        are NOT removed or penalized individually.

    min_text_length is a whole-page sanity check only.
    """

    text = text or ""

    character_count = len(text)

    words = re.findall(r"\S+", text)
    word_count = len(words)

    if character_count == 0:
        return {
            "character_count": 0,
            "word_count": 0,
            "alphanumeric_ratio": 0.0,
            "printable_ratio": 0.0,
        }

    alphanumeric_count = sum(
        1 for char in text
        if char.isalnum()
    )

    printable_count = sum(
        1 for char in text
        if char.isprintable()
    )

    return {
        "character_count": character_count,
        "word_count": word_count,
        "alphanumeric_ratio": (
            alphanumeric_count / character_count
        ),
        "printable_ratio": (
            printable_count / character_count
        ),
    }


# ============================================================
# Structural signals
# ============================================================

def _structural_score(text: str) -> float:
    """
    Estimate whether the page contains useful document structure.

    This is NOT a financial/document-understanding decision.

    It only checks for common structural signals such as:
        - invoice
        - date
        - total
        - amount
        - tax
        - supplier/vendor
        - PO
        - quantity
        - unit price

    The score is intentionally conservative.
    """

    text = text or ""

    if not text.strip():
        return 0.0

    lower = text.lower()

    signals = [
        r"\binvoice\b",
        r"\binvoice\s*(no|number|#)\b",
        r"\bdate\b",
        r"\btotal\b",
        r"\bamount\b",
        r"\btax\b",
        r"\bvat\b",
        r"\bsupplier\b",
        r"\bvendor\b",
        r"\bpo\b",
        r"\bpurchase\s+order\b",
        r"\bquantity\b",
        r"\bqty\b",
        r"\bunit\s*price\b",
        r"\bsubtotal\b",
        r"\bcurrency\b",
    ]

    matched = sum(
        1
        for pattern in signals
        if re.search(pattern, lower)
    )

    # Four or more structural signals means strong structure.
    return min(matched / 4.0, 1.0)


def _quality_components(
    text: str,
    min_text_length: int,
) -> tuple[dict[str, Any], float, float, float]:
    """
    Calculate reusable quality components.

    Returns:
        stats,
        length_score,
        word_score,
        structural_score
    """

    stats = text_statistics(text)

    character_count = stats["character_count"]
    word_count = stats["word_count"]

    length_score = min(
        character_count / max(min_text_length * 4, 1),
        1.0,
    )

    word_score = min(
        word_count / 20.0,
        1.0,
    )

    structural_score = _structural_score(text)

    return (
        stats,
        length_score,
        word_score,
        structural_score,
    )


# ============================================================
# Native PDF text
# ============================================================

def assess_native_text(
    text: str,
    min_text_length: int = DEFAULT_MIN_TEXT_LENGTH,
    threshold: float = DEFAULT_QUALITY_THRESHOLD,
) -> QualityAssessment:
    """
    Assess text extracted directly from a PDF.

    Native text is accepted only when its page-level quality
    reaches the configured threshold.

    Default threshold = 90%.

    This does NOT determine whether the document is actually
    an invoice. That decision belongs to document understanding.
    """

    stats, length_score, word_score, structural_score = (
        _quality_components(
            text,
            min_text_length,
        )
    )

    character_count = stats["character_count"]
    word_count = stats["word_count"]
    alphanumeric_ratio = stats["alphanumeric_ratio"]
    printable_ratio = stats["printable_ratio"]

    reasons: list[str] = []

    # --------------------------------------------------------
    # Whole-page sanity checks
    # --------------------------------------------------------

    if character_count < min_text_length:
        reasons.append(
            f"Text length {character_count} is below "
            f"minimum {min_text_length}"
        )

    if word_count < MIN_NATIVE_WORDS:
        reasons.append(
            f"Word count {word_count} is too low"
        )

    if alphanumeric_ratio < 0.30:
        reasons.append(
            "Low alphanumeric character ratio"
        )

    if printable_ratio < 0.80:
        reasons.append(
            "Low printable character ratio"
        )

    # --------------------------------------------------------
    # Quality score
    # --------------------------------------------------------

    score = (
        0.30 * length_score
        + 0.20 * word_score
        + 0.20 * alphanumeric_ratio
        + 0.15 * printable_ratio
        + 0.15 * structural_score
    )

    score = min(max(score, 0.0), 1.0)

    if score < threshold:
        reasons.append(
            f"Quality score {score:.3f} is below "
            f"required threshold {threshold:.3f}"
        )

    accepted = (
        score >= threshold
        and character_count >= min_text_length
        and word_count >= MIN_NATIVE_WORDS
        and alphanumeric_ratio >= 0.30
        and printable_ratio >= 0.80
    )

    return QualityAssessment(
        score=round(float(score), 4),
        accepted=accepted,
        character_count=character_count,
        word_count=word_count,
        alphanumeric_ratio=round(
            float(alphanumeric_ratio),
            4,
        ),
        printable_ratio=round(
            float(printable_ratio),
            4,
        ),
        reasons=reasons,
        confidence_available=False,
        recognition_confidence=None,
        confidence_source=None,
    )


# ============================================================
# Native text -> OCR decision
# ============================================================

def should_use_ocr(
    text: str,
    min_text_length: int = DEFAULT_MIN_TEXT_LENGTH,
    threshold: float = DEFAULT_QUALITY_THRESHOLD,
) -> bool:
    """
    Return True when native PDF text does not meet the
    required quality threshold.
    """

    assessment = assess_native_text(
        text,
        min_text_length=min_text_length,
        threshold=threshold,
    )

    return not assessment.accepted


def should_run_ocr(
    text: str,
    min_text_length: int = DEFAULT_MIN_TEXT_LENGTH,
    threshold: float = DEFAULT_QUALITY_THRESHOLD,
) -> bool:
    """
    Backward-compatible alias for should_use_ocr().
    """

    return should_use_ocr(
        text,
        min_text_length=min_text_length,
        threshold=threshold,
    )


# ============================================================
# OCR result
# ============================================================

def assess_ocr_result(
    text: str,
    confidence: float | None = None,
    threshold: float = DEFAULT_QUALITY_THRESHOLD,
    confidence_source: str | None = None,
) -> QualityAssessment:
    """
    Assess OCR output.

    IMPORTANT:

    1. If explicit recognition confidence exists:
           recognition confidence must be >= threshold.

    2. If recognition confidence is unavailable:
           DO NOT invent a confidence value.
           DO NOT use layout confidence as OCR confidence.

       In that case the text/structure quality score is used
       for routing, while the result remains marked as
       confidence-unavailable.

    3. A result with unavailable recognition confidence may
       therefore be accepted by the OCR text-quality gate,
       but it must still be verified by the supervisor layer.
    """

    stats, length_score, word_score, structural_score = (
        _quality_components(
            text,
            DEFAULT_MIN_TEXT_LENGTH,
        )
    )

    character_count = stats["character_count"]
    word_count = stats["word_count"]
    alphanumeric_ratio = stats["alphanumeric_ratio"]
    printable_ratio = stats["printable_ratio"]

    reasons: list[str] = []

    # --------------------------------------------------------
    # Text quality
    # --------------------------------------------------------

    text_quality = (
        0.30 * length_score
        + 0.20 * word_score
        + 0.20 * alphanumeric_ratio
        + 0.15 * printable_ratio
        + 0.15 * structural_score
    )

    text_quality = min(
        max(text_quality, 0.0),
        1.0,
    )

    # --------------------------------------------------------
    # Explicit recognition confidence
    # --------------------------------------------------------

    normalized_confidence: float | None = None

    if confidence is not None:

        try:
            normalized_confidence = float(confidence)

        except (TypeError, ValueError):

            normalized_confidence = None

            reasons.append(
                "OCR confidence value is invalid"
            )

    if normalized_confidence is not None:

        normalized_confidence = max(
            0.0,
            min(normalized_confidence, 1.0),
        )

        # Explicit confidence is real evidence, so combine it
        # with page-level text quality.
        score = (
            0.60 * normalized_confidence
            + 0.40 * text_quality
        )

        confidence_available = True

        if normalized_confidence < threshold:
            reasons.append(
                f"OCR recognition confidence "
                f"{normalized_confidence:.3f} is below "
                f"required threshold {threshold:.3f}"
            )

    else:

        # ----------------------------------------------------
        # Confidence unavailable
        # ----------------------------------------------------

        score = text_quality

        confidence_available = False

        reasons.append(
            "OCR recognition confidence unavailable; "
            "text/structure quality used for routing"
        )

    # --------------------------------------------------------
    # General sanity checks
    # --------------------------------------------------------

    if character_count == 0:
        reasons.append(
            "OCR returned no text"
        )

    if word_count < MIN_OCR_WORDS:
        reasons.append(
            f"OCR word count {word_count} is too low"
        )

    if alphanumeric_ratio < MIN_ALPHANUMERIC_RATIO:
        reasons.append(
            "Low alphanumeric character ratio"
        )

    if printable_ratio < MIN_PRINTABLE_RATIO:
        reasons.append(
            "Low printable character ratio"
        )

    if score < threshold:
        reasons.append(
            f"OCR quality score {score:.3f} is below "
            f"required threshold {threshold:.3f}"
        )

    # --------------------------------------------------------
    # Acceptance
    # --------------------------------------------------------

    accepted = (
        score >= threshold
        and character_count >= 10
        and word_count >= MIN_OCR_WORDS
        and alphanumeric_ratio >= MIN_ALPHANUMERIC_RATIO
        and printable_ratio >= MIN_PRINTABLE_RATIO
        and (
            normalized_confidence is None
            or normalized_confidence >= threshold
        )
    )

    return QualityAssessment(
        score=round(float(score), 4),
        accepted=accepted,
        character_count=character_count,
        word_count=word_count,
        alphanumeric_ratio=round(
            float(alphanumeric_ratio),
            4,
        ),
        printable_ratio=round(
            float(printable_ratio),
            4,
        ),
        reasons=reasons,
        confidence_available=confidence_available,
        recognition_confidence=(
            round(float(normalized_confidence), 4)
            if normalized_confidence is not None
            else None
        ),
        confidence_source=(
            confidence_source
            if normalized_confidence is not None
            else None
        ),
    )


# ============================================================
# Unlimited-OCR decision
# ============================================================

def should_use_unlimited_ocr(
    text: str,
    confidence: float | None = None,
    threshold: float = DEFAULT_QUALITY_THRESHOLD,
    confidence_source: str | None = None,
) -> bool:
    """
    Decide whether Unlimited-OCR should be invoked.

    Unlimited-OCR is used only when the primary OCR result
    fails the 90% quality gate.

    Missing confidence alone does NOT automatically mean the
    OCR result is bad.
    """

    assessment = assess_ocr_result(
        text=text,
        confidence=confidence,
        threshold=threshold,
        confidence_source=confidence_source,
    )

    return not assessment.accepted


# ============================================================
# Convenience helper
# ============================================================

def quality_score(
    text: str,
    min_text_length: int = DEFAULT_MIN_TEXT_LENGTH,
) -> float:
    """
    Return only the page-level text/structure quality score.

    Useful when a caller needs a simple numeric score.
    """

    stats, length_score, word_score, structural_score = (
        _quality_components(
            text,
            min_text_length,
        )
    )

    score = (
        0.30 * length_score
        + 0.20 * word_score
        + 0.20 * stats["alphanumeric_ratio"]
        + 0.15 * stats["printable_ratio"]
        + 0.15 * structural_score
    )

    return round(
        min(max(float(score), 0.0), 1.0),
        4,
    )


class TextQualityChecker:
    """Compatibility facade for callers using the original OCR API."""

    def __init__(
        self,
        threshold: float = 0.80,
        min_text_length: int = DEFAULT_MIN_TEXT_LENGTH,
    ) -> None:
        self.threshold = threshold
        self.min_text_length = min_text_length

    def analyze(self, text: str) -> dict[str, Any]:
        assessment = assess_native_text(
            text,
            min_text_length=self.min_text_length,
            threshold=self.threshold,
        )
        return {
            "is_usable": assessment.accepted,
            "needs_ocr": not assessment.accepted,
            "score": assessment.score,
            "reasons": assessment.reasons,
        }

    def assess_ocr_result(
        self,
        text: str,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        assessment = globals()["assess_ocr_result"](
            text,
            confidence=confidence,
            threshold=self.threshold,
        )
        return {
            "accepted": assessment.accepted,
            "score": assessment.score,
            "reasons": assessment.reasons,
        }

    def should_use_fallback(
        self,
        text: str,
        confidence: float | None = None,
    ) -> bool:
        return should_use_unlimited_ocr(
            text,
            confidence=confidence,
            threshold=self.threshold,
        )


# ============================================================
# CLI test
# ============================================================

if __name__ == "__main__":

    test_text = """
    INVOICE
    Invoice Number: INV-001
    Date: 13-09-2026
    Supplier: ABC Pvt Ltd
    PO Number: PO-123
    Quantity: 1
    Unit Price: 99.00
    Tax: 5%
    Total: 103.95
    """

    print("=" * 60)
    print("QUALITY CHECK TEST")
    print("=" * 60)

    native = assess_native_text(
        test_text,
        threshold=0.90,
    )

    print("\nNATIVE TEXT")
    print(f"Score:      {native.score:.4f}")
    print(f"Accepted:   {native.accepted}")
    print(f"Characters: {native.character_count}")
    print(f"Words:      {native.word_count}")
    print(
        f"Alpha ratio:{native.alphanumeric_ratio:.4f}"
    )
    print(
        f"Printable:  {native.printable_ratio:.4f}"
    )

    print("\nReasons:")
    for reason in native.reasons:
        print(f"  - {reason}")

    print("\nOCR WITHOUT CONFIDENCE")

    ocr = assess_ocr_result(
        test_text,
        confidence=None,
        threshold=0.90,
    )

    print(f"Score:               {ocr.score:.4f}")
    print(f"Accepted:            {ocr.accepted}")
    print(
        f"Confidence available:{ocr.confidence_available}"
    )
    print(
        f"Recognition confidence:"
        f" {ocr.recognition_confidence}"
    )
    print(
        f"Confidence source:   {ocr.confidence_source}"
    )

    print("\nReasons:")
    for reason in ocr.reasons:
        print(f"  - {reason}")

    print("\nOCR WITH 95% CONFIDENCE")

    ocr_confident = assess_ocr_result(
        test_text,
        confidence=0.95,
        threshold=0.90,
        confidence_source="explicit_recognition_score",
    )

    print(f"Score:               {ocr_confident.score:.4f}")
    print(f"Accepted:            {ocr_confident.accepted}")
    print(
        f"Confidence available:"
        f" {ocr_confident.confidence_available}"
    )
    print(
        f"Recognition confidence:"
        f" {ocr_confident.recognition_confidence}"
    )
    print(
        f"Confidence source:   "
        f"{ocr_confident.confidence_source}"
    )

    print("\nShort-value preservation test")

    short_value_text = (
        "INV-1 PO-2 QTY 1 TAX 5% TOTAL 99"
    )

    short_stats = text_statistics(
        short_value_text
    )

    print(
        f"Text: {short_value_text}"
    )
    print(
        f"Characters: {short_stats['character_count']}"
    )
    print(
        f"Words: {short_stats['word_count']}"
    )

    print("=" * 60)