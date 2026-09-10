"""Helper for building a throwaway KASPER source tree (integration tier only).

A synthetic source lets the integration tier install from somewhere that is *not* this
repository — an incomplete checkout, or a directory that is not a git repository at all.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import install

#: The file dropped into each owned directory so the directory is not empty.
STUB_NAME = "stub.md"


def make_source(
    root: Path,
    settings: Mapping[str, Any],
    ledger: Mapping[str, Any],
    omit: str = "",
) -> Path:
    """Build a ``.claude/`` under ``root`` that ``install.install`` can run against.

    Args:
        root: The directory to build the source tree in; created when absent.
        settings: The document written as the source ``settings.json``.
        ledger: The document written as the source permission ledger.
        omit: An owned path (ADR-0006 §3) to leave out, simulating a partial checkout.

    Returns:
        ``root``, ready to pass as ``source_root``.
    """
    claude = root / ".claude"
    claude.mkdir(parents=True, exist_ok=True)
    for name in install.OWNED_PATHS:
        if name == omit:
            continue
        if name.endswith(".md"):
            (claude / name).write_text(f"# {name}\n", encoding="utf-8")
        else:
            (claude / name).mkdir(exist_ok=True)
            (claude / name / STUB_NAME).write_text(f"# {name}\n", encoding="utf-8")
    (claude / install.SETTINGS_NAME).write_text(json.dumps(settings), encoding="utf-8")
    (claude / install.LEDGER_NAME).write_text(json.dumps(ledger), encoding="utf-8")
    return root
