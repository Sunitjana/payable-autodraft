from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import fitz


@dataclass
class PageRecord:
    """
    Page-level information required by the OCR and document-processing
    pipeline.
    """

    page_number: int
    text: str
    image_path: Optional[Path]
    width: float
    height: float
    source_pdf: Path


class PageProcessor:
    """
    Converts PDF pages into page-level processing records.

    Responsibilities:
        - Extract native PDF text.
        - Render PDF pages to PNG images.
        - Provide page dimensions.
        - Provide stable image paths for OCR.
        - Do not perform OCR itself.

    OCR is handled by src.ocr.extractor.
    """

    def __init__(
        self,
        dpi: int = 200,
        image_output_dir: str | Path = "output/page_images",
    ) -> None:

        if isinstance(dpi, bool):
            raise TypeError(
                "dpi must be an integer."
            )

        if not isinstance(dpi, int):
            raise TypeError(
                "dpi must be an integer."
            )

        if dpi <= 0:
            raise ValueError(
                "dpi must be greater than 0."
            )

        self.dpi = dpi
        self.image_output_dir = Path(
            image_output_dir
        )

    # ==================================================================
    # PATH VALIDATION
    # ==================================================================

    @staticmethod
    def _validate_pdf_path(
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
    # OPEN PDF
    # ==================================================================

    @staticmethod
    def _open_document(
        pdf_path: Path,
    ) -> fitz.Document:
        """
        Open a PDF safely.

        Password-protected PDFs are not supported unless they accept an
        empty password.
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

                authenticated = False

            if not authenticated:

                document.close()

                raise ValueError(
                    "Encrypted PDF requires a password: "
                    f"{pdf_path}"
                )

        return document

    # ==================================================================
    # PAGE VALIDATION
    # ==================================================================

    @staticmethod
    def _validate_document(
        document: fitz.Document,
        pdf_path: Path,
    ) -> None:
        """
        Ensure the PDF contains at least one page.
        """

        if len(document) == 0:

            document.close()

            raise ValueError(
                f"PDF contains no pages: {pdf_path}"
            )

    # ==================================================================
    # IMAGE PATH
    # ==================================================================

    def _get_image_path(
        self,
        pdf_path: Path,
        page_number: int,
    ) -> Path:
        """
        Return the deterministic output path for a rendered page.
        """

        return (
            self.image_output_dir
            / (
                f"{pdf_path.stem}"
                f"_page_{page_number}.png"
            )
        )

    # ==================================================================
    # RENDER PAGE
    # ==================================================================

    def _render_page(
        self,
        page: fitz.Page,
        image_path: Path,
    ) -> None:
        """
        Render a PDF page to PNG.

        A temporary PNG is written first and then renamed into place.
        """

        self.image_output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        scale = (
            self.dpi / 72.0
        )

        matrix = fitz.Matrix(
            scale,
            scale,
        )

        try:

            pixmap = page.get_pixmap(
                matrix=matrix,
                alpha=False,
            )

            # Keep .png as the final suffix so PyMuPDF can determine
            # the image format correctly.
            temporary_path = image_path.with_name(
                image_path.name
                + ".tmp.png"
            )

            try:

                pixmap.save(
                    str(temporary_path)
                )

                temporary_path.replace(
                    image_path
                )

            finally:

                if temporary_path.exists():

                    try:
                        temporary_path.unlink()
                    except OSError:
                        pass

        except Exception as exc:

            raise ValueError(
                f"Could not render PDF page to image: "
                f"{image_path}"
            ) from exc

    # ==================================================================
    # ENSURE IMAGE
    # ==================================================================

    def _ensure_image(
        self,
        page: fitz.Page,
        image_path: Path,
    ) -> Path:
        """
        Ensure a usable page image exists.

        Existing zero-byte images are regenerated.
        """

        if (
            image_path.exists()
            and image_path.is_file()
            and image_path.stat().st_size > 0
        ):
            return image_path

        self._render_page(
            page,
            image_path,
        )

        if (
            not image_path.exists()
            or image_path.stat().st_size == 0
        ):

            raise ValueError(
                f"Rendered page image is empty: "
                f"{image_path}"
            )

        return image_path

    # ==================================================================
    # BUILD PAGE RECORD
    # ==================================================================

    def _build_page_record(
        self,
        page: fitz.Page,
        page_number: int,
        pdf_path: Path,
    ) -> PageRecord:
        """
        Convert a PyMuPDF page into a PageRecord.
        """

        try:

            text = (
                page.get_text(
                    "text"
                )
                or ""
            )

            rect = page.rect

            image_path = (
                self._get_image_path(
                    pdf_path,
                    page_number,
                )
            )

            self._ensure_image(
                page,
                image_path,
            )

            return PageRecord(
                page_number=page_number,
                text=text,
                image_path=image_path,
                width=float(
                    rect.width
                ),
                height=float(
                    rect.height
                ),
                source_pdf=pdf_path,
            )

        except ValueError:
            raise

        except Exception as exc:

            raise ValueError(
                f"Could not process page "
                f"{page_number} of PDF: "
                f"{pdf_path}"
            ) from exc

    # ==================================================================
    # PROCESS COMPLETE PDF
    # ==================================================================

    def process(
        self,
        pdf_path: str | Path,
    ) -> list[PageRecord]:
        """
        Render all pages and return page-level records.
        """

        pdf_path = self._validate_pdf_path(
            pdf_path
        )

        document = self._open_document(
            pdf_path
        )

        try:

            self._validate_document(
                document,
                pdf_path,
            )

            records: list[PageRecord] = []

            for index, page in enumerate(
                document
            ):

                page_number = (
                    index + 1
                )

                records.append(
                    self._build_page_record(
                        page,
                        page_number,
                        pdf_path,
                    )
                )

            return records

        finally:

            if not document.is_closed:
                document.close()

    # ==================================================================
    # PROCESS SINGLE PAGE
    # ==================================================================

    def process_page(
        self,
        pdf_path: str | Path,
        page_number: int,
    ) -> PageRecord:
        """
        Process one 1-based PDF page.

        Unlike the previous implementation, this does not render every
        page before selecting the requested page.
        """

        if isinstance(
            page_number,
            bool,
        ):

            raise TypeError(
                "page_number must be an integer."
            )

        if not isinstance(
            page_number,
            int,
        ):

            raise TypeError(
                "page_number must be an integer."
            )

        if page_number < 1:

            raise ValueError(
                "page_number must be 1 or greater."
            )

        pdf_path = self._validate_pdf_path(
            pdf_path
        )

        document = self._open_document(
            pdf_path
        )

        try:

            self._validate_document(
                document,
                pdf_path,
            )

            page_count = len(
                document
            )

            if page_number > page_count:

                raise IndexError(
                    f"Page {page_number} does not exist. "
                    f"PDF contains {page_count} pages."
                )

            page = document.load_page(
                page_number - 1
            )

            return self._build_page_record(
                page,
                page_number,
                pdf_path,
            )

        finally:

            if not document.is_closed:
                document.close()


# ======================================================================
# TESTS
# ======================================================================

def _run_tests() -> None:

    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:

        root = Path(
            temp_dir
        )

        pdf_path = (
            root / "invoice.pdf"
        )

        output_dir = (
            root / "page_images"
        )

        # --------------------------------------------------------------
        # Create test PDF
        # --------------------------------------------------------------

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
            "Total Amount: 123.45",
        )

        document.save(
            str(pdf_path)
        )

        document.close()

        # --------------------------------------------------------------
        # Processor
        # --------------------------------------------------------------

        processor = PageProcessor(
            dpi=100,
            image_output_dir=output_dir,
        )

        # --------------------------------------------------------------
        # process()
        # --------------------------------------------------------------

        records = processor.process(
            pdf_path
        )

        assert len(records) == 2

        assert (
            records[0].page_number
            == 1
        )

        assert (
            records[1].page_number
            == 2
        )

        assert (
            "INV-001"
            in records[0].text
        )

        assert (
            "123.45"
            in records[1].text
        )

        assert (
            records[0].width
            == 612.0
        )

        assert (
            records[0].height
            == 792.0
        )

        # --------------------------------------------------------------
        # Images
        # --------------------------------------------------------------

        for record in records:

            assert (
                record.image_path
                is not None
            )

            assert (
                record.image_path.exists()
            )

            assert (
                record.image_path.stat().st_size
                > 0
            )

            assert (
                record.source_pdf
                == pdf_path
            )

        # --------------------------------------------------------------
        # process() should reuse valid images
        # --------------------------------------------------------------

        first_image = (
            records[0].image_path
        )

        assert first_image is not None

        original_mtime = (
            first_image.stat().st_mtime_ns
        )

        records_again = processor.process(
            pdf_path
        )

        assert (
            records_again[0].image_path
            == first_image
        )

        assert (
            first_image.stat().st_mtime_ns
            == original_mtime
        )

        # --------------------------------------------------------------
        # Zero-byte image should be regenerated
        # --------------------------------------------------------------

        first_image.write_bytes(
            b""
        )

        assert (
            first_image.stat().st_size
            == 0
        )

        regenerated = processor.process(
            pdf_path
        )

        assert (
            regenerated[0]
            .image_path
            .stat()
            .st_size
            > 0
        )

        # --------------------------------------------------------------
        # process_page()
        # --------------------------------------------------------------

        page_two_record = (
            processor.process_page(
                pdf_path,
                2,
            )
        )

        assert (
            page_two_record.page_number
            == 2
        )

        assert (
            "123.45"
            in page_two_record.text
        )

        assert (
            page_two_record.image_path
            is not None
        )

        assert (
            page_two_record.image_path.exists()
        )

        # --------------------------------------------------------------
        # Invalid page number
        # --------------------------------------------------------------

        try:

            processor.process_page(
                pdf_path,
                0,
            )

            raise AssertionError(
                "page_number=0 did not raise."
            )

        except ValueError:
            pass

        try:

            processor.process_page(
                pdf_path,
                3,
            )

            raise AssertionError(
                "Out-of-range page did not raise."
            )

        except IndexError:
            pass

        try:

            processor.process_page(
                pdf_path,
                "1",  # type: ignore
            )

            raise AssertionError(
                "String page_number did not raise."
            )

        except TypeError:
            pass

        # --------------------------------------------------------------
        # Missing PDF
        # --------------------------------------------------------------

        try:

            processor.process(
                root / "missing.pdf"
            )

            raise AssertionError(
                "Missing PDF did not raise."
            )

        except FileNotFoundError:
            pass

        # --------------------------------------------------------------
        # Directory
        # --------------------------------------------------------------

        try:

            processor.process(
                root
            )

            raise AssertionError(
                "Directory did not raise."
            )

        except ValueError:
            pass

        # --------------------------------------------------------------
        # Non-PDF
        # --------------------------------------------------------------

        text_file = (
            root / "test.txt"
        )

        text_file.write_text(
            "not a PDF",
            encoding="utf-8",
        )

        try:

            processor.process(
                text_file
            )

            raise AssertionError(
                "Non-PDF did not raise."
            )

        except ValueError:
            pass

        # --------------------------------------------------------------
        # Invalid DPI
        # --------------------------------------------------------------

        try:

            PageProcessor(
                dpi=0
            )

            raise AssertionError(
                "dpi=0 did not raise."
            )

        except ValueError:
            pass

        try:

            PageProcessor(
                dpi="200",  # type: ignore
            )

            raise AssertionError(
                "String dpi did not raise."
            )

        except TypeError:
            pass

        # --------------------------------------------------------------
        # Corrupt PDF
        # --------------------------------------------------------------

        corrupt_pdf = (
            root / "corrupt.pdf"
        )

        corrupt_pdf.write_bytes(
            b"this is not a valid PDF"
        )

        try:

            processor.process(
                corrupt_pdf
            )

            raise AssertionError(
                "Corrupt PDF did not raise."
            )

        except ValueError:
            pass

    print(
        "ALL PAGE PROCESSOR TESTS PASSED"
    )


if __name__ == "__main__":
    _run_tests()