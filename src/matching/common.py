# src/matching/common.py

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union


PathLike = Union[str, Path]


def load_master_data(path: PathLike) -> Any:
    """
    Load a master-data JSON file safely.

    Rules:
    - Missing file -> FileNotFoundError
    - Directory instead of file -> ValueError
    - Empty file -> ValueError
    - Invalid UTF-8 -> ValueError
    - Invalid JSON -> ValueError
    - Valid JSON -> return the decoded JSON object unchanged

    The loader deliberately does not create fallback records or
    modify master-data values.
    """

    file_path = Path(path)

    # ---------------------------------------------------------
    # 1. File existence
    # ---------------------------------------------------------

    if not file_path.exists():
        raise FileNotFoundError(
            f"Master-data file not found: {file_path}"
        )

    # ---------------------------------------------------------
    # 2. Must be a regular file
    # ---------------------------------------------------------

    if not file_path.is_file():
        raise ValueError(
            f"Master-data path is not a file: {file_path}"
        )

    # ---------------------------------------------------------
    # 3. Read UTF-8 JSON
    #
    # utf-8-sig also handles JSON files containing a UTF-8 BOM.
    # ---------------------------------------------------------

    try:
        text = file_path.read_text(
            encoding="utf-8-sig"
        )

    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Master-data file is not valid UTF-8: "
            f"{file_path}"
        ) from exc

    # ---------------------------------------------------------
    # 4. Reject empty master-data files
    # ---------------------------------------------------------

    if not text.strip():
        raise ValueError(
            f"Master-data file is empty: {file_path}"
        )

    # ---------------------------------------------------------
    # 5. Parse JSON
    # ---------------------------------------------------------

    try:
        return json.loads(text)

    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in master-data file "
            f"{file_path}: "
            f"line {exc.lineno}, "
            f"column {exc.colno}: "
            f"{exc.msg}"
        ) from exc


# =============================================================
# TESTS
# =============================================================

def _run_tests() -> None:

    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:

        root = Path(temp_dir)

        # -----------------------------------------------------
        # 1. Normal JSON object
        # -----------------------------------------------------

        normal_file = root / "normal.json"

        normal_file.write_text(
            '{"supplier_id": "SUP001"}',
            encoding="utf-8",
        )

        result = load_master_data(normal_file)

        assert result == {
            "supplier_id": "SUP001"
        }

        # -----------------------------------------------------
        # 2. JSON list
        # -----------------------------------------------------

        list_file = root / "list.json"

        list_file.write_text(
            '[{"code": "A"}, {"code": "B"}]',
            encoding="utf-8",
        )

        result = load_master_data(list_file)

        assert result == [
            {"code": "A"},
            {"code": "B"},
        ]

        # -----------------------------------------------------
        # 3. UTF-8 BOM
        # -----------------------------------------------------

        bom_file = root / "bom.json"

        bom_file.write_text(
            "\ufeff{\"code\": \"A\"}",
            encoding="utf-8",
        )

        result = load_master_data(bom_file)

        assert result == {
            "code": "A"
        }

        # -----------------------------------------------------
        # 4. Missing file
        # -----------------------------------------------------

        missing_file = root / "missing.json"

        try:
            load_master_data(missing_file)
            assert False, (
                "Missing file should raise "
                "FileNotFoundError"
            )

        except FileNotFoundError:
            pass

        # -----------------------------------------------------
        # 5. Directory instead of file
        # -----------------------------------------------------

        directory = root / "directory"
        directory.mkdir()

        try:
            load_master_data(directory)
            assert False, (
                "Directory should raise ValueError"
            )

        except ValueError:
            pass

        # -----------------------------------------------------
        # 6. Empty file
        # -----------------------------------------------------

        empty_file = root / "empty.json"

        empty_file.write_text(
            "   ",
            encoding="utf-8",
        )

        try:
            load_master_data(empty_file)
            assert False, (
                "Empty file should raise ValueError"
            )

        except ValueError:
            pass

        # -----------------------------------------------------
        # 7. Invalid JSON
        # -----------------------------------------------------

        invalid_file = root / "invalid.json"

        invalid_file.write_text(
            "{invalid json}",
            encoding="utf-8",
        )

        try:
            load_master_data(invalid_file)
            assert False, (
                "Invalid JSON should raise ValueError"
            )

        except ValueError:
            pass

        # -----------------------------------------------------
        # 8. Preserve nested master data
        # -----------------------------------------------------

        nested_file = root / "nested.json"

        nested_data = {
            "companies": [
                {
                    "company_code": "C001",
                    "business_units": [
                        {
                            "business_unit_code": "BU01",
                            "locations": [
                                {
                                    "location_code": "KOL"
                                }
                            ],
                        }
                    ],
                }
            ]
        }

        nested_file.write_text(
            json.dumps(nested_data),
            encoding="utf-8",
        )

        result = load_master_data(nested_file)

        assert result == nested_data
        assert (
            result["companies"][0]["company_code"]
            == "C001"
        )

        print(
            "ALL COMMON MASTER DATA LOADER TESTS PASSED"
        )


if __name__ == "__main__":
    _run_tests()