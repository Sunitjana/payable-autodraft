from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import torch


# ============================================================
# Result
# ============================================================


@dataclass
class QwenVLResult:
    """
    Result returned by the Qwen3-VL supervisor.
    """

    success: bool
    raw_text: str
    parsed: Optional[Dict[str, Any]]

    error: Optional[str] = None

    # Whether the model returned usable structured JSON.
    verified: bool = False


# ============================================================
# Supervisor
# ============================================================


class QwenVL:
    """
    Qwen3-VL visual verification supervisor.

    Responsibilities
    ----------------
    - Inspect the ORIGINAL document image.
    - Compare OCR outputs.
    - Verify extracted fields.
    - Identify OCR errors.
    - Identify conflicts.
    - Correct OCR only when the original image visibly
      supports the correction.
    - Return unresolved values as null.

    This module does NOT:
    - perform master-data matching
    - calculate financial totals
    - create ERP payloads
    - invent missing values

    Default model:
        Qwen/Qwen3-VL-8B-Instruct
    """

    DEFAULT_MODEL = "Qwen/Qwen3-VL-8B-Instruct"

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device_map: str = "auto",
        dtype: str = "auto",
        max_new_tokens: int = 256,
    ) -> None:

        self.model_name = model_name
        self.device_map = device_map
        self.dtype = dtype
        self.max_new_tokens = max_new_tokens

        self.model = None
        self.processor = None

    # ========================================================
    # MODEL LOADING
    # ========================================================

    def load(self) -> None:
        """
        Lazily load Qwen3-VL and its processor.
        """

        if (
            self.model is not None
            and self.processor is not None
        ):
            return

        try:
            from transformers import (
                AutoProcessor,
                Qwen3VLForConditionalGeneration,
            )

        except ImportError as exc:
            raise ImportError(
                "transformers is not installed. "
                "Install transformers before using Qwen3-VL."
            ) from exc

        load_kwargs: dict[str, Any] = {
            "device_map": self.device_map,
        }

        # ----------------------------------------------------
        # DTYPE
        # ----------------------------------------------------

        if self.dtype != "auto":

            dtype_value = getattr(
                torch,
                self.dtype,
                None,
            )

            if dtype_value is None:
                raise ValueError(
                    f"Unsupported torch dtype: {self.dtype}"
                )

            load_kwargs["dtype"] = dtype_value

        # ----------------------------------------------------
        # MODEL
        # ----------------------------------------------------

        self.model = (
            Qwen3VLForConditionalGeneration
            .from_pretrained(
                self.model_name,
                **load_kwargs,
            )
        )

        # ----------------------------------------------------
        # PROCESSOR
        # ----------------------------------------------------

        self.processor = (
            AutoProcessor.from_pretrained(
                self.model_name
            )
        )

    # ========================================================
    # PROMPT
    # ========================================================

    @staticmethod
    def build_verification_prompt(
        ocr_primary: str,
        ocr_fallback: str = "",
        extracted_data: Optional[
            Dict[str, Any]
        ] = None,
    ) -> str:
        """
        Build a strict visual verification prompt.
        """

        extracted_json = json.dumps(
            extracted_data or {},
            ensure_ascii=False,
            indent=2,
        )

        return f"""
You are a strict financial-document verification supervisor.

You are given:

1. The ORIGINAL document image.
2. Primary OCR output.
3. Optional fallback OCR output.
4. Current extracted structured data.

The ORIGINAL DOCUMENT IMAGE is the highest-priority evidence.

Your job is to VERIFY the information against the visible
document, not to guess or reconstruct missing information.

============================================================
PRIMARY OCR
============================================================

{ocr_primary}

============================================================
FALLBACK OCR
============================================================

{ocr_fallback}

============================================================
CURRENT EXTRACTED DATA
============================================================

{extracted_json}

============================================================
STRICT RULES
============================================================

1. Use the original document image as the primary evidence.

2. Compare the OCR outputs against the original image.

3. Do NOT trust OCR blindly.

4. Do NOT invent any value.

5. If a value is not visible or cannot be established
   reliably from the image, return null.

6. Preserve the original document language.

7. Do not translate field values.

8. If OCR is incorrect but the original image clearly shows
   the correct value, return the visually supported value and
   mark the field as "corrected".

9. If OCR sources conflict and the image cannot resolve the
   conflict, return null and mark the field as "unresolved".

10. Financial numbers must be copied exactly as visible.

11. Preserve decimal points.

12. Preserve currency symbols/codes.

13. Preserve percentages.

14. Preserve invoice numbers exactly.

15. Preserve PO numbers exactly.

16. Do NOT calculate a missing financial value.

17. Do NOT derive a value merely because another field
    mathematically suggests it.

18. Do NOT substitute a master-data value for a value printed
    on the document.

19. Distinguish between:
      - subtotal
      - tax
      - discount
      - charge
      - gross total
      - withholding
      - amount payable

20. A withholding amount must not automatically be treated as
    a discount or charge.

21. Preserve line-item quantities and unit prices exactly.

22. If a value is uncertain, use null.

23. Return ONLY valid JSON.

============================================================
REQUIRED JSON STRUCTURE
============================================================

{{
  "document_type": "invoice|credit_memo|non_payable|unknown",

  "is_payable": true,

  "overall_confidence": 0.0,

  "verification_status":
    "verified|corrected|unresolved|rejected",

  "fields": {{

    "invoice_number": {{
      "value": null,
      "confidence": 0.0,
      "status":
        "verified|corrected|unresolved"
    }},

    "invoice_date": {{
      "value": null,
      "confidence": 0.0,
      "status":
        "verified|corrected|unresolved"
    }},

    "due_date": {{
      "value": null,
      "confidence": 0.0,
      "status":
        "verified|corrected|unresolved"
    }},

    "supplier_name": {{
      "value": null,
      "confidence": 0.0,
      "status":
        "verified|corrected|unresolved"
    }},

    "supplier_tax_id": {{
      "value": null,
      "confidence": 0.0,
      "status":
        "verified|corrected|unresolved"
    }},

    "po_number": {{
      "value": null,
      "confidence": 0.0,
      "status":
        "verified|corrected|unresolved"
    }},

    "currency": {{
      "value": null,
      "confidence": 0.0,
      "status":
        "verified|corrected|unresolved"
    }},

    "subtotal": {{
      "value": null,
      "confidence": 0.0,
      "status":
        "verified|corrected|unresolved"
    }},

    "gross_amount": {{
      "value": null,
      "confidence": 0.0,
      "status":
        "verified|corrected|unresolved"
    }}
  }},

  "line_items": [],

  "taxes": [],

  "discounts": [],

  "charges": [],

  "conflicts": [],

  "evidence": []
}}

============================================================
FIELD CONFIDENCE
============================================================

Confidence must represent visual verification confidence.

Use:

- 0.90-1.00 = clearly visible and verified
- 0.70-0.89 = reasonably visible but some uncertainty
- below 0.70 = uncertain

If value is null:
    confidence must be 0.0.

Do not fabricate confidence values.

============================================================
FINAL SAFETY RULE
============================================================

When in doubt:

    return null

Do not guess.
Do not calculate missing values.
Do not invent values.
""".strip()

    # ========================================================
    # IMAGE VALIDATION
    # ========================================================

    @staticmethod
    def _validate_image(
        image_path: str | Path,
    ) -> Path:

        path = Path(image_path)

        if not path.exists():
            raise FileNotFoundError(
                f"Document image not found: {path}"
            )

        if not path.is_file():
            raise ValueError(
                f"Document image is not a file: {path}"
            )

        return path.resolve()

    # ========================================================
    # JSON NORMALIZATION
    # ========================================================

    @staticmethod
    def _clean_json_text(
        text: str,
    ) -> str:

        text = text.strip()

        # Remove markdown fences.
        text = re.sub(
            r"```(?:json)?",
            "",
            text,
            flags=re.IGNORECASE,
        )

        text = text.replace(
            "```",
            "",
        )

        return text.strip()

    # ========================================================
    # JSON PARSER
    # ========================================================

    @classmethod
    def _parse_json(
        cls,
        text: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Safely parse JSON returned by the model.
        """

        if not text:
            return None

        cleaned = cls._clean_json_text(text)

        # ----------------------------------------------------
        # Direct JSON
        # ----------------------------------------------------

        try:

            value = json.loads(cleaned)

            if isinstance(value, dict):
                return value

        except json.JSONDecodeError:
            pass

        # ----------------------------------------------------
        # Extract JSON object
        # ----------------------------------------------------

        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start == -1 or end == -1:
            return None

        if end <= start:
            return None

        candidate = cleaned[
            start:end + 1
        ]

        try:

            value = json.loads(candidate)

            if isinstance(value, dict):
                return value

        except json.JSONDecodeError:
            return None

        return None

    # ========================================================
    # RESPONSE VALIDATION
    # ========================================================

    @staticmethod
    def _validate_response(
        data: Optional[Dict[str, Any]],
    ) -> bool:
        """
        Validate the minimum supervisor response structure.

        This does not perform financial validation.
        """

        if not isinstance(data, dict):
            return False

        required_keys = {
            "document_type",
            "is_payable",
            "overall_confidence",
            "fields",
            "line_items",
            "taxes",
            "discounts",
            "charges",
            "conflicts",
            "evidence",
        }

        if not required_keys.issubset(
            data.keys()
        ):
            return False

        if not isinstance(
            data.get("fields"),
            dict,
        ):
            return False

        if not isinstance(
            data.get("line_items"),
            list,
        ):
            return False

        if not isinstance(
            data.get("taxes"),
            list,
        ):
            return False

        if not isinstance(
            data.get("conflicts"),
            list,
        ):
            return False

        return True

    # ========================================================
    # GENERATION
    # ========================================================

    def verify(
        self,
        image_path: str | Path,
        ocr_primary: str,
        ocr_fallback: str = "",
        extracted_data: Optional[
            Dict[str, Any]
        ] = None,
    ) -> QwenVLResult:
        """
        Verify extracted document information against the
        original page image.
        """

        try:

            image_path = self._validate_image(
                image_path
            )

            self.load()

            prompt = self.build_verification_prompt(
                ocr_primary=ocr_primary,
                ocr_fallback=ocr_fallback,
                extracted_data=extracted_data,
            )

            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "image": str(image_path),
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ]

            # ------------------------------------------------
            # Prepare model input
            # ------------------------------------------------

            inputs = (
                self.processor.apply_chat_template(
                    messages,
                    tokenize=True,
                    add_generation_prompt=True,
                    return_dict=True,
                    return_tensors="pt",
                )
            )

            # ------------------------------------------------
            # Device
            # ------------------------------------------------

            if hasattr(
                self.model,
                "device",
            ):

                inputs = inputs.to(
                    self.model.device
                )

            # ------------------------------------------------
            # Generation
            # ------------------------------------------------

            with torch.inference_mode():

                generated_ids = (
                    self.model.generate(
                        **inputs,
                        max_new_tokens=(
                            self.max_new_tokens
                        ),
                    )
                )

            # ------------------------------------------------
            # Remove prompt tokens
            # ------------------------------------------------

            generated_ids_trimmed = [
                output_ids[len(input_ids):]
                for input_ids, output_ids
                in zip(
                    inputs["input_ids"],
                    generated_ids,
                )
            ]

            # ------------------------------------------------
            # Decode
            # ------------------------------------------------

            output_text = (
                self.processor.batch_decode(
                    generated_ids_trimmed,
                    skip_special_tokens=True,
                    clean_up_tokenization_spaces=False,
                )[0]
            )

            # ------------------------------------------------
            # Parse JSON
            # ------------------------------------------------

            parsed = self._parse_json(
                output_text
            )

            # ------------------------------------------------
            # Validate structure
            # ------------------------------------------------

            verified = self._validate_response(
                parsed
            )

            if not verified:

                return QwenVLResult(
                    success=False,
                    raw_text=output_text,
                    parsed=parsed,
                    error=(
                        "Qwen3-VL returned invalid or "
                        "incomplete verification JSON."
                    ),
                    verified=False,
                )

            return QwenVLResult(
                success=True,
                raw_text=output_text,
                parsed=parsed,
                error=None,
                verified=True,
            )

        except Exception as exc:

            return QwenVLResult(
                success=False,
                raw_text="",
                parsed=None,
                error=str(exc),
                verified=False,
            )

    # ========================================================
    # STATUS
    # ========================================================

    def is_loaded(self) -> bool:
        """
        Return True when both model and processor are loaded.
        """

        return (
            self.model is not None
            and self.processor is not None
        )

    # ========================================================
    # UNLOAD
    # ========================================================

    def unload(self) -> None:
        """
        Release Qwen3-VL resources.
        """

        self.model = None
        self.processor = None

        try:

            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        except Exception:
            pass


# ================================================================
# CLI
# ================================================================


def main() -> None:

    parser = __import__(
        "argparse"
    ).ArgumentParser(
        description=(
            "Test Qwen3-VL visual document supervisor"
        )
    )

    parser.add_argument(
        "image",
        help="Path to rendered document page image.",
    )

    parser.add_argument(
        "--device-map",
        default="auto",
        help="Transformers device map.",
    )

    parser.add_argument(
        "--dtype",
        default="auto",
        help="Torch dtype, e.g. bfloat16 or float16.",
    )

    args = parser.parse_args()

    # ========================================================
    # CPU-FRIENDLY QWEN CONFIGURATION
    # ========================================================

    supervisor = QwenVL(
        device_map=args.device_map,
        dtype=args.dtype,
        max_new_tokens=256,
    )

    # ========================================================
    # Simple test OCR
    # ========================================================

    primary_ocr = (
        "Invoice Number: TEST-001\n"
        "Date: 13-09-2026\n"
        "Total: 100.00"
    )

    # ========================================================
    # Run verification
    # ========================================================

    result = supervisor.verify(
        image_path=args.image,
        ocr_primary=primary_ocr,
    )

    print()
    print("=" * 80)
    print("QWEN3-VL SUPERVISOR RESULT")
    print("=" * 80)

    print(
        f"Success:  {result.success}"
    )

    print(
        f"Verified: {result.verified}"
    )

    if result.error:

        print()
        print("ERROR:")
        print(result.error)

    if result.parsed:

        print()
        print("PARSED JSON:")
        print(
            json.dumps(
                result.parsed,
                ensure_ascii=False,
                indent=2,
            )
        )

    elif result.raw_text:

        print()
        print("RAW MODEL OUTPUT:")
        print(result.raw_text)

    print("=" * 80)


if __name__ == "__main__":
    main()