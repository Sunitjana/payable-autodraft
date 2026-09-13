from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .page_reference import PageReference, FieldProvenance


@dataclass
class EvidenceItem:
    """
    Raw evidence collected during document processing.
    """

    document_name: str
    page_number: int

    source: str

    text: str = ""

    confidence: Optional[float] = None

    metadata: Dict[str, Any] = field(default_factory=dict)

    reference: Optional[PageReference] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_name": self.document_name,
            "page_number": self.page_number,
            "source": self.source,
            "text": self.text,
            "confidence": self.confidence,
            "metadata": self.metadata,
            "reference": (
                self.reference.to_dict()
                if self.reference
                else None
            ),
        }


class EvidenceStore:
    """
    Central store for source evidence and field provenance.

    Important:
    - Does not alter source evidence.
    - Does not invent values.
    - Allows extraction results to be traced back to pages.
    """

    def __init__(self) -> None:
        self._items: List[EvidenceItem] = []
        self._fields: Dict[str, FieldProvenance] = {}

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def add_evidence(
        self,
        document_name: str,
        page_number: int,
        source: str,
        text: str = "",
        confidence: Optional[float] = None,
        bbox: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EvidenceItem:

        reference = PageReference(
            document_name=document_name,
            page_number=page_number,
            bbox=bbox,
            source=source,
            text=text,
        )

        item = EvidenceItem(
            document_name=document_name,
            page_number=page_number,
            source=source,
            text=text,
            confidence=confidence,
            metadata=metadata or {},
            reference=reference,
        )

        self._items.append(item)

        return item

    def get_all_evidence(self) -> List[EvidenceItem]:
        return list(self._items)

    def get_page_evidence(
        self,
        page_number: int,
    ) -> List[EvidenceItem]:

        return [
            item
            for item in self._items
            if item.page_number == page_number
        ]

    def get_document_evidence(
        self,
        document_name: str,
    ) -> List[EvidenceItem]:

        return [
            item
            for item in self._items
            if item.document_name == document_name
        ]

    # ------------------------------------------------------------------
    # Field provenance
    # ------------------------------------------------------------------

    def add_field(
        self,
        field_name: str,
        value: Any,
        references: Optional[List[PageReference]] = None,
        confidence: Optional[float] = None,
        extraction_method: Optional[str] = None,
        verified: bool = False,
        notes: Optional[str] = None,
    ) -> FieldProvenance:

        provenance = FieldProvenance(
            field_name=field_name,
            value=value,
            references=references or [],
            confidence=confidence,
            extraction_method=extraction_method,
            verified=verified,
            notes=notes,
        )

        self._fields[field_name] = provenance

        return provenance

    def get_field(
        self,
        field_name: str,
    ) -> Optional[FieldProvenance]:

        return self._fields.get(field_name)

    def get_all_fields(self) -> Dict[str, FieldProvenance]:
        return dict(self._fields)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence": [
                item.to_dict()
                for item in self._items
            ],
            "field_provenance": {
                field_name: provenance.to_dict()
                for field_name, provenance in self._fields.items()
            },
        }

    def save(self, path: str | Path) -> None:
        """
        Save evidence to JSON.
        """

        import json

        path = Path(path)
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with path.open(
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                self.to_dict(),
                file,
                ensure_ascii=False,
                indent=2,
                default=str,
            )