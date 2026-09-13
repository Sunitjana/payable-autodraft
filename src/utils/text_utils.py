from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# Unicode / basic normalization
# ---------------------------------------------------------------------------

def normalize_unicode(text: str) -> str:
    """
    Normalize Unicode characters while preserving the original language.

    NFC is used so characters such as accented letters remain represented
    consistently without translating the text.
    """
    if text is None:
        return ""

    text = str(text)

    return unicodedata.normalize("NFC", text)


# ---------------------------------------------------------------------------
# Whitespace
# ---------------------------------------------------------------------------

def normalize_whitespace(text: str) -> str:
    """
    Collapse repeated whitespace while preserving the text content.
    """

    if not text:
        return ""

    text = normalize_unicode(text)

    # Replace tabs/newlines/multiple spaces with one space.
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def clean_ocr_text(text: str) -> str:
    """
    Basic OCR text cleanup.

    Important:
    This function does NOT attempt to correct OCR characters such as
    O -> 0 or I -> 1 because such corrections can corrupt invoice numbers,
    tax IDs, PO numbers, and financial values.
    """

    if not text:
        return ""

    text = normalize_unicode(text)

    # Normalize line endings.
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")

    # Remove Unicode zero-width characters.
    text = re.sub(
        r"[\u200b\u200c\u200d\ufeff]",
        "",
        text,
    )

    # Remove trailing spaces from individual lines.
    lines = [
        line.rstrip()
        for line in text.split("\n")
    ]

    # Remove completely empty lines at the beginning/end,
    # but preserve internal line structure.
    while lines and not lines[0].strip():
        lines.pop(0)

    while lines and not lines[-1].strip():
        lines.pop()

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Comparison normalization
# ---------------------------------------------------------------------------

def normalize_for_comparison(text: str) -> str:
    """
    Normalize text for matching/comparison.

    This is intended for supplier names, labels, etc.

    It should NOT be used to replace the original extracted value.
    """

    if not text:
        return ""

    text = normalize_unicode(text)
    text = text.casefold()

    # Normalize common punctuation to spaces.
    text = re.sub(
        r"[\.,;:!?()\[\]{}<>\"'`/\\|_+=*#@$%^&~]",
        " ",
        text,
    )

    # Collapse whitespace.
    text = normalize_whitespace(text)

    return text


def compact_for_comparison(text: str) -> str:
    """
    Stronger normalization for comparison.

    Example:
        'ABC Pvt. Ltd.'
        'ABC Pvt Ltd'

    become comparable strings.

    Use only for matching, never for final output.
    """

    normalized = normalize_for_comparison(text)

    return re.sub(
        r"[^a-z0-9]+",
        "",
        normalized,
    )


# ---------------------------------------------------------------------------
# Label / keyword utilities
# ---------------------------------------------------------------------------

def contains_any(
    text: str,
    terms: Iterable[str],
    *,
    case_sensitive: bool = False,
) -> bool:
    """
    Check whether text contains any supplied term.
    """

    if not text:
        return False

    if case_sensitive:
        source = text
        candidates = terms
    else:
        source = text.casefold()
        candidates = [
            str(term).casefold()
            for term in terms
        ]

    return any(
        term in source
        for term in candidates
        if term
    )


def find_matching_terms(
    text: str,
    terms: Iterable[str],
    *,
    case_sensitive: bool = False,
) -> list[str]:
    """
    Return all supplied terms found in text.
    """

    if not text:
        return []

    if case_sensitive:
        source = text
    else:
        source = text.casefold()

    matches = []

    for term in terms:
        if not term:
            continue

        search_term = (
            term
            if case_sensitive
            else str(term).casefold()
        )

        if search_term in source:
            matches.append(str(term))

    return matches


def extract_after_label(
    text: str,
    labels: Iterable[str],
) -> Optional[str]:
    """
    Extract text appearing after a label.

    Example:
        'Invoice Number: INV-1001'

    returns:
        'INV-1001'

    This is deliberately conservative and should be followed by
    validation/matching.
    """

    if not text:
        return None

    for label in labels:
        if not label:
            continue

        pattern = (
            rf"(?i)"
            rf"{re.escape(label)}"
            rf"\s*[:#\-]?\s*"
            rf"([^\n\r]+)"
        )

        match = re.search(
            pattern,
            text,
        )

        if match:
            value = normalize_whitespace(
                match.group(1)
            )

            if value:
                return value

    return None


# ---------------------------------------------------------------------------
# Line handling
# ---------------------------------------------------------------------------

def split_lines(text: str) -> list[str]:
    """
    Split text into non-empty normalized lines.
    """

    if not text:
        return []

    cleaned = clean_ocr_text(text)

    return [
        line.strip()
        for line in cleaned.split("\n")
        if line.strip()
    ]


def remove_empty_lines(
    lines: Iterable[str],
) -> list[str]:
    """
    Remove blank lines while preserving order.
    """

    return [
        str(line).strip()
        for line in lines
        if str(line).strip()
    ]


# ---------------------------------------------------------------------------
# Identifier normalization
# ---------------------------------------------------------------------------

def normalize_identifier(
    value: str,
) -> str:
    """
    Normalize an identifier for comparison.

    Useful for:
        Invoice number
        PO number
        Tax ID

    Original value must always be retained separately.
    """

    if not value:
        return ""

    value = normalize_unicode(value)
    value = value.casefold()

    # Remove spaces and common separators only for comparison.
    value = re.sub(
        r"[\s\-_/\\.:#]",
        "",
        value,
    )

    return value


# ---------------------------------------------------------------------------
# Amount/text helpers
# ---------------------------------------------------------------------------

def normalize_numeric_text(
    value: str,
) -> str:
    """
    Normalize formatting around numeric values without converting
    the value to float.

    This prevents loss of precision during extraction.
    """

    if not value:
        return ""

    value = normalize_unicode(value)

    # Remove spaces around numbers.
    value = re.sub(
        r"\s+",
        "",
        value,
    )

    return value


# ---------------------------------------------------------------------------
# Similarity helpers
# ---------------------------------------------------------------------------

def token_set(text: str) -> set[str]:
    """
    Convert text into a normalized token set.
    """

    normalized = normalize_for_comparison(text)

    if not normalized:
        return set()

    return set(normalized.split())


def token_overlap(
    text_a: str,
    text_b: str,
) -> float:
    """
    Calculate simple token overlap.

    Returns a value between 0 and 1.

    This is useful as a lightweight pre-filter before fuzzy matching.
    """

    a = token_set(text_a)
    b = token_set(text_b)

    if not a or not b:
        return 0.0

    intersection = len(a & b)

    denominator = max(
        len(a),
        len(b),
    )

    return intersection / denominator


# ---------------------------------------------------------------------------
# Safety helpers
# ---------------------------------------------------------------------------

def is_empty_text(
    text: Optional[str],
) -> bool:
    """
    Check whether text is empty after normalization.
    """

    return not bool(
        normalize_whitespace(
            text or ""
        )
    )


def truncate_text(
    text: str,
    max_length: int = 5000,
) -> str:
    """
    Limit text size for model prompts/logging.

    This does not alter stored source evidence.
    """

    if not text:
        return ""

    if len(text) <= max_length:
        return text

    return (
        text[:max_length]
        + "\n...[TRUNCATED]..."
    )