"""Integration tier for ``.claude/scripts/permission_recorder.py``.

The recorder has no pure dump function — ``main()`` reads a hook payload from stdin and
rewrites the ledger at a module-level path resolved from ``CLAUDE_PROJECT_DIR`` — so it is
exercised through its real entrypoint, as a subprocess, against a temporary project.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers.hooks import run_script

#: A ledger the recorder can read, whose prose carries the em dashes the real one uses.
NOTE = "recorded by hand — never by a hook"


def _project_with_ledger(tmp_path: Path) -> Path:
    """A throwaway project whose ``.claude/`` holds a minimal, non-ASCII ledger."""
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "permissions-ledger.json").write_text(
        json.dumps(
            {
                "_doc": NOTE,
                "allow_keys": {"ls": "read-only"},
                "allow_patterns": [],
                "ask_patterns": [],
                "grants": [],
                "denials": [],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def test_recorder_writes_non_ascii_prose_literally(repo_root: Path, tmp_path: Path) -> None:
    """A recorded grant must not re-escape the ledger's em dashes into ``\\u2014``.

    The default ``ensure_ascii=True`` would rewrite every line of prose in the file on each
    grant, turning a one-line append into a whole-file diff.
    """
    project = _project_with_ledger(tmp_path)
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git status"},
        "permission_mode": "default",
    }
    completed = subprocess.run(
        [sys.executable, str(repo_root / ".claude/scripts/permission_recorder.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        # The real environment, so the recorder's `shutil.which("git")` finds git wherever
        # this machine keeps it; only the ledger's location is overridden.
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)},
    )
    assert completed.returncode == 0, completed.stderr
    ledger_path = project / ".claude/permissions-ledger.json"
    raw = ledger_path.read_text(encoding="utf-8")
    # The grant proves the file was actually rewritten, so the assertions below are not
    # passing on the untouched original.
    assert [grant["key"] for grant in json.loads(raw)["grants"]] == ["git status"]
    assert NOTE in raw
    assert "\\u2014" not in raw


@pytest.mark.parametrize(
    ("part", "expected"),
    [
        ("od -c", "od -c"),
        ("od \t  -c", "od -c"),
        ("od -c " + "x" * 153, "od -c " + "x" * 153),
        ("od -c " + "x" * 154, "od -c " + "x" * 154),
        ("od -c " + "x" * 155, "od -c " + "x" * 154),
        ("od \t -c " + "x" * 220, "od -c " + "x" * 154),
    ],
    ids=["pipeline", "whitespace", "159-chars", "160-chars", "161-chars", "normalize-then-cap"],
)
def test_recorded_example_describes_promoted_pipeline_part(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    part: str,
    expected: str,
) -> None:
    """Persist the normalized promoted part, preserving up to exactly 160 characters."""
    project = _project_with_ledger(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    completed = run_script(
        repo_root,
        "permission_recorder",
        {
            "tool_name": "Bash",
            "tool_input": {"command": f"cat f | {part} | tail -3"},
            "permission_mode": "default",
        },
        project,
    )
    assert completed.returncode == 0, completed.stderr
    ledger = json.loads((project / ".claude/permissions-ledger.json").read_text(encoding="utf-8"))
    examples = [grant["example"] for grant in ledger["grants"] if grant["key"] == "od"]
    assert examples == [expected]
