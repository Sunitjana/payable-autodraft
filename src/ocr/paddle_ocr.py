"""
PaddleOCR-VL 1.6 OCR backend.

Responsibilities:
    - Load PaddleOCR-VL lazily
    - Process images and PDFs
    - Extract OCR text
    - Preserve structured document information
    - Preserve Markdown/table information
    - Extract recognition confidence when explicitly available
    - Keep layout confidence separate from OCR confidence
    - Save normalized results
    - Never run inference twice during saving
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


logger = logging.getLogger(__name__)


# ============================================================
# Result object
# ============================================================


@dataclass
class PaddleOCRResult:
    """
    Normalized PaddleOCR-VL result.

    Important:
        confidence is OPTIONAL.

        PaddleOCR-VL document-parser output does not always expose
        token/character recognition confidence. We must never fabricate
        a confidence value when it is unavailable.
    """

    text: str = ""

    markdown: str = ""

    structured: Dict[str, Any] = field(
        default_factory=dict
    )

    # --------------------------------------------------------
    # Recognition confidence
    # --------------------------------------------------------

    confidence: Optional[float] = None

    confidence_available: bool = False

    confidence_source: Optional[str] = None

    # --------------------------------------------------------
    # Layout confidence
    # --------------------------------------------------------

    # IMPORTANT:
    # This is NOT OCR recognition confidence.
    #
    # It represents the confidence of layout detection boxes.
    layout_confidence: Optional[float] = None

    # --------------------------------------------------------
    # Metadata
    # --------------------------------------------------------

    source: Optional[str] = None

    page_number: Optional[int] = None

    success: bool = False

    error: Optional[str] = None

    # Original Paddle result.
    # Not serialized because it may not be JSON serializable.
    raw_result: Any = None

    @property
    def has_text(self) -> bool:
        """Return True when OCR text is available."""
        return bool(self.text.strip())

    @property
    def text_length(self) -> int:
        """Return number of non-whitespace text characters."""
        return len(self.text.strip())

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert normalized result to a JSON-safe dictionary.
        """

        return {
            "text": self.text,
            "markdown": self.markdown,
            "structured": self.structured,

            # Recognition confidence
            "confidence": self.confidence,
            "confidence_available": self.confidence_available,
            "confidence_source": self.confidence_source,

            # Layout confidence is deliberately separate.
            "layout_confidence": self.layout_confidence,

            "source": self.source,
            "page_number": self.page_number,
            "success": self.success,
            "error": self.error,
        }


# ============================================================
# PaddleOCR-VL backend
# ============================================================


class PaddleOCRBackend:
    """
    PaddleOCR-VL 1.6 backend.

    The model is loaded lazily when the first extraction request
    is made.
    """

    def __init__(
        self,
        pipeline_version: str = "v1.6",
        device: Optional[str] = None,
    ) -> None:

        self.pipeline_version = pipeline_version
        self.device = device

        self._pipeline = None

    # ========================================================
    # Load model
    # ========================================================

    def _load_pipeline(self) -> None:
        """
        Load PaddleOCR-VL lazily.
        """

        if self._pipeline is not None:
            return

        try:
            from paddleocr import PaddleOCRVL

        except ImportError as exc:
            raise RuntimeError(
                "PaddleOCR is not installed.\n\n"
                "Install PaddleOCR and the appropriate "
                "PaddlePaddle package for your system."
            ) from exc

        logger.info(
            "Loading PaddleOCR-VL %s...",
            self.pipeline_version,
        )

        kwargs: Dict[str, Any] = {
            "pipeline_version": self.pipeline_version,
        }

        # Always pass an explicit device rather than omitting it.
        # Leaving "device" unset lets PaddleOCR/PaddleX fall back to
        # their own auto-detection, which raises a bare
        # AssertionError in some environments (observed: PaddleX's
        # parse_device() rejecting whatever it auto-selects when no
        # PaddlePaddle GPU build is installed). Defaulting to "cpu"
        # is always a valid, supported device string and matches
        # what most installs actually have available; a real GPU
        # can still be requested explicitly via PADDLE_DEVICE.
        kwargs["device"] = self.device or "cpu"

        try:
            self._pipeline = PaddleOCRVL(
                **kwargs
            )

        except (TypeError, AssertionError) as exc:

            # Some PaddleOCR installations don't accept a "device"
            # kwarg at all (TypeError), and some reject even an
            # explicit "cpu" (AssertionError from PaddleX's own
            # device parsing, depending on how it was built). Either
            # way, retry once without specifying a device and let
            # the installed library use whatever default it can
            # actually support.
            if "device" in kwargs:

                logger.warning(
                    "PaddleOCRVL did not accept "
                    "device=%s (%s). Retrying "
                    "without an explicit device.",
                    kwargs["device"],
                    type(exc).__name__,
                )

                kwargs.pop("device", None)

                self._pipeline = PaddleOCRVL(
                    **kwargs
                )

            else:
                raise exc

        logger.info(
            "PaddleOCR-VL %s loaded successfully.",
            self.pipeline_version,
        )

    # ========================================================
    # Availability
    # ========================================================

    @staticmethod
    def is_available() -> bool:
        """
        Check whether PaddleOCR-VL can be imported.
        """

        try:
            from paddleocr import PaddleOCRVL

            _ = PaddleOCRVL

            return True

        except ImportError:
            return False

    # ========================================================
    # Text cleanup
    # ========================================================

    @staticmethod
    def _clean_text(
        text: Any,
    ) -> str:
        """
        Basic OCR text cleanup.

        Important:
            OCR text is NOT translated, rewritten,
            normalized semantically, or corrected.
        """

        if text is None:
            return ""

        text = str(text)

        text = text.replace(
            "\x00",
            " ",
        )

        text = text.replace(
            "\r\n",
            "\n",
        )

        text = text.replace(
            "\r",
            "\n",
        )

        return text.strip()

    # ========================================================
    # Convert native Paddle result to dictionary
    # ========================================================

    @staticmethod
    def _get_json(
        result: Any,
    ) -> Dict[str, Any]:
        """
        Obtain the native PaddleOCR JSON result.

        PaddleOCR result objects expose a `json` property.
        """

        try:

            data = result.json

            if callable(data):
                data = data()

            if isinstance(data, dict):
                return data

        except Exception as exc:

            logger.debug(
                "Unable to read PaddleOCR result.json: %s",
                exc,
            )

        return {}

    # ========================================================
    # Collect text recursively
    # ========================================================

    @classmethod
    def _collect_text(
        cls,
        value: Any,
        output: List[str],
    ) -> None:
        """
        Recursively collect text from common PaddleOCR
        structures.
        """

        if value is None:
            return

        # ----------------------------------------------------
        # String
        # ----------------------------------------------------

        if isinstance(value, str):

            text = cls._clean_text(
                value
            )

            if text:
                output.append(text)

            return

        # ----------------------------------------------------
        # List / tuple
        # ----------------------------------------------------

        if isinstance(
            value,
            (list, tuple),
        ):

            for item in value:

                cls._collect_text(
                    item,
                    output,
                )

            return

        # ----------------------------------------------------
        # Dictionary
        # ----------------------------------------------------

        if isinstance(value, dict):

            # Only collect known text fields.
            for key in (
                "text",
                "content",
                "rec_text",
                "rec_texts",
                "recognized_text",
                "ocr_text",
                "markdown_text",
                "markdown",
                "block_content",
                "transcription",
            ):

                if key in value:

                    cls._collect_text(
                        value[key],
                        output,
                    )

            return

    # ========================================================
    # Extract recognized text
    # ========================================================

    @classmethod
    def _extract_text(
        cls,
        result: Any,
    ) -> str:
        """
        Extract recognized text from PaddleOCR-VL output.
        """

        data = cls._get_json(
            result
        )

        texts: List[str] = []

        def walk(
            value: Any,
        ) -> None:

            if value is None:
                return

            # ------------------------------------------------
            # Dictionary
            # ------------------------------------------------

            if isinstance(
                value,
                dict,
            ):

                for key, item in value.items():

                    key_lower = str(
                        key
                    ).lower()

                    if key_lower in {
                        "rec_texts",
                        "rec_text",
                        "recognized_text",
                        "ocr_text",
                        "text",
                        "content",
                        "markdown_text",
                        "markdown",
                        "block_content",
                        "transcription",
                    }:

                        cls._collect_text(
                            item,
                            texts,
                        )

                        continue

                    walk(item)

                return

            # ------------------------------------------------
            # List / tuple
            # ------------------------------------------------

            if isinstance(
                value,
                (list, tuple),
            ):

                for item in value:

                    walk(item)

        walk(data)

        # ----------------------------------------------------
        # Remove duplicates
        # ----------------------------------------------------

        unique_texts: List[str] = []

        seen = set()

        for text in texts:

            text = cls._clean_text(
                text
            )

            if not text:
                continue

            if text in seen:
                continue

            seen.add(text)

            unique_texts.append(
                text
            )

        return "\n".join(
            unique_texts
        )

    # ========================================================
    # Extract Markdown
    # ========================================================

    @classmethod
    def _extract_markdown(
        cls,
        result: Any,
    ) -> str:
        """
        Extract Markdown/table information from PaddleOCR-VL.
        """

        try:

            markdown = result.markdown

            if callable(markdown):
                markdown = markdown()

            # -----------------------------------------------
            # Dictionary
            # -----------------------------------------------

            if isinstance(
                markdown,
                dict,
            ):

                markdown_texts = markdown.get(
                    "markdown_texts"
                )

                if isinstance(
                    markdown_texts,
                    str,
                ):

                    return cls._clean_text(
                        markdown_texts
                    )

                if isinstance(
                    markdown_texts,
                    list,
                ):

                    return "\n\n".join(
                        cls._clean_text(x)
                        for x in markdown_texts
                        if x
                    )

            # -----------------------------------------------
            # String
            # -----------------------------------------------

            if isinstance(
                markdown,
                str,
            ):

                return cls._clean_text(
                    markdown
                )

        except Exception as exc:

            logger.debug(
                "Unable to read PaddleOCR Markdown: %s",
                exc,
            )

        return ""

    # ========================================================
    # Recognition confidence
    # ========================================================

    @classmethod
    def _extract_confidence(
        cls,
        result: Any,
    ) -> Tuple[
        Optional[float],
        Optional[str],
    ]:
        """
        Extract recognition confidence ONLY when PaddleOCR-VL
        explicitly exposes it.

        Returns:
            (confidence, source)

        Example:

            (0.9472, "explicit_recognition_score")

        If recognition confidence is unavailable:

            (None, None)

        IMPORTANT:
            Never use layout detection confidence as OCR
            recognition confidence.

            Never use arbitrary numbers from the invoice.

            Never convert missing confidence into 0.0.
        """

        data = cls._get_json(
            result
        )

        scores: List[float] = []

        # ----------------------------------------------------
        # ONLY these fields are allowed to represent OCR
        # recognition confidence.
        # ----------------------------------------------------

        confidence_keys = {
            "rec_scores",
            "rec_score",
            "rec_confidences",
            "rec_confidence",

            "recognition_scores",
            "recognition_score",
            "recognition_confidences",
            "recognition_confidence",

            "ocr_scores",
            "ocr_score",
            "ocr_confidences",
            "ocr_confidence",

            "text_scores",
            "text_score",

            "confidence",
        }

        # ----------------------------------------------------
        # Collect score values
        # ----------------------------------------------------

        def collect(
            value: Any,
        ) -> None:

            if value is None:
                return

            # Single numeric score
            if isinstance(
                value,
                (int, float),
            ) and not isinstance(
                value,
                bool,
            ):

                score = float(
                    value
                )

                if 0.0 <= score <= 1.0:
                    scores.append(
                        score
                    )

                return

            # List of scores
            if isinstance(
                value,
                (list, tuple),
            ):

                for item in value:

                    collect(item)

                return

            # Dictionary containing score values
            if isinstance(
                value,
                dict,
            ):

                for key, item in value.items():

                    if str(
                        key
                    ).lower() in confidence_keys:

                        collect(item)

                    elif isinstance(
                        item,
                        dict,
                    ):

                        walk(item)

        # ----------------------------------------------------
        # Walk result
        # ----------------------------------------------------

        def walk(
            value: Any,
        ) -> None:

            if isinstance(
                value,
                dict,
            ):

                for key, item in value.items():

                    key_lower = str(
                        key
                    ).lower()

                    if key_lower in confidence_keys:

                        collect(item)

                    elif isinstance(
                        item,
                        dict,
                    ):

                        walk(item)

                    elif isinstance(
                        item,
                        list,
                    ):

                        # IMPORTANT:
                        #
                        # Do not recursively scan arbitrary
                        # lists because they may contain:
                        #
                        # invoice numbers
                        # quantities
                        # prices
                        # tax amounts
                        #
                        # Only confidence-related lists are
                        # handled by collect().
                        continue

            elif isinstance(
                value,
                list,
            ):

                for item in value:

                    if isinstance(
                        item,
                        dict,
                    ):

                        walk(item)

        walk(data)

        # ----------------------------------------------------
        # No recognition score available
        # ----------------------------------------------------

        if not scores:

            return (
                None,
                None,
            )

        # ----------------------------------------------------
        # Mean recognition confidence
        # ----------------------------------------------------

        confidence = round(
            sum(scores) / len(scores),
            4,
        )

        return (
            confidence,
            "explicit_recognition_score",
        )

    # ========================================================
    # Layout confidence
    # ========================================================

    @classmethod
    def _extract_layout_confidence(
        cls,
        result: Any,
    ) -> Optional[float]:
        """
        Calculate mean layout-detection confidence.

        IMPORTANT:
            This metric is separate from OCR recognition
            confidence.

        It must NEVER be reported as OCR confidence.
        """

        data = cls._get_json(
            result
        )

        # ----------------------------------------------------
        # Find layout boxes
        # ----------------------------------------------------

        def find_layout_boxes(
            value: Any,
        ) -> Optional[Any]:

            if isinstance(
                value,
                dict,
            ):

                for key, item in value.items():

                    if (
                        str(key).lower()
                        == "layout_det_res"
                        and isinstance(
                            item,
                            dict,
                        )
                    ):

                        candidate = item.get(
                            "boxes"
                        )

                        if isinstance(
                            candidate,
                            list,
                        ):

                            return candidate

                    found = find_layout_boxes(
                        item
                    )

                    if found is not None:
                        return found

            elif isinstance(
                value,
                list,
            ):

                for item in value:

                    found = find_layout_boxes(
                        item
                    )

                    if found is not None:
                        return found

            return None

        boxes = find_layout_boxes(
            data
        )

        if not boxes:
            return None

        # ----------------------------------------------------
        # Extract layout scores
        # ----------------------------------------------------

        scores: List[float] = []

        for box in boxes:

            if not isinstance(
                box,
                dict,
            ):
                continue

            score = box.get(
                "score"
            )

            if isinstance(
                score,
                (int, float),
            ) and not isinstance(
                score,
                bool,
            ):

                score = float(
                    score
                )

                if 0.0 <= score <= 1.0:

                    scores.append(
                        score
                    )

        if not scores:
            return None

        return round(
            sum(scores) / len(scores),
            4,
        )

    # ========================================================
    # Normalize result
    # ========================================================

    def _normalize_result(
        self,
        result: Any,
        source: Union[str, Path],
        page_number: Optional[int] = None,
    ) -> PaddleOCRResult:
        """
        Convert native PaddleOCR result into our normalized
        application result.
        """

        structured = self._get_json(
            result
        )

        # ----------------------------------------------------
        # Text
        # ----------------------------------------------------

        text = self._extract_text(
            result
        )

        # ----------------------------------------------------
        # Markdown
        # ----------------------------------------------------

        markdown = self._extract_markdown(
            result
        )

        # ----------------------------------------------------
        # Markdown fallback
        # ----------------------------------------------------

        if not text and markdown:

            text = markdown

        # ----------------------------------------------------
        # Recognition confidence
        # ----------------------------------------------------

        confidence, confidence_source = (
            self._extract_confidence(
                result
            )
        )

        # ----------------------------------------------------
        # Layout confidence
        # ----------------------------------------------------

        layout_confidence = (
            self._extract_layout_confidence(
                result
            )
        )

        # ----------------------------------------------------
        # Success
        # ----------------------------------------------------

        success = bool(
            text.strip()
            or structured
        )

        return PaddleOCRResult(

            text=text,

            markdown=markdown,

            structured=structured,

            confidence=confidence,

            confidence_available=(
                confidence is not None
            ),

            confidence_source=(
                confidence_source
            ),

            layout_confidence=(
                layout_confidence
            ),

            source=str(source),

            page_number=page_number,

            success=success,

            error=None,

            raw_result=result,
        )

    # ========================================================
    # Extract single image / PDF
    # ========================================================

    def extract(
        self,
        input_path: Union[str, Path],
        page_number: Optional[int] = None,
    ) -> PaddleOCRResult:
        """
        Process one image or PDF.

        For multi-page PDFs where page-level results are required,
        use extract_document().
        """

        input_path = Path(
            input_path
        )

        # ----------------------------------------------------
        # File check
        # ----------------------------------------------------

        if not input_path.exists():

            return PaddleOCRResult(

                source=str(
                    input_path
                ),

                page_number=page_number,

                success=False,

                error=(
                    f"Input file does not exist: "
                    f"{input_path}"
                ),
            )

        try:

            self._load_pipeline()

            logger.info(
                "PaddleOCR-VL processing: %s",
                input_path,
            )

            # ------------------------------------------------
            # SINGLE inference
            # ------------------------------------------------

            output = self._pipeline.predict(
                str(input_path)
            )

            results = list(
                output
            )

            # ------------------------------------------------
            # No results
            # ------------------------------------------------

            if not results:

                return PaddleOCRResult(

                    source=str(
                        input_path
                    ),

                    page_number=page_number,

                    success=False,

                    error=(
                        "PaddleOCR-VL returned "
                        "no results."
                    ),
                )

            # ------------------------------------------------
            # Single result
            # ------------------------------------------------

            if len(results) == 1:

                return self._normalize_result(

                    result=results[0],

                    source=input_path,

                    page_number=page_number,
                )

            # ------------------------------------------------
            # Multiple results
            # ------------------------------------------------

            normalized = [

                self._normalize_result(

                    result=res,

                    source=input_path,

                    page_number=(
                        page_number
                        if page_number is not None
                        else index + 1
                    ),
                )

                for index, res in enumerate(
                    results
                )
            ]

            return self._merge_results(
                normalized,
                input_path,
            )

        except Exception as exc:

            logger.exception(
                "PaddleOCR-VL failed: %s",
                input_path,
            )

            return PaddleOCRResult(

                source=str(
                    input_path
                ),

                page_number=page_number,

                success=False,

                error=str(exc),
            )

    # ========================================================
    # Extract complete document
    # ========================================================

    def extract_document(
        self,
        input_path: Union[str, Path],
        restructure_pages: bool = True,
        merge_tables: bool = True,
        relevel_titles: bool = False,
        concatenate_pages: bool = False,
    ) -> List[PaddleOCRResult]:
        """
        Process a complete PDF/document.

        PaddleOCR-VL processes PDF pages individually.
        """

        input_path = Path(
            input_path
        )

        # ----------------------------------------------------
        # File check
        # ----------------------------------------------------

        if not input_path.exists():

            return [

                PaddleOCRResult(

                    source=str(
                        input_path
                    ),

                    success=False,

                    error=(
                        f"Input file does not exist: "
                        f"{input_path}"
                    ),
                )
            ]

        try:

            self._load_pipeline()

            logger.info(
                "PaddleOCR-VL processing document: %s",
                input_path,
            )

            # ------------------------------------------------
            # SINGLE document inference
            # ------------------------------------------------

            output = self._pipeline.predict(
                str(input_path)
            )

            pages = list(
                output
            )

            # ------------------------------------------------
            # No page results
            # ------------------------------------------------

            if not pages:

                return [

                    PaddleOCRResult(

                        source=str(
                            input_path
                        ),

                        success=False,

                        error=(
                            "PaddleOCR-VL returned "
                            "no page results."
                        ),
                    )
                ]

            # ------------------------------------------------
            # Cross-page restructuring
            # ------------------------------------------------

            if (
                restructure_pages
                and hasattr(
                    self._pipeline,
                    "restructure_pages",
                )
            ):

                try:

                    pages = (
                        self._pipeline.restructure_pages(
                            pages,
                            merge_tables=merge_tables,
                            relevel_titles=relevel_titles,
                            concatenate_pages=concatenate_pages,
                        )
                    )

                except TypeError:

                    # Compatibility fallback.
                    try:

                        pages = (
                            self._pipeline.restructure_pages(
                                pages,
                                merge_tables=merge_tables,
                            )
                        )

                    except Exception as exc:

                        logger.warning(
                            "Could not restructure pages: %s",
                            exc,
                        )

                except Exception as exc:

                    logger.warning(
                        "Could not restructure pages: %s",
                        exc,
                    )

            # ------------------------------------------------
            # Normalize pages
            # ------------------------------------------------

            normalized: List[
                PaddleOCRResult
            ] = []

            for index, page in enumerate(
                pages
            ):

                normalized.append(

                    self._normalize_result(

                        result=page,

                        source=input_path,

                        page_number=index + 1,
                    )
                )

            return normalized

        except Exception as exc:

            logger.exception(
                "PaddleOCR-VL document processing failed: %s",
                input_path,
            )

            return [

                PaddleOCRResult(

                    source=str(
                        input_path
                    ),

                    success=False,

                    error=str(exc),
                )
            ]

    # ========================================================
    # Merge results
    # ========================================================

    @staticmethod
    def _merge_results(
        results: List[PaddleOCRResult],
        source: Union[str, Path],
    ) -> PaddleOCRResult:
        """
        Merge multiple normalized results.

        Recognition confidence remains unavailable if none of
        the individual pages exposes an explicit recognition
        confidence.
        """

        text_parts: List[str] = []

        markdown_parts: List[str] = []

        structured_pages: List[
            Dict[str, Any]
        ] = []

        confidences: List[float] = []

        layout_confidences: List[float] = []

        all_success = True

        errors: List[str] = []

        # ----------------------------------------------------
        # Process every page
        # ----------------------------------------------------

        for result in results:

            if result.text:

                text_parts.append(
                    result.text
                )

            if result.markdown:

                markdown_parts.append(
                    result.markdown
                )

            structured_pages.append(
                result.structured
            )

            # ------------------------------------------------
            # IMPORTANT:
            #
            # confidence can be None.
            # Never compare None > 0.
            # ------------------------------------------------

            if result.confidence is not None:

                confidences.append(
                    float(
                        result.confidence
                    )
                )

            # ------------------------------------------------
            # Layout confidence
            # ------------------------------------------------

            if result.layout_confidence is not None:

                layout_confidences.append(
                    float(
                        result.layout_confidence
                    )
                )

            # ------------------------------------------------
            # Status
            # ------------------------------------------------

            if not result.success:

                all_success = False

            if result.error:

                errors.append(
                    result.error
                )

        # ----------------------------------------------------
        # Recognition confidence
        # ----------------------------------------------------

        if confidences:

            confidence: Optional[float] = round(
                sum(confidences)
                / len(confidences),
                4,
            )

            confidence_available = True

            confidence_source = (
                "explicit_recognition_score"
            )

        else:

            # IMPORTANT:
            #
            # Do NOT use 0.0 here.
            #
            # 0.0 means "the model explicitly reported zero",
            # while None means "the model did not provide the
            # recognition confidence".
            confidence = None

            confidence_available = False

            confidence_source = None

        # ----------------------------------------------------
        # Layout confidence
        # ----------------------------------------------------

        if layout_confidences:

            layout_confidence: Optional[float] = round(
                sum(layout_confidences)
                / len(layout_confidences),
                4,
            )

        else:

            layout_confidence = None

        # ----------------------------------------------------
        # Return merged result
        # ----------------------------------------------------

        return PaddleOCRResult(

            text="\n\n".join(
                text_parts
            ),

            markdown="\n\n".join(
                markdown_parts
            ),

            structured={
                "pages": structured_pages
            },

            confidence=confidence,

            confidence_available=(
                confidence_available
            ),

            confidence_source=(
                confidence_source
            ),

            layout_confidence=(
                layout_confidence
            ),

            source=str(source),

            page_number=None,

            success=(
                bool(
                    text_parts
                    or structured_pages
                )
                and all_success
            ),

            error=(
                "; ".join(errors)
                if errors
                else None
            ),
        )

    # ========================================================
    # Save normalized results
    # ========================================================

    def save_normalized_results(
        self,
        results: List[PaddleOCRResult],
        input_path: Union[str, Path],
        output_dir: Union[str, Path] = "output/paddle_ocr",
    ) -> Dict[str, Path]:
        """
        Save already-computed PaddleOCR-VL results.

        IMPORTANT:

            This method does NOT call predict().

            OCR inference has already happened in
            extract_document() / extract().

            Calling predict() again would run the model twice.
        """

        input_path = Path(
            input_path
        )

        output_dir = Path(
            output_dir
        )

        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        json_path = (
            output_dir
            / f"{input_path.stem}.json"
        )

        markdown_path = (
            output_dir
            / f"{input_path.stem}.md"
        )

        # ----------------------------------------------------
        # JSON
        # ----------------------------------------------------

        payload = {

            "document": input_path.name,

            "source": str(
                input_path
            ),

            "pipeline": "PaddleOCR-VL",

            "pipeline_version": (
                self.pipeline_version
            ),

            "page_count": len(
                results
            ),

            "pages": [
                result.to_dict()
                for result in results
            ],
        }

        json_path.write_text(

            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),

            encoding="utf-8",
        )

        # ----------------------------------------------------
        # Markdown
        # ----------------------------------------------------

        markdown_parts: List[str] = []

        for result in results:

            if result.markdown:

                markdown_parts.append(
                    f"## Page {result.page_number}\n\n"
                    f"{result.markdown}"
                )

            elif result.text:

                markdown_parts.append(
                    f"## Page {result.page_number}\n\n"
                    f"{result.text}"
                )

        markdown_path.write_text(

            "\n\n".join(
                markdown_parts
            ),

            encoding="utf-8",
        )

        logger.info(
            "Saved normalized PaddleOCR JSON: %s",
            json_path,
        )

        logger.info(
            "Saved normalized PaddleOCR Markdown: %s",
            markdown_path,
        )

        return {

            "json": json_path,

            "markdown": markdown_path,
        }

    # ========================================================
    # Unload model
    # ========================================================

    def unload(self) -> None:
        """
        Release the PaddleOCR-VL pipeline reference.
        """

        self._pipeline = None

        logger.info(
            "PaddleOCR-VL pipeline released."
        )


# ============================================================
# Command-line test
# ============================================================


def main() -> None:
    """
    Standalone PaddleOCR-VL test.

    Usage:

        python -m src.ocr.paddle_ocr documents/invoice.png

    or:

        python -m src.ocr.paddle_ocr documents/invoice.pdf
    """

    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Run PaddleOCR-VL 1.6 "
            "on an image or PDF."
        )
    )

    parser.add_argument(
        "input",
        help="Path to image or PDF.",
    )

    parser.add_argument(
        "--output",
        default="output/paddle_ocr",
        help="Directory for OCR outputs.",
    )

    parser.add_argument(
        "--device",
        default=None,
        help=(
            "Optional Paddle device, "
            "for example gpu:0 or cpu."
        ),
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(name)s | "
            "%(message)s"
        ),
    )

    backend = PaddleOCRBackend(
        pipeline_version="v1.6",
        device=args.device,
    )

    input_path = Path(
        args.input
    )

    # ========================================================
    # PDF
    # ========================================================

    if input_path.suffix.lower() == ".pdf":

        results = backend.extract_document(

            input_path=input_path,

            restructure_pages=True,

            merge_tables=True,

            relevel_titles=False,

            concatenate_pages=False,
        )

        for result in results:

            print(
                "\n"
                + "=" * 80
            )

            print(
                f"PAGE: {result.page_number}"
            )

            print(
                f"SUCCESS: {result.success}"
            )

            # ------------------------------------------------
            # Recognition confidence
            # ------------------------------------------------

            if result.confidence is not None:

                print(
                    f"CONFIDENCE: "
                    f"{result.confidence:.4f}"
                )

            else:

                print(
                    "CONFIDENCE: UNAVAILABLE"
                )

            print(
                "CONFIDENCE AVAILABLE: "
                f"{result.confidence_available}"
            )

            print(
                "CONFIDENCE SOURCE: "
                f"{result.confidence_source or 'N/A'}"
            )

            # ------------------------------------------------
            # Layout confidence
            # ------------------------------------------------

            if result.layout_confidence is not None:

                print(
                    "LAYOUT CONFIDENCE: "
                    f"{result.layout_confidence:.4f}"
                )

            else:

                print(
                    "LAYOUT CONFIDENCE: UNAVAILABLE"
                )

            print(
                f"TEXT LENGTH: "
                f"{result.text_length}"
            )

            print(
                "=" * 80
            )

            print(
                result.text
            )

            if result.markdown:

                print(
                    "\nMARKDOWN:"
                )

                print(
                    result.markdown
                )

            if result.error:

                print(
                    "\nERROR:",
                    result.error,
                )

        # ----------------------------------------------------
        # Save already-computed results.
        #
        # IMPORTANT:
        # Do NOT run PaddleOCR-VL again.
        # ----------------------------------------------------

        backend.save_normalized_results(

            results=results,

            input_path=input_path,

            output_dir=args.output,
        )

    # ========================================================
    # Image
    # ========================================================

    else:

        result = backend.extract(
            input_path
        )

        print(
            "\n"
            + "=" * 80
        )

        print(
            "PADDLEOCR-VL RESULT"
        )

        print(
            "=" * 80
        )

        print(
            f"Success: "
            f"{result.success}"
        )

        # ----------------------------------------------------
        # Recognition confidence
        # ----------------------------------------------------

        if result.confidence is not None:

            print(
                f"Confidence: "
                f"{result.confidence:.4f}"
            )

        else:

            print(
                "Confidence: UNAVAILABLE"
            )

        print(
            "Confidence available: "
            f"{result.confidence_available}"
        )

        print(
            "Confidence source: "
            f"{result.confidence_source or 'N/A'}"
        )

        # ----------------------------------------------------
        # Layout confidence
        # ----------------------------------------------------

        if result.layout_confidence is not None:

            print(
                "Layout confidence: "
                f"{result.layout_confidence:.4f}"
            )

        else:

            print(
                "Layout confidence: UNAVAILABLE"
            )

        print(
            f"Text length: "
            f"{result.text_length}"
        )

        print(
            "\nTEXT:"
        )

        print(
            result.text
        )

        if result.markdown:

            print(
                "\nMARKDOWN:"
            )

            print(
                result.markdown
            )

        if result.error:

            print(
                "\nERROR:"
            )

            print(
                result.error
            )

        # ----------------------------------------------------
        # Save already-computed result.
        #
        # IMPORTANT:
        # Do NOT run PaddleOCR-VL again.
        # ----------------------------------------------------

        backend.save_normalized_results(

            results=[result],

            input_path=input_path,

            output_dir=args.output,
        )


# ============================================================
# Entry point
# ============================================================


if __name__ == "__main__":
    main()