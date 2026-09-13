from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Optional

from ..config.settings import (
    UNLIMITED_OCR_MODEL,
    MODEL_DEVICE,
    HF_TOKEN,
)


logger = logging.getLogger(__name__)


class UnlimitedOCR:
    """
    Fallback OCR engine using baidu/Unlimited-OCR.

    This module is intentionally responsible only for:

        Image -> OCR transcription

    It must NOT:
        - determine whether a document is payable
        - invent missing financial values
        - perform supplier matching
        - perform PO matching
        - calculate totals
        - make ERP decisions

    Those responsibilities belong to later pipeline stages.

    Production backend:
        Transformers image-text-to-text pipeline.
    """

    PIPELINE_MODE = "pipeline"

    def __init__(
        self,
        model_name: str = UNLIMITED_OCR_MODEL,
        device_map: str = MODEL_DEVICE,
        hf_token: str = HF_TOKEN,
        mode: str = PIPELINE_MODE,
    ) -> None:

        self.model_name = model_name
        self.device_map = device_map
        self.hf_token = hf_token or None

        mode = mode.lower().strip()

        if mode != self.PIPELINE_MODE:
            raise ValueError(
                "Unlimited-OCR currently supports only "
                "'pipeline' mode for production inference."
            )

        self.mode = mode

        self._pipeline = None

    # ============================================================
    # MODEL LOADING
    # ============================================================

    def _load_pipeline(self):
        """
        Lazily load the official Transformers pipeline.

        Official model-card API:

            pipeline(
                "image-text-to-text",
                model="baidu/Unlimited-OCR",
                trust_remote_code=True,
            )
        """

        if self._pipeline is not None:
            return self._pipeline

        try:
            from transformers import pipeline

        except ImportError as exc:
            raise ImportError(
                "transformers is not installed. "
                "Install it before using Unlimited-OCR."
            ) from exc

        logger.info(
            "Loading Unlimited-OCR: %s",
            self.model_name,
        )

        pipeline_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "trust_remote_code": True,
        }

        # Hugging Face authentication is optional for this model.
        if self.hf_token:
            pipeline_kwargs["token"] = self.hf_token

        # Only provide device_map when explicitly configured.
        #
        # For:
        #     MODEL_DEVICE=auto
        #
        # Transformers handles device placement.
        if (
            self.device_map
            and self.device_map.lower() != "auto"
        ):
            pipeline_kwargs["device_map"] = self.device_map

        self._pipeline = pipeline(
            "image-text-to-text",
            **pipeline_kwargs,
        )

        logger.info(
            "Unlimited-OCR loaded successfully."
        )

        return self._pipeline

    # ============================================================
    # PROMPT
    # ============================================================

    @staticmethod
    def build_prompt() -> str:
        """
        OCR-only instruction.

        The model is explicitly instructed to transcribe rather
        than reason about the document.
        """

        return """
Read the provided document page carefully and transcribe its
visible content.

Requirements:

1. Transcribe all visible text.
2. Preserve the original language.
3. Do not translate the document.
4. Preserve invoice numbers, dates, amounts, currencies,
   percentages, tax values, PO numbers and supplier information.
5. Preserve line-item information when visible.
6. Preserve table structure when possible.
7. Preserve the order of the document content.
8. Do not invent missing values.
9. Do not infer values that are not visible.
10. If a value is unreadable or uncertain, mark it as uncertain.
11. Preserve important symbols, decimal points and percentages.
12. Preserve numeric values exactly as visible.

Return only the extracted document content.
""".strip()

    # ============================================================
    # INPUT
    # ============================================================

    @staticmethod
    def _build_pipeline_input(
        image_path: Path,
        prompt: str,
    ) -> dict[str, Any]:
        """
        Build the image-text-to-text pipeline input.
        """

        return {
            "text": prompt,
            "images": [str(image_path)],
        }

    # ============================================================
    # OUTPUT NORMALIZATION
    # ============================================================

    @classmethod
    def _normalize_output(
        cls,
        output: Any,
    ) -> str:
        """
        Convert possible Transformers pipeline output formats
        into plain text.
        """

        if output is None:
            return ""

        # --------------------------------------------------------
        # String
        # --------------------------------------------------------

        if isinstance(output, str):
            return output.strip()

        # --------------------------------------------------------
        # Dictionary
        # --------------------------------------------------------

        if isinstance(output, dict):

            for key in (
                "generated_text",
                "text",
                "output_text",
                "content",
            ):

                if key not in output:
                    continue

                value = output[key]

                if isinstance(value, str):
                    return value.strip()

                nested = cls._normalize_output(value)

                if nested:
                    return nested

            return ""

        # --------------------------------------------------------
        # List / tuple
        # --------------------------------------------------------

        if isinstance(output, (list, tuple)):

            parts: list[str] = []

            for item in output:

                text = cls._normalize_output(item)

                if text:
                    parts.append(text)

            return "\n".join(parts).strip()

        # --------------------------------------------------------
        # Fallback
        # --------------------------------------------------------

        return str(output).strip()

    # ============================================================
    # EXTRACTION
    # ============================================================

    def _extract_with_pipeline(
        self,
        image_path: Path,
        prompt: str,
    ) -> dict[str, Any]:
        """
        Run Unlimited-OCR inference.
        """

        pipe = self._load_pipeline()

        pipeline_input = self._build_pipeline_input(
            image_path=image_path,
            prompt=prompt,
        )

        output = pipe(
            pipeline_input,
        )

        text = self._normalize_output(output)

        return {
            "success": True,
            "text": text,
            "confidence": None,
            "confidence_available": False,
            "confidence_source": None,
            "model": self.model_name,
            "source": "unlimited-ocr",
            "backend": "transformers-pipeline",
            "image": str(image_path),
            "raw": output,
        }

    # ============================================================
    # MAIN API
    # ============================================================

    def extract(
        self,
        image_path: str | Path,
        prompt: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Run Unlimited-OCR on one rendered document page.

        Parameters
        ----------
        image_path:
            Path to a rendered page image.

        prompt:
            Optional OCR instruction.

        Returns
        -------
        dict
            Normalized OCR result.
        """

        image_path = Path(image_path)

        # --------------------------------------------------------
        # Validate image
        # --------------------------------------------------------

        if not image_path.exists():

            return {
                "success": False,
                "text": "",
                "confidence": None,
                "confidence_available": False,
                "confidence_source": None,
                "model": self.model_name,
                "source": "unlimited-ocr",
                "backend": self.mode,
                "image": str(image_path),
                "error": (
                    f"Image not found: {image_path}"
                ),
                "raw": None,
            }

        if not image_path.is_file():

            return {
                "success": False,
                "text": "",
                "confidence": None,
                "confidence_available": False,
                "confidence_source": None,
                "model": self.model_name,
                "source": "unlimited-ocr",
                "backend": self.mode,
                "image": str(image_path),
                "error": (
                    f"Input path is not a file: "
                    f"{image_path}"
                ),
                "raw": None,
            }

        prompt = (
            prompt
            if prompt is not None
            else self.build_prompt()
        )

        # --------------------------------------------------------
        # OCR inference
        # --------------------------------------------------------

        try:

            return self._extract_with_pipeline(
                image_path=image_path,
                prompt=prompt,
            )

        except Exception as exc:

            logger.exception(
                "Unlimited-OCR failed for %s",
                image_path,
            )

            return {
                "success": False,
                "text": "",
                "confidence": None,
                "confidence_available": False,
                "confidence_source": None,
                "model": self.model_name,
                "source": "unlimited-ocr",
                "backend": self.mode,
                "image": str(image_path),
                "error": str(exc),
                "raw": None,
            }

    # ============================================================
    # STATUS
    # ============================================================

    def is_loaded(self) -> bool:
        """Return True if the OCR pipeline is loaded."""

        return self._pipeline is not None

    # ============================================================
    # UNLOAD
    # ============================================================

    def unload(self) -> None:
        """
        Release the model reference.

        This is useful because Unlimited-OCR is a large model.
        """

        self._pipeline = None

        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        except ImportError:
            pass

        logger.info(
            "Unlimited-OCR resources released."
        )


# ================================================================
# CLI
# ================================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description="Test baidu/Unlimited-OCR"
    )

    parser.add_argument(
        "input",
        help="Path to a rendered document page image.",
    )

    parser.add_argument(
        "--device-map",
        default=MODEL_DEVICE,
        help="Device map, normally 'auto'.",
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

    ocr = UnlimitedOCR(
        device_map=args.device_map,
    )

    result = ocr.extract(
        args.input,
    )

    print()
    print("=" * 80)
    print("UNLIMITED-OCR RESULT")
    print("=" * 80)

    print(
        f"Success: "
        f"{result.get('success')}"
    )

    print(
        f"Backend: "
        f"{result.get('backend')}"
    )

    print(
        f"Model: "
        f"{result.get('model')}"
    )

    print(
        f"Confidence: "
        f"{result.get('confidence')}"
    )

    print(
        f"Confidence available: "
        f"{result.get('confidence_available')}"
    )

    if result.get("text"):

        print()
        print("TEXT:")
        print("-" * 80)
        print(result["text"])

    if result.get("error"):

        print()
        print("ERROR:")
        print("-" * 80)
        print(result["error"])

    print("=" * 80)


if __name__ == "__main__":
    main()