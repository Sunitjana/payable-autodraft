from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .quality_check import (
    assess_native_text,
    should_run_ocr,
    assess_ocr_result,
    should_use_unlimited_ocr,
)

from .paddle_ocr import (
    PaddleOCRBackend,
    PaddleOCRResult,
)

from .unlimited_ocr import (
    UnlimitedOCR,
)


logger = logging.getLogger(__name__)


# ============================================================
# OCR RESULT
# ============================================================

@dataclass
class OCRPageResult:
    """
    Final normalized OCR result for one document page.

    The result keeps evidence from all extraction stages so that
    later document-understanding and supervisor components can
    compare the available evidence.

    Sources:
        native-pdf
        paddleocr-vl
        unlimited-ocr
        native-pdf-fallback
        none
    """

    # Final selected text.
    text: str = ""

    # Original PDF-native text.
    native_text: str = ""

    # PaddleOCR-VL text.
    paddle_text: str = ""

    # Unlimited-OCR text.
    unlimited_text: str = ""

    # Final source.
    source: str = "none"

    # Quality/confidence of selected result.
    confidence: float = 0.0

    # Quality information.
    native_quality: Dict[str, Any] = field(
        default_factory=dict
    )

    paddle_quality: Dict[str, Any] = field(
        default_factory=dict
    )

    unlimited_quality: Dict[str, Any] = field(
        default_factory=dict
    )

    # Whether Unlimited-OCR was used.
    fallback_used: bool = False

    # Whether usable text was obtained.
    success: bool = False

    # Error information.
    error: Optional[str] = None

    # Page number.
    page_number: Optional[int] = None

    # Rendered page image.
    image_path: Optional[str] = None

    # Structured PaddleOCR-VL output.
    structured: Dict[str, Any] = field(
        default_factory=dict
    )

    # PaddleOCR-VL markdown.
    markdown: str = ""

    # Evidence from each stage.
    evidence: Dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert result into a JSON-compatible dictionary."""

        return asdict(self)


# ============================================================
# OCR EXTRACTOR
# ============================================================

class OCRExtractor:
    """
    Critical-document OCR decision engine.

    Processing order:

        1. PDF-native text
        2. PaddleOCR-VL 1.6
        3. Unlimited-OCR fallback

    Quality policy:

        Native text:
            accepted only when quality >= 0.90

        PaddleOCR-VL:
            accepted only when quality >= 0.90

        Unlimited-OCR:
            used as fallback evidence

    Important:
        OCR quality does NOT establish financial truth.

    Downstream supervisor, financial validation, master-data
    validation and ERP validation must still verify the result.
    """

    def __init__(
        self,
        paddle_enabled: bool = True,
        paddle_pipeline_version: str = "v1.6",
        paddle_device: Optional[str] = None,
        unlimited_ocr_enabled: bool = True,
        unlimited_ocr_model: str = "baidu/Unlimited-OCR",
        unlimited_ocr_mode: str = "pipeline",
        model_device: str = "auto",
        hf_token: str = "",
        native_min_length: int = 30,
        ocr_confidence_threshold: float = 0.90,
    ) -> None:

        self.paddle_enabled = bool(
            paddle_enabled
        )

        self.paddle_pipeline_version = (
            paddle_pipeline_version
        )

        self.paddle_device = (
            paddle_device
            or "cpu"
        )

        self.unlimited_ocr_enabled = bool(
            unlimited_ocr_enabled
        )

        self.unlimited_ocr_model = (
            unlimited_ocr_model
        )

        self.unlimited_ocr_mode = (
            unlimited_ocr_mode
        )

        self.model_device = (
            model_device
        )

        self.hf_token = (
            hf_token
        )

        self.native_min_length = int(
            native_min_length
        )

        # Critical-document quality threshold.
        self.ocr_confidence_threshold = max(
            0.0,
            min(
                float(ocr_confidence_threshold),
                1.0,
            ),
        )

        # Lazy-loaded OCR backends.
        self._paddle: Optional[
            PaddleOCRBackend
        ] = None

        self._unlimited: Optional[
            UnlimitedOCR
        ] = None

        logger.info(
            "OCRExtractor initialized."
        )

        logger.info(
            "PaddleOCR-VL enabled=%s, version=%s",
            self.paddle_enabled,
            self.paddle_pipeline_version,
        )

        logger.info(
            "Unlimited-OCR enabled=%s, mode=%s",
            self.unlimited_ocr_enabled,
            self.unlimited_ocr_mode,
        )

        logger.info(
            "Critical OCR quality threshold=%.2f",
            self.ocr_confidence_threshold,
        )

    # ============================================================
    # BACKEND LOADERS
    # ============================================================

    def _get_paddle(self) -> PaddleOCRBackend:
        """
        Lazily initialize PaddleOCR-VL.
        """

        if self._paddle is None:

            logger.info(
                "Loading PaddleOCR-VL %s...",
                self.paddle_pipeline_version,
            )

            self._paddle = PaddleOCRBackend(
                pipeline_version=(
                    self.paddle_pipeline_version
                ),
                device=self.paddle_device,
            )

        return self._paddle

    def _get_unlimited(self) -> UnlimitedOCR:
        """
        Lazily initialize Unlimited-OCR.
        """

        if self._unlimited is None:

            logger.info(
                "Loading Unlimited-OCR: %s",
                self.unlimited_ocr_model,
            )

            self._unlimited = UnlimitedOCR(
                model_name=self.unlimited_ocr_model,
                device_map=self.model_device,
                hf_token=self.hf_token,
                mode=self.unlimited_ocr_mode,
            )

        return self._unlimited

    # ============================================================
    # SAFE HELPERS
    # ============================================================

    @staticmethod
    def _text_from_result(
        result: Any,
    ) -> str:
        """
        Extract text from common backend result formats.
        """

        if result is None:
            return ""

        if isinstance(result, str):
            return result.strip()

        if isinstance(result, dict):

            value = (
                result.get("text")
                or result.get("content")
                or result.get("output")
                or ""
            )

            return str(value).strip()

        value = getattr(
            result,
            "text",
            "",
        )

        return str(
            value or ""
        ).strip()

    @staticmethod
    def _confidence_from_result(
        result: Any,
    ) -> float:
        """
        Extract confidence when the backend provides one.

        Returns 0.0 when confidence is unavailable.
        This does NOT mean the OCR result itself is necessarily
        wrong; it means no verified confidence value was found.
        """

        if result is None:
            return 0.0

        if isinstance(result, dict):

            value = (
                result.get("confidence")
                if "confidence" in result
                else result.get("score", 0.0)
            )

        else:

            value = getattr(
                result,
                "confidence",
                0.0,
            )

            if value is None:
                value = getattr(
                    result,
                    "score",
                    0.0,
                )

        if value is None:
            return 0.0

        try:

            return max(
                0.0,
                min(
                    1.0,
                    float(value),
                ),
            )

        except (
            TypeError,
            ValueError,
        ):

            return 0.0

    @staticmethod
    def _safe_dict(
        value: Any,
    ) -> Dict[str, Any]:
        """
        Convert common result objects into dictionaries.

        Supports:
            dict
            dataclasses
            objects exposing to_dict()
        """

        if value is None:
            return {}

        if isinstance(value, dict):
            return dict(value)

        # Dataclass support.
        try:

            if is_dataclass(value):

                converted = asdict(
                    value
                )

                if isinstance(
                    converted,
                    dict,
                ):
                    return converted

        except Exception:
            pass

        # Custom to_dict() support.
        if hasattr(
            value,
            "to_dict",
        ):

            try:

                converted = value.to_dict()

                if isinstance(
                    converted,
                    dict,
                ):
                    return converted

            except Exception:
                pass

        return {}

    # ============================================================
    # NATIVE TEXT QUALITY
    # ============================================================

    def _assess_native(
        self,
        native_text: str,
    ) -> Dict[str, Any]:
        """
        Assess native PDF text using the critical-document
        quality threshold.
        """

        try:

            assessment = assess_native_text(
                native_text,
                min_text_length=self.native_min_length,
                threshold=self.ocr_confidence_threshold,
            )

            return self._safe_dict(
                assessment
            )

        except Exception as exc:

            logger.exception(
                "Native text quality assessment failed: %s",
                exc,
            )

            return {
                "score": 0.0,
                "accepted": False,
                "usable": False,
                "good": False,
                "error": str(exc),
            }

    # ============================================================
    # OCR DECISION
    # ============================================================

    def _should_run_ocr(
        self,
        native_text: str,
        native_quality: Dict[str, Any],
    ) -> bool:
        """
        Decide whether PaddleOCR-VL is required.

        The decision is controlled by the 0.90 critical-document
        quality threshold.
        """

        try:

            return should_run_ocr(
                native_text,
                min_text_length=self.native_min_length,
                threshold=self.ocr_confidence_threshold,
            )

        except Exception as exc:

            logger.exception(
                "OCR decision failed: %s",
                exc,
            )

            # Conservative behavior:
            # if quality cannot be evaluated safely,
            # run visual OCR.
            return True

    # ============================================================
    # PADDLE OCR QUALITY
    # ============================================================

    def _assess_paddle(
        self,
        text: str,
        confidence: float,
    ) -> Dict[str, Any]:
        """
        Assess PaddleOCR-VL output.
        """

        try:

            assessment = assess_ocr_result(
                text=text,
                confidence=confidence,
                threshold=self.ocr_confidence_threshold,
            )

            return self._safe_dict(
                assessment
            )

        except Exception as exc:

            logger.exception(
                "PaddleOCR-VL quality assessment failed: %s",
                exc,
            )

            return {
                "score": 0.0,
                "accepted": False,
                "usable": bool(
                    text.strip()
                ),
                "good": False,
                "confidence": confidence,
                "error": str(exc),
            }

    # ============================================================
    # UNLIMITED OCR DECISION
    # ============================================================

    def _should_use_unlimited(
        self,
        paddle_text: str,
        paddle_quality: Dict[str, Any],
        paddle_confidence: float,
    ) -> bool:
        """
        Decide whether Unlimited-OCR fallback is required.

        PaddleOCR-VL must satisfy the configured quality threshold.
        """

        try:

            return should_use_unlimited_ocr(
                text=paddle_text,
                confidence=paddle_confidence,
                threshold=self.ocr_confidence_threshold,
            )

        except Exception as exc:

            logger.exception(
                "Unlimited-OCR decision failed: %s",
                exc,
            )

            # Conservative behavior:
            # use fallback when the quality decision itself fails.
            return True

    # ============================================================
    # EXTRACT PAGE
    # ============================================================

    def extract_page(
        self,
        native_text: str,
        image_path: Optional[
            Union[str, Path]
        ] = None,
        page_number: Optional[int] = None,
    ) -> OCRPageResult:
        """
        Process one PDF page.

        Workflow:

            Native PDF text
                    ↓
            90% quality gate
                    ↓
            PaddleOCR-VL 1.6
                    ↓
            90% quality gate
                    ↓
            Unlimited-OCR fallback
                    ↓
            Supervisor / validation downstream
        """

        native_text = (
            native_text
            or ""
        )

        image = (
            Path(image_path)
            if image_path
            else None
        )

        result = OCRPageResult(
            native_text=native_text,
            page_number=page_number,
            image_path=(
                str(image)
                if image
                else None
            ),
        )

        # ========================================================
        # STEP 1 — NATIVE TEXT QUALITY
        # ========================================================

        native_quality = self._assess_native(
            native_text
        )

        result.native_quality = (
            native_quality
        )

        use_ocr = self._should_run_ocr(
            native_text,
            native_quality,
        )

        logger.info(
            "Page %s: native quality score=%.4f, "
            "OCR required=%s",
            page_number
            if page_number is not None
            else "?",
            float(
                native_quality.get(
                    "score",
                    0.0,
                )
                or 0.0
            ),
            use_ocr,
        )

        # ========================================================
        # NATIVE TEXT ACCEPTED
        # ========================================================

        if not use_ocr:

            logger.info(
                "Page %s: native PDF text accepted.",
                page_number
                if page_number is not None
                else "?",
            )

            result.text = (
                native_text.strip()
            )

            result.source = (
                "native-pdf"
            )

            result.confidence = (
                self._native_confidence(
                    native_quality
                )
            )

            result.success = bool(
                result.text
            )

            result.evidence = {
                "native": {
                    "text": native_text,
                    "quality": native_quality,
                }
            }

            return result

        # ========================================================
        # STEP 2 — PADDLEOCR-VL
        # ========================================================

        if (
            self.paddle_enabled
            and image is not None
        ):

            if not image.exists():

                logger.warning(
                    "Page %s image does not exist: %s",
                    page_number,
                    image,
                )

            else:

                try:

                    logger.info(
                        "Page %s: running PaddleOCR-VL %s.",
                        page_number,
                        self.paddle_pipeline_version,
                    )

                    paddle_result = (
                        self._get_paddle().extract(
                            input_path=image,
                            page_number=page_number,
                        )
                    )

                    paddle_text = (
                        self._text_from_result(
                            paddle_result
                        )
                    )

                    paddle_confidence = (
                        self._confidence_from_result(
                            paddle_result
                        )
                    )

                    result.paddle_text = (
                        paddle_text
                    )

                    result.paddle_quality = (
                        self._assess_paddle(
                            text=paddle_text,
                            confidence=paddle_confidence,
                        )
                    )

                    # Preserve structured PaddleOCR-VL
                    # information.
                    if isinstance(
                        paddle_result,
                        PaddleOCRResult,
                    ):

                        result.structured = (
                            paddle_result.structured
                            or {}
                        )

                        result.markdown = (
                            paddle_result.markdown
                            or ""
                        )

                    elif isinstance(
                        paddle_result,
                        dict,
                    ):

                        result.structured = (
                            paddle_result.get(
                                "structured",
                                {},
                            )
                            or {}
                        )

                        result.markdown = (
                            paddle_result.get(
                                "markdown",
                                "",
                            )
                            or ""
                        )

                    use_unlimited = (
                        self._should_use_unlimited(
                            paddle_text=paddle_text,
                            paddle_quality=(
                                result.paddle_quality
                            ),
                            paddle_confidence=(
                                paddle_confidence
                            ),
                        )
                    )

                    paddle_accepted = (
                        bool(
                            result.paddle_quality.get(
                                "accepted",
                                False,
                            )
                        )
                    )

                    logger.info(
                        "Page %s: PaddleOCR-VL score=%.4f, "
                        "accepted=%s, fallback=%s",
                        page_number,
                        float(
                            result.paddle_quality.get(
                                "score",
                                0.0,
                            )
                            or 0.0
                        ),
                        paddle_accepted,
                        use_unlimited,
                    )

                    # ------------------------------------------------
                    # Paddle result accepted
                    # ------------------------------------------------

                    if (
                        paddle_text
                        and paddle_accepted
                        and not use_unlimited
                    ):

                        logger.info(
                            "Page %s: PaddleOCR-VL "
                            "result accepted.",
                            page_number,
                        )

                        result.text = (
                            paddle_text
                        )

                        result.source = (
                            "paddleocr-vl"
                        )

                        result.confidence = (
                            float(
                                result.paddle_quality.get(
                                    "score",
                                    paddle_confidence,
                                )
                                or 0.0
                            )
                        )

                        result.success = True

                        result.evidence = {
                            "native": {
                                "text": native_text,
                                "quality": (
                                    native_quality
                                ),
                            },
                            "paddle": {
                                "text": paddle_text,
                                "confidence": (
                                    paddle_confidence
                                ),
                                "quality": (
                                    result.paddle_quality
                                ),
                                "structured": (
                                    result.structured
                                ),
                                "markdown": (
                                    result.markdown
                                ),
                            },
                        }

                        return result

                    logger.info(
                        "Page %s: PaddleOCR-VL result "
                        "did not meet 90%% gate; "
                        "trying Unlimited-OCR.",
                        page_number,
                    )

                except Exception as exc:

                    logger.exception(
                        "PaddleOCR-VL failed on page %s: %s",
                        page_number,
                        exc,
                    )

                    result.paddle_quality = {
                        "score": 0.0,
                        "accepted": False,
                        "usable": False,
                        "good": False,
                        "error": str(exc),
                    }

        # ========================================================
        # STEP 3 — UNLIMITED-OCR FALLBACK
        # ========================================================

        if (
            self.unlimited_ocr_enabled
            and image is not None
            and image.exists()
        ):

            try:

                logger.info(
                    "Page %s: running Unlimited-OCR fallback.",
                    page_number,
                )

                unlimited_result = (
                    self._get_unlimited().extract(
                        image_path=image,
                    )
                )

                unlimited_text = (
                    self._text_from_result(
                        unlimited_result
                    )
                )

                unlimited_confidence = (
                    self._confidence_from_result(
                        unlimited_result
                    )
                )

                result.unlimited_text = (
                    unlimited_text
                )

                # Assess Unlimited-OCR output too.
                result.unlimited_quality = (
                    self._assess_paddle(
                        text=unlimited_text,
                        confidence=(
                            unlimited_confidence
                        ),
                    )
                )

                logger.info(
                    "Page %s: Unlimited-OCR score=%.4f.",
                    page_number,
                    float(
                        result.unlimited_quality.get(
                            "score",
                            0.0,
                        )
                        or 0.0
                    ),
                )

                # ------------------------------------------------
                # Unlimited-OCR is fallback evidence.
                #
                # It is NOT treated as truth.
                # The supervisor and financial validation must
                # verify the final extracted values.
                # ------------------------------------------------

                if unlimited_text:

                    logger.info(
                        "Page %s: Unlimited-OCR "
                        "produced text.",
                        page_number,
                    )

                    result.text = (
                        unlimited_text
                    )

                    result.source = (
                        "unlimited-ocr"
                    )

                    result.confidence = (
                        float(
                            result.unlimited_quality.get(
                                "score",
                                unlimited_confidence,
                            )
                            or 0.0
                        )
                    )

                    result.fallback_used = True

                    result.success = True

                    result.evidence = {
                        "native": {
                            "text": native_text,
                            "quality": (
                                native_quality
                            ),
                        },
                        "paddle": {
                            "text": (
                                result.paddle_text
                            ),
                            "quality": (
                                result.paddle_quality
                            ),
                        },
                        "unlimited": {
                            "text": unlimited_text,
                            "confidence": (
                                unlimited_confidence
                            ),
                            "quality": (
                                result.unlimited_quality
                            ),
                        },
                    }

                    return result

                logger.warning(
                    "Page %s: Unlimited-OCR "
                    "returned no text.",
                    page_number,
                )

            except Exception as exc:

                logger.exception(
                    "Unlimited-OCR failed on page %s: %s",
                    page_number,
                    exc,
                )

                result.unlimited_quality = {
                    "score": 0.0,
                    "accepted": False,
                    "usable": False,
                    "confidence": 0.0,
                    "error": str(exc),
                }

        # ========================================================
        # STEP 4 — LAST RESORT
        # ========================================================

        # Important:
        # Do NOT silently treat low-quality native text as a
        # successful OCR result.
        #
        # We preserve it as evidence, but mark the page as
        # unsuccessful if it failed the quality gate.

        if native_text.strip():

            logger.warning(
                "Page %s: all OCR paths failed. "
                "Native text retained as evidence only.",
                page_number,
            )

            result.text = (
                native_text.strip()
            )

            result.source = (
                "native-pdf-fallback"
            )

            result.confidence = (
                float(
                    native_quality.get(
                        "score",
                        0.0,
                    )
                    or 0.0
                )
            )

            # It is text, but it did NOT pass the critical
            # quality gate.
            result.success = False

            result.error = (
                "Native text was below the critical "
                "quality threshold and OCR did not "
                "produce an accepted result."
            )

        else:

            result.text = ""

            result.source = (
                "none"
            )

            result.confidence = 0.0

            result.success = False

            result.error = (
                "No usable native or OCR text."
            )

        result.evidence = {
            "native": {
                "text": native_text,
                "quality": native_quality,
            },
            "paddle": {
                "text": (
                    result.paddle_text
                ),
                "quality": (
                    result.paddle_quality
                ),
            },
            "unlimited": {
                "text": (
                    result.unlimited_text
                ),
                "quality": (
                    result.unlimited_quality
                ),
            },
        }

        return result

    # ============================================================
    # NATIVE CONFIDENCE
    # ============================================================

    @staticmethod
    def _native_confidence(
        quality: Dict[str, Any],
    ) -> float:
        """
        Return the actual native-text quality score.

        We do not manufacture model confidence.
        """

        for key in (
            "score",
            "quality_score",
            "confidence",
        ):

            value = quality.get(
                key
            )

            if value is not None:

                try:

                    return max(
                        0.0,
                        min(
                            1.0,
                            float(value),
                        ),
                    )

                except (
                    TypeError,
                    ValueError,
                ):

                    pass

        return 0.0

    # ============================================================
    # COMPLETE DOCUMENT
    # ============================================================

    def extract_document(
        self,
        pages: List[Any],
    ) -> List[OCRPageResult]:
        """
        Process a list of PageRecord objects.
        """

        results: List[
            OCRPageResult
        ] = []

        for index, page in enumerate(
            pages
        ):

            # Support PageRecord.text.
            native_text = (
                getattr(
                    page,
                    "native_text",
                    None,
                )
                or getattr(
                    page,
                    "text",
                    None,
                )
                or ""
            )

            # Support PageRecord.image_path.
            image_path = (
                getattr(
                    page,
                    "image_path",
                    None,
                )
                or getattr(
                    page,
                    "rendered_path",
                    None,
                )
                or getattr(
                    page,
                    "path",
                    None,
                )
            )

            page_number = (
                getattr(
                    page,
                    "page_number",
                    None,
                )
                or index + 1
            )

            result = self.extract_page(
                native_text=native_text,
                image_path=image_path,
                page_number=page_number,
            )

            results.append(
                result
            )

        return results

    # ============================================================
    # STATUS
    # ============================================================

    def get_status(
        self,
    ) -> Dict[str, Any]:
        """
        Return OCR backend status.
        """

        return {
            "paddle_enabled": (
                self.paddle_enabled
            ),
            "paddle_pipeline_version": (
                self.paddle_pipeline_version
            ),
            "paddle_loaded": (
                self._paddle is not None
            ),
            "unlimited_ocr_enabled": (
                self.unlimited_ocr_enabled
            ),
            "unlimited_ocr_model": (
                self.unlimited_ocr_model
            ),
            "unlimited_ocr_mode": (
                self.unlimited_ocr_mode
            ),
            "unlimited_ocr_loaded": (
                self._unlimited is not None
            ),
            "quality_threshold": (
                self.ocr_confidence_threshold
            ),
            "native_min_length": (
                self.native_min_length
            ),
        }

    # ============================================================
    # UNLOAD
    # ============================================================

    def unload(self) -> None:
        """
        Release OCR models.
        """

        if self._paddle is not None:

            try:

                self._paddle.unload()

            except Exception as exc:

                logger.warning(
                    "Could not unload PaddleOCR-VL: %s",
                    exc,
                )

        if self._unlimited is not None:

            try:

                self._unlimited.unload()

            except Exception as exc:

                logger.warning(
                    "Could not unload Unlimited-OCR: %s",
                    exc,
                )

        self._paddle = None
        self._unlimited = None

        logger.info(
            "OCR backends unloaded."
        )


PDFTextExtractor = OCRExtractor


# ============================================================
# SIMPLE CLI TEST
# ============================================================

def main() -> None:

    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Test the OCR extraction pipeline."
        )
    )

    parser.add_argument(
        "image",
        help="Path to rendered page image.",
    )

    parser.add_argument(
        "--native-text",
        default="",
        help=(
            "Optional PDF-native text "
            "for this page."
        ),
    )

    parser.add_argument(
        "--page",
        type=int,
        default=1,
        help="Page number.",
    )

    parser.add_argument(
        "--paddle-device",
        default=None,
        help=(
            "Paddle device, e.g. "
            "gpu:0 or cpu."
        ),
    )

    parser.add_argument(
        "--unlimited-mode",
        choices=[
            "pipeline",
            "model",
        ],
        default="pipeline",
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=0.90,
        help=(
            "Critical OCR quality threshold. "
            "Default: 0.90"
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

    extractor = OCRExtractor(
        paddle_enabled=True,
        paddle_pipeline_version="v1.6",
        paddle_device=args.paddle_device,
        unlimited_ocr_enabled=True,
        unlimited_ocr_model=(
            "baidu/Unlimited-OCR"
        ),
        unlimited_ocr_mode=(
            args.unlimited_mode
        ),
        model_device="auto",
        ocr_confidence_threshold=(
            args.threshold
        ),
    )

    result = extractor.extract_page(
        native_text=args.native_text,
        image_path=args.image,
        page_number=args.page,
    )

    print(
        "\n"
        + "=" * 80
    )

    print(
        "OCR EXTRACTION RESULT"
    )

    print(
        "=" * 80
    )

    print(
        f"Success      : {result.success}"
    )

    print(
        f"Source       : {result.source}"
    )

    print(
        f"Confidence   : {result.confidence:.4f}"
    )

    print(
        f"Fallback     : {result.fallback_used}"
    )

    print(
        f"Page         : {result.page_number}"
    )

    print(
        "\n"
        + "-" * 80
    )

    print(
        "QUALITY"
    )

    print(
        "-" * 80
    )

    print(
        f"Threshold    : "
        f"{extractor.ocr_confidence_threshold:.2f}"
    )

    print(
        f"Native score : "
        f"{result.native_quality.get('score', 0.0)}"
    )

    print(
        f"Paddle score : "
        f"{result.paddle_quality.get('score', 0.0)}"
    )

    print(
        f"Unlimited    : "
        f"{result.unlimited_quality.get('score', 0.0)}"
    )

    print(
        "\n"
        + "-" * 80
    )

    print(
        "TEXT"
    )

    print(
        "-" * 80
    )

    print(
        result.text
    )

    print(
        "\n"
        + "-" * 80
    )

    print(
        "EVIDENCE SOURCES"
    )

    print(
        "-" * 80
    )

    print(
        f"Native text length   : "
        f"{len(result.native_text)}"
    )

    print(
        f"Paddle text length   : "
        f"{len(result.paddle_text)}"
    )

    print(
        f"Unlimited text length: "
        f"{len(result.unlimited_text)}"
    )

    if result.error:

        print(
            "\nERROR:"
        )

        print(
            result.error
        )


if __name__ == "__main__":
    main()