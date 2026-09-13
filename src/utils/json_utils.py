from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict


def sanitize_for_json(value: Any) -> Any:
    """
    Convert common Python/model objects into JSON-safe values.

    Does not invent or transform business values.
    """

    if value is None:
        return None

    # Dataclass
    if is_dataclass(value):
        return sanitize_for_json(
            asdict(value)
        )

    # Decimal
    if isinstance(value, Decimal):
        return str(value)

    # Date / datetime
    if isinstance(value, (date, datetime)):
        return value.isoformat()

    # Path
    if isinstance(value, Path):
        return str(value)

    # Dictionary
    if isinstance(value, dict):
        return {
            str(key): sanitize_for_json(val)
            for key, val in value.items()
        }

    # List / tuple / set
    if isinstance(value, (list, tuple, set)):
        return [
            sanitize_for_json(item)
            for item in value
        ]

    # NumPy-like scalar support without requiring NumPy
    if hasattr(value, "item"):
        try:
            return sanitize_for_json(
                value.item()
            )
        except Exception:
            pass

    # NumPy-like arrays
    if hasattr(value, "tolist"):
        try:
            return sanitize_for_json(
                value.tolist()
            )
        except Exception:
            pass

    # Primitive JSON types
    if isinstance(
        value,
        (str, int, float, bool),
    ):
        return value

    # Last-resort representation.
    # Prefer explicit serialization for business objects.
    return str(value)


def dumps_json(
    data: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> str:

    safe_data = sanitize_for_json(data)

    return json.dumps(
        safe_data,
        indent=indent,
        ensure_ascii=ensure_ascii,
    )


def load_json(
    path: str | Path,
) -> Any:

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"JSON file not found: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:

        return json.load(file)


def save_json(
    path: str | Path,
    data: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:

    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    safe_data = sanitize_for_json(data)

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            safe_data,
            file,
            indent=indent,
            ensure_ascii=ensure_ascii,
        )