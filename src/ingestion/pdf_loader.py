from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import fitz


@dataclass
class PDFPage:
    """Represents one PDF page with native text and basic geometry."""

    page_number: int
    text: str
    width: float
    height: float
    image: Optional[object] = None


@dataclass
class PDFDocument:
    """Represents a loaded PDF document."""

    path: Path
    page_count: int
    pages: list[PDFPage]


class PDFLoader:
    """
    PDF loading and native-text extraction layer.

    Responsibilities:
    - Open PDF files using PyMuPDF.
    - Extract native text page-by-page.
    - Provide basic page metadata.
    - Do not perform OCR.

    OCR is handled separately by the OCR layer.
    """

    def __init__(
        self,
        max_pages: int = 1000,
    ) -> None:

        if isinstance(
            max_pages,
            bool,
        ) or not isinstance(
            max_pages,
            int,
        ):
            raise TypeError(
                "max_pages must be an integer."
            )

        if max_pages < 1:
            raise ValueError(
                "max_pages must be at least 1."
            )

        self.max_pages = max_pages

    # ==================================================================
    # PATH VALIDATION
    # ==================================================================

    @staticmethod
    def _validate_path(
        pdf_path: str | Path,
    ) -> Path:
        """
        Validate and normalize the PDF path.
        """

        path = Path(pdf_path)

        if not path.exists():
            raise FileNotFoundError(
                f"PDF not found: {path}"
            )

        if not path.is_file():
            raise ValueError(
                f"PDF path is not a file: {path}"
            )

        if path.suffix.lower() != ".pdf":
            raise ValueError(
                f"Expected a PDF file: {path}"
            )

        return path

    # ==================================================================
    # DOCUMENT OPENING
    # ==================================================================

    @staticmethod
    def _open_document(
        pdf_path: Path,
    ) -> fitz.Document:
        """
        Open a PDF safely.

        Handles corrupt/unreadable PDFs and password-protected PDFs.
        """

        try:

            document = fitz.open(
                str(pdf_path)
            )

        except Exception as exc:

            raise ValueError(
                f"Could not open PDF: {pdf_path}"
            ) from exc

        # --------------------------------------------------------------
        # Encrypted PDF
        # --------------------------------------------------------------

        if document.is_encrypted:

            try:
                authenticated = (
                    document.authenticate("")
                )

            except Exception:

                document.close()

                raise ValueError(
                    f"Encrypted PDF requires a password: "
                    f"{pdf_path}"
                )

            if not authenticated:

                document.close()

                raise ValueError(
                    f"Encrypted PDF requires a password: "
                    f"{pdf_path}"
                )

        return document

    # ==================================================================
    # PAGE COUNT VALIDATION
    # ==================================================================

    def _validate_page_count(
        self,
        document: fitz.Document,
        pdf_path: Path,
    ) -> int:
        """
        Validate page count against configured limits.
        """

        page_count = len(document)

        if page_count == 0:

            raise ValueError(
                f"PDF contains no pages: {pdf_path}"
            )

        if page_count > self.max_pages:

            raise ValueError(
                f"PDF has {page_count} pages, "
                f"which exceeds MAX_PAGES={self.max_pages}"
            )

        return page_count

    # ==================================================================
    # LOAD
    # ==================================================================

    def load(
        self,
        pdf_path: str | Path,
    ) -> PDFDocument:
        """
        Load a PDF and extract native text from every page.
        """

        pdf_path = self._validate_path(
            pdf_path
        )

        document = self._open_document(
            pdf_path
        )

        try:

            page_count = self._validate_page_count(
                document,
                pdf_path,
            )

            pages: list[PDFPage] = []

            for index, page in enumerate(
                document
            ):

                page_number = index + 1

                try:

                    text = (
                        page.get_text("text")
                        or ""
                    )

                    rect = page.rect

                    pages.append(
                        PDFPage(
                            page_number=page_number,
                            text=text,
                            width=float(
                                rect.width
                            ),
                            height=float(
                                rect.height
                            ),
                        )
                    )

                except Exception as exc:

                    raise ValueError(
                        f"Could not process page "
                        f"{page_number} of PDF: "
                        f"{pdf_path}"
                    ) from exc

            return PDFDocument(
                path=pdf_path,
                page_count=page_count,
                pages=pages,
            )

        finally:

            document.close()

    # ==================================================================
    # ITERATE PAGES
    # ==================================================================

    def iter_pages(
        self,
        pdf_path: str | Path,
    ) -> Iterator[PDFPage]:
        """
        Yield PDF pages one at a time.

        This avoids constructing a PDFDocument containing all page
        records when streaming is preferred.
        """

        pdf_path = self._validate_path(
            pdf_path
        )

        document = self._open_document(
            pdf_path
        )

        try:

            self._validate_page_count(
                document,
                pdf_path,
            )

            for index, page in enumerate(
                document
            ):

                page_number = index + 1

                try:

                    rect = page.rect

                    yield PDFPage(
                        page_number=page_number,
                        text=(
                            page.get_text(
                                "text"
                            )
                            or ""
                        ),
                        width=float(
                            rect.width
                        ),
                        height=float(
                            rect.height
                        ),
                    )

                except Exception as exc:

                    raise ValueError(
                        f"Could not process page "
                        f"{page_number} of PDF: "
                        f"{pdf_path}"
                    ) from exc

        finally:

            document.close()

    # ==================================================================
    # NATIVE TEXT
    # ==================================================================

    def extract_native_text(
        self,
        pdf_path: str | Path,
    ) -> str:
        """
        Return all non-empty native PDF text as one string.

        No OCR is performed here.
        """

        document = self.load(
            pdf_path
        )

        return "\n".join(
            page.text.strip()
            for page in document.pages
            if page.text.strip()
        )

    # ==================================================================
    # PAGE COUNT
    # ==================================================================

    def get_page_count(
        self,
        pdf_path: str | Path,
    ) -> int:
        """
        Return the number of pages in a PDF.
        """

        pdf_path = self._validate_path(
            pdf_path
        )

        document = self._open_document(
            pdf_path
        )

        try:

            return self._validate_page_count(
                document,
                pdf_path,
            )

        finally:

            document.close()

    # ==================================================================
    # PDF INFORMATION
    # ==================================================================

    def get_pdf_info(
        self,
        pdf_path: str | Path,
    ) -> dict:
        """
        Return basic PDF metadata.
        """

        pdf_path = self._validate_path(
            pdf_path
        )

        document = self._open_document(
            pdf_path
        )

        try:

            page_count = self._validate_page_count(
                document,
                pdf_path,
            )

            return {
                "path": str(pdf_path),
                "filename": pdf_path.name,
                "page_count": page_count,
                "metadata": (
                    document.metadata
                    or {}
                ),
            }

        finally:

            document.close()


# ======================================================================
# TESTS
# ======================================================================

def _run_tests() -> None:

    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:

        root = Path(temp_dir)

        # --------------------------------------------------------------
        # Create test PDF
        # --------------------------------------------------------------

        pdf_path = (
            root / "test.pdf"
        )

        document = fitz.open()

        page_one = document.new_page(
            width=612,
            height=792,
        )

        page_one.insert_text(
            (72, 72),
            "Invoice Number: INV-001",
        )

        page_two = document.new_page(
            width=612,
            height=792,
        )

        page_two.insert_text(
            (72, 72),
            "Total: 123.45",
        )

        document.set_metadata(
            {
                "title": "Test Invoice"
            }
        )

        document.save(
            str(pdf_path)
        )

        document.close()

        # --------------------------------------------------------------
        # Loader
        # --------------------------------------------------------------

        loader = PDFLoader(
            max_pages=10
        )

        # --------------------------------------------------------------
        # load()
        # --------------------------------------------------------------

        loaded = loader.load(
            pdf_path
        )

        assert loaded.path == pdf_path
        assert loaded.page_count == 2
        assert len(loaded.pages) == 2

        assert (
            loaded.pages[0].page_number
            == 1
        )

        assert (
            "INV-001"
            in loaded.pages[0].text
        )

        assert (
            loaded.pages[0].width
            == 612.0
        )

        assert (
            loaded.pages[0].height
            == 792.0
        )

        # --------------------------------------------------------------
        # iter_pages()
        # --------------------------------------------------------------

        streamed = list(
            loader.iter_pages(
                pdf_path
            )
        )

        assert len(streamed) == 2

        assert (
            streamed[0].page_number
            == 1
        )

        assert (
            streamed[1].page_number
            == 2
        )

        assert (
            "123.45"
            in streamed[1].text
        )

        # --------------------------------------------------------------
        # extract_native_text()
        # --------------------------------------------------------------

        native_text = (
            loader.extract_native_text(
                pdf_path
            )
        )

        assert (
            "INV-001"
            in native_text
        )

        assert (
            "123.45"
            in native_text
        )

        # --------------------------------------------------------------
        # get_page_count()
        # --------------------------------------------------------------

        assert (
            loader.get_page_count(
                pdf_path
            )
            == 2
        )

        # --------------------------------------------------------------
        # get_pdf_info()
        # --------------------------------------------------------------

        info = loader.get_pdf_info(
            pdf_path
        )

        assert (
            info["filename"]
            == "test.pdf"
        )

        assert (
            info["page_count"]
            == 2
        )

        assert (
            info["metadata"]["title"]
            == "Test Invoice"
        )

        # --------------------------------------------------------------
        # Missing PDF
        # --------------------------------------------------------------

        missing = (
            root / "missing.pdf"
        )

        try:

            loader.load(
                missing
            )

            raise AssertionError(
                "Missing PDF did not raise."
            )

        except FileNotFoundError:
            pass

        # --------------------------------------------------------------
        # Non-PDF
        # --------------------------------------------------------------

        text_file = (
            root / "test.txt"
        )

        text_file.write_text(
            "not a pdf",
            encoding="utf-8",
        )

        try:

            loader.load(
                text_file
            )

            raise AssertionError(
                "Non-PDF did not raise."
            )

        except ValueError:
            pass

        # --------------------------------------------------------------
        # Directory path
        # --------------------------------------------------------------

        try:

            loader.load(
                root
            )

            raise AssertionError(
                "Directory did not raise."
            )

        except ValueError:
            pass

        # --------------------------------------------------------------
        # Invalid max_pages
        # --------------------------------------------------------------

        try:

            PDFLoader(
                max_pages=0
            )

            raise AssertionError(
                "max_pages=0 did not raise."
            )

        except ValueError:
            pass

        try:

            PDFLoader(
                max_pages="10"  # type: ignore
            )

            raise AssertionError(
                "String max_pages did not raise."
            )

        except TypeError:
            pass

        # --------------------------------------------------------------
        # Page limit
        # --------------------------------------------------------------

        limited_loader = PDFLoader(
            max_pages=1
        )

        try:

            limited_loader.load(
                pdf_path
            )

            raise AssertionError(
                "Page limit did not raise."
            )

        except ValueError:
            pass

        # --------------------------------------------------------------
        # Corrupt PDF
        # --------------------------------------------------------------

        corrupt = (
            root / "corrupt.pdf"
        )

        corrupt.write_bytes(
            b"this is not a valid pdf"
        )

        try:

            loader.load(
                corrupt
            )

            raise AssertionError(
                "Corrupt PDF did not raise."
            )

        except ValueError:
            pass

    print(
        "ALL PDF LOADER TESTS PASSED"
    )


if __name__ == "__main__":
    _run_tests()