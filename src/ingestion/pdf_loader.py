from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from pypdf import PdfReader


@dataclass
class PDFPage:
    page_number: int
    text: str
    width: float
    height: float
    image: Optional[object] = None


@dataclass
class PDFDocument:
    path: Path
    page_count: int
    pages: list[PDFPage]


class PDFLoader:
    """Lightweight native PDF text loader using pypdf.

    Rendering is intentionally kept out of this class.  Scanned pages are
    rendered on demand by PageProcessor only when OCR is actually required.
    """

    def __init__(self, max_pages: int = 1000) -> None:
        if isinstance(max_pages, bool) or not isinstance(max_pages, int):
            raise TypeError("max_pages must be an integer.")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1.")
        self.max_pages = max_pages

    @staticmethod
    def _validate_path(pdf_path: str | Path) -> Path:
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {path}")
        if not path.is_file():
            raise ValueError(f"PDF path is not a file: {path}")
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"Expected a PDF file: {path}")
        return path

    def _reader(self, pdf_path: Path) -> PdfReader:
        try:
            reader = PdfReader(str(pdf_path), strict=False)
        except Exception as exc:
            raise ValueError(f"Could not open PDF: {pdf_path}") from exc
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception:
                ok = 0
            if not ok:
                raise ValueError(f"Encrypted PDF requires a password: {pdf_path}")
        count = len(reader.pages)
        if count == 0:
            raise ValueError(f"PDF contains no pages: {pdf_path}")
        if count > self.max_pages:
            raise ValueError(
                f"PDF has {count} pages, which exceeds MAX_PAGES={self.max_pages}"
            )
        return reader

    @staticmethod
    def _page_size(page) -> tuple[float, float]:
        try:
            box = page.mediabox
            return float(box.width), float(box.height)
        except Exception:
            return 0.0, 0.0

    def load(self, pdf_path: str | Path) -> PDFDocument:
        path = self._validate_path(pdf_path)
        reader = self._reader(path)
        pages: list[PDFPage] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                width, height = self._page_size(page)
                text = page.extract_text() or ""
                pages.append(PDFPage(index, text, width, height))
            except Exception as exc:
                raise ValueError(f"Could not process page {index} of {path}") from exc
        return PDFDocument(path, len(pages), pages)

    def iter_pages(self, pdf_path: str | Path) -> Iterator[PDFPage]:
        path = self._validate_path(pdf_path)
        reader = self._reader(path)
        for index, page in enumerate(reader.pages, start=1):
            width, height = self._page_size(page)
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            yield PDFPage(index, text, width, height)

    def extract_native_text(self, pdf_path: str | Path) -> str:
        return "\n".join(p.text.strip() for p in self.iter_pages(pdf_path) if p.text.strip())

    def get_page_count(self, pdf_path: str | Path) -> int:
        return len(self._reader(self._validate_path(pdf_path)).pages)

    def get_pdf_info(self, pdf_path: str | Path) -> dict:
        path = self._validate_path(pdf_path)
        reader = self._reader(path)
        return {
            "path": str(path),
            "filename": path.name,
            "page_count": len(reader.pages),
            "metadata": dict(reader.metadata or {}),
        }
