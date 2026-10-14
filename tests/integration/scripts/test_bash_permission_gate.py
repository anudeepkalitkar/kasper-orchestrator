"""Real seed-ledger decisions with an isolated home and no Git execution."""

import json
from pathlib import Path

import pytest

from tests.helpers.hooks import run_script
from tests.helpers.permission_cases import FEATURE_PUSH_CASES, HOOK_BYPASS_CASES


@pytest.mark.parametrize(
    ("command", "guarded"),
    HOOK_BYPASS_CASES + [(command, not allowed) for command, allowed in FEATURE_PUSH_CASES],
)
def test_seed_ledger_guards_hook_bypasses_and_scopes_pushes(
    repo_root: Path,
    tmp_path: Path,
    command: str,
    guarded: bool,
) -> None:
    """The shipped policy guards bypasses and shared branches, allowing feature pushes."""
    config = tmp_path / ".claude"
    config.mkdir()
    (config / "permissions-ledger.json").write_text(
        (repo_root / ".claude/permissions-ledger.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    result = run_script(
        repo_root,
        "bash_permission_gate",
        {"tool_name": "Bash", "tool_input": {"command": command}},
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == (
        "ask" if guarded else "allow"
    )
