from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pypdf import PdfReader
import pypdfium2 as pdfium


@dataclass
class PageRecord:
    page_number: int
    text: str
    image_path: Optional[Path]
    width: float
    height: float
    source_pdf: Path


class PageProcessor:
    """Fast PDF page metadata/text reader with lazy image rendering.

    pypdf is used for native text and pypdfium2 is used only when an image is
    actually requested. This removes the PyMuPDF dependency and prevents the
    old implementation from rendering every page before OCR routing.
    """

    def __init__(self, dpi: int = 180, image_output_dir: str | Path = "output/page_images") -> None:
        if isinstance(dpi, bool) or not isinstance(dpi, int):
            raise TypeError("dpi must be an integer.")
        if dpi <= 0:
            raise ValueError("dpi must be greater than 0.")
        self.dpi = dpi
        self.image_output_dir = Path(image_output_dir)

    @staticmethod
    def _validate_pdf_path(pdf_path: str | Path) -> Path:
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {path}")
        if not path.is_file():
            raise ValueError(f"PDF path is not a file: {path}")
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"Expected a PDF file: {path}")
        return path

    @staticmethod
    def _reader(path: Path) -> PdfReader:
        try:
            reader = PdfReader(str(path), strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise ValueError("password protected")
            if len(reader.pages) == 0:
                raise ValueError("no pages")
            return reader
        except Exception as exc:
            raise ValueError(f"Could not open PDF: {path}") from exc

    def _get_image_path(self, pdf_path: Path, page_number: int, dpi: int | None = None) -> Path:
        use_dpi = int(dpi or self.dpi)
        return self.image_output_dir / f"{pdf_path.stem}_dpi{use_dpi}_page_{page_number}.png"

    def process(self, pdf_path: str | Path, render_images: bool = False) -> list[PageRecord]:
        path = self._validate_pdf_path(pdf_path)
        reader = self._reader(path)
        records: list[PageRecord] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
                box = page.mediabox
                width, height = float(box.width), float(box.height)
            except Exception as exc:
                raise ValueError(f"Could not process page {index} of {path}") from exc
            image_path = self.render_page(path, index) if render_images else None
            records.append(PageRecord(index, text, image_path, width, height, path))
        return records

    def process_page(self, pdf_path: str | Path, page_number: int, render_image: bool = True) -> PageRecord:
        if isinstance(page_number, bool) or not isinstance(page_number, int):
            raise TypeError("page_number must be an integer.")
        if page_number < 1:
            raise ValueError("page_number must be 1 or greater.")
        path = self._validate_pdf_path(pdf_path)
        reader = self._reader(path)
        if page_number > len(reader.pages):
            raise IndexError(f"Page {page_number} does not exist. PDF contains {len(reader.pages)} pages.")
        page = reader.pages[page_number - 1]
        text = page.extract_text() or ""
        box = page.mediabox
        image_path = self.render_page(path, page_number) if render_image else None
        return PageRecord(page_number, text, image_path, float(box.width), float(box.height), path)

    def render_page(self, pdf_path: str | Path, page_number: int, dpi: int | None = None) -> Path:
        path = self._validate_pdf_path(pdf_path)
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
            raise ValueError("page_number must be a positive integer")
        use_dpi = int(dpi or self.dpi)
        if use_dpi <= 0:
            raise ValueError("dpi must be positive")
        image_path = self._get_image_path(path, page_number, use_dpi)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        if image_path.exists() and image_path.stat().st_size > 0:
            return image_path

        doc = None
        page = None
        bitmap = None
        image = None
        try:
            doc = pdfium.PdfDocument(str(path))
            if page_number > len(doc):
                raise IndexError(f"Page {page_number} does not exist. PDF contains {len(doc)} pages.")
            page = doc[page_number - 1]
            bitmap = page.render(scale=use_dpi / 72.0, rotation=0)
            image = bitmap.to_pil()
            tmp = image_path.with_suffix(".tmp.png")
            image.save(tmp, format="PNG", optimize=True)
            tmp.replace(image_path)
            return image_path
        except Exception as exc:
            raise ValueError(f"Could not render page {page_number} of {path}") from exc
        finally:
            try:
                if image is not None:
                    image.close()
            except Exception:
                pass
            try:
                if bitmap is not None:
                    bitmap.close()
            except Exception:
                pass
            try:
                if page is not None:
                    page.close()
            except Exception:
                pass
            try:
                if doc is not None:
                    doc.close()
            except Exception:
                pass
