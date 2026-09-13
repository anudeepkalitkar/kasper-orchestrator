"""Helpers for reading what a run left on disk (integration tier only)."""

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


def tree_bytes(root: Path) -> dict[str, bytes]:
    """Snapshot every file under ``root`` as ``relative posix path -> bytes``.

    Symlinks are recorded by their target rather than followed, so a snapshot says what a
    directory *is* and comparing two of them proves nothing was rewritten behind a link.

    Args:
        root: The directory to snapshot.

    Returns:
        One entry per file and symlink found, keyed by path relative to ``root``.
    """
    snapshot: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        key = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[key] = b"symlink -> " + str(path.readlink()).encode("utf-8")
        elif path.is_file():
            snapshot[key] = path.read_bytes()
    return snapshot
