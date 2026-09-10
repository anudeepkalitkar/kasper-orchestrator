"""Helpers for reading files an install produced (integration tier only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    """Load a UTF-8 JSON object from disk.

    Args:
        path: The file to read.

    Returns:
        The decoded object.
    """
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
