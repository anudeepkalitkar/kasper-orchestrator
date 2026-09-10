#!/usr/bin/env python3
"""Best-effort Python autoformatter, invoked as a PostToolUse hook.

Reads the hook payload (JSON) from stdin, extracts the edited file path, and — if it is a Python
file and ``ruff`` is available — runs ``ruff check --fix`` (lint autofix) then ``ruff format``.
Prefers the project's ``.venv/bin/ruff`` (KASPER installs ruff into the venv via ``make setup``),
falling back to any ``ruff`` on PATH.

A silent no-op when the edited file is not Python, ``ruff`` is not found, or anything goes wrong.
It NEVER blocks the tool call: it always exits 0.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_RUFF_STEPS: tuple[list[str], ...] = (["check", "--fix", "--quiet"], ["format", "--quiet"])


def _find_ruff() -> str | None:
    """Return the project's venv ruff if present, else any ruff on PATH, else None."""
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if project:
        venv_ruff = Path(project) / ".venv" / "bin" / "ruff"
        if venv_ruff.is_file():
            return str(venv_ruff)
    return shutil.which("ruff")


def _format_file(file_path: Path) -> bool:
    """Run ruff --fix then ruff format on a Python file in place.

    Returns True only when the file's bytes actually changed, so the caller can surface a visible
    trace on real reformats and stay silent on already-clean edits.
    """
    if file_path.suffix != ".py" or not file_path.is_file():
        return False
    ruff = _find_ruff()
    if ruff is None:
        return False
    try:
        before = file_path.read_bytes()
    except OSError:
        return False
    for args in _RUFF_STEPS:
        try:
            subprocess.run(
                [ruff, *args, str(file_path)], check=False, capture_output=True, timeout=30
            )
        except (subprocess.SubprocessError, OSError):
            continue  # best-effort: a formatter failure must not break the workflow
    try:
        return file_path.read_bytes() != before
    except OSError:
        return False


def _extract_file_path(payload: dict[str, object]) -> Path | None:
    """Pull the edited file path out of a PostToolUse hook payload."""
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        raw = tool_input.get("file_path")
        if isinstance(raw, str) and raw:
            return Path(raw)
    return None


def main() -> int:
    """Read the PostToolUse payload from stdin and autoformat the edited Python file."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    file_path = _extract_file_path(payload)
    if file_path is not None and _format_file(file_path):
        # `systemMessage` renders inline in Claude Code — you see when the hook reformatted a file.
        print(json.dumps({"systemMessage": f"✎ autoformatted {file_path.name}"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
