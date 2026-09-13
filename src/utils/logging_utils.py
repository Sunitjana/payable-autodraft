from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional


DEFAULT_FORMAT = (
    "%(asctime)s | "
    "%(levelname)s | "
    "%(name)s | "
    "%(message)s"
)


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
) -> logging.Logger:
    """
    Configure application-wide logging.
    """

    numeric_level = getattr(
        logging,
        level.upper(),
        logging.INFO,
    )

    root_logger = logging.getLogger()

    root_logger.setLevel(numeric_level)

    # Prevent duplicate handlers if setup_logging is called twice.
    root_logger.handlers.clear()

    formatter = logging.Formatter(DEFAULT_FORMAT)

    console_handler = logging.StreamHandler(
        sys.stdout
    )

    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)

    if log_file:
        log_path = Path(log_file)

        log_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        file_handler = logging.FileHandler(
            log_path,
            encoding="utf-8",
        )

        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)

        root_logger.addHandler(file_handler)

    return root_logger


def get_logger(
    name: Optional[str] = None,
) -> logging.Logger:

    return logging.getLogger(
        name or "payable_autodraft"
    )