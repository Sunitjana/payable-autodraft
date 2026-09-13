from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PageReference:
    """
    Identifies the source location of extracted information.
    """

    document_name: str
    page_number: int

    # Optional location information
    bbox: Optional[List[float]] = None

    # Source type: native_pdf, paddle_ocr, unlimited_ocr, qwen_vl, etc.
    source: str = "unknown"

    # Original text associated with this evidence
    text: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_name": self.document_name,
            "page_number": self.page_number,
            "bbox": self.bbox,
            "source": self.source,
            "text": self.text,
        }


@dataclass
class FieldProvenance:
    """
    Provenance information for one extracted field.

    Example:
        invoice_number -> INV-12345
        page -> 2
        source -> paddle_ocr
    """

    field_name: str
    value: Any

    references: List[PageReference] = field(default_factory=list)

    confidence: Optional[float] = None

    extraction_method: Optional[str] = None

    verified: bool = False

    notes: Optional[str] = None

    def add_reference(self, reference: PageReference) -> None:
        self.references.append(reference)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_name": self.field_name,
            "value": self.value,
            "references": [
                reference.to_dict()
                for reference in self.references
            ],
            "confidence": self.confidence,
            "extraction_method": self.extraction_method,
            "verified": self.verified,
            "notes": self.notes,
        }