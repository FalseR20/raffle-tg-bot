"""CSV safety helpers."""

from __future__ import annotations

from typing import Any

_FORMULA_PREFIXES = ("=", "+", "-", "@")


def safe_csv_value(value: Any) -> Any:
    """Neutralize spreadsheet formula injection in user-controlled CSV cells."""
    if not isinstance(value, str):
        return value
    if value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def safe_excel_value(value: Any) -> Any:
    """Prevent openpyxl from turning a user string into a formula."""
    if isinstance(value, str) and value.startswith("="):
        return "'" + value
    return value
