"""
OCR package for multilingual payable document processing.

Primary OCR:
    PP-OCRv5 through PaddleOCR

Fallback OCR:
    baidu/Unlimited-OCR

The OCR layer supports the 33 document languages used by the project.
"""

from .extractor import OCRExtractor
from .quality_check import (
    assess_native_text,
    assess_ocr_result,
    should_use_ocr,
    should_use_unlimited_ocr,
)

from .paddle_ocr import PaddleOCRBackend
from .unlimited_ocr import UnlimitedOCR

__all__ = [
    "OCRExtractor",
    "PaddleOCRBackend",
    "UnlimitedOCR",
    "assess_native_text",
    "assess_ocr_result",
    "should_use_ocr",
    "should_use_unlimited_ocr",
]