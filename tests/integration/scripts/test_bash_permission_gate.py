"""Real seed-ledger decisions with an isolated home and scratch Git repositories."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers.git import git
from tests.helpers.hooks import run_script, script_path
from tests.helpers.permission_cases import FEATURE_PUSH_CASES, HOOK_BYPASS_CASES


@pytest.mark.parametrize(
    ("command", "decision"),
    [(command, "ask" if guarded else "allow") for command, guarded in HOOK_BYPASS_CASES]
    + [(command, "allow" if allowed else "deny") for command, allowed in FEATURE_PUSH_CASES]
    + [
        ("git push", "allow"),
        ("git push --no-verify origin feat/x", "ask"),
        ("git push --no-verify origin master", "deny"),
    ],
)
def test_seed_ledger_guards_hook_bypasses_and_scopes_pushes(
    repo_root: Path,
    checkpoint_repo: Path,
    command: str,
    decision: str,
) -> None:
    """The shipped policy guards bypasses and shared branches, allowing feature pushes."""
    git(checkpoint_repo, "branch", "-m", "feat/test-push")
    config = checkpoint_repo / ".claude"
    config.mkdir()
    (config / "permissions-ledger.json").write_text(
        (repo_root / ".claude/permissions-ledger.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    result = run_script(
        repo_root,
        "bash_permission_gate",
        {"tool_name": "Bash", "tool_input": {"command": command}},
        checkpoint_repo,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == decision


@pytest.mark.parametrize("dispatch", [False, True], ids=["direct", "dispatcher"])
@pytest.mark.parametrize("command", ["git push", "git push --all"])
def test_shared_branch_push_is_denied_through_real_entrypoints(
    repo_root: Path, checkpoint_repo: Path, dispatch: bool, command: str
) -> None:
    """Both entrypoints must emit a hard deny, including when runpy loads the gate."""
    git(checkpoint_repo, "branch", "-m", "master")
    config = checkpoint_repo / ".claude"
    config.mkdir()
    (config / "kasper-manifest.json").write_text(
        json.dumps({"source": str(repo_root)}), encoding="utf-8"
    )
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(checkpoint_repo),
    }
    if dispatch:
        result = subprocess.run(
            [sys.executable, str(script_path(repo_root, "run_hook")), "bash_permission_gate"],
            input=json.dumps(payload),
            cwd=checkpoint_repo,
            env={
                **os.environ,
                "HOME": str(checkpoint_repo),
                "USERPROFILE": str(checkpoint_repo),
                "CLAUDE_PROJECT_DIR": str(repo_root),
            },
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    else:
        result = run_script(repo_root, "bash_permission_gate", payload, checkpoint_repo)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout, "Hook silently omitted the required deny decision"
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "deny"
    assert output["permissionDecisionReason"].startswith("blocked by the push guard:")
    assert "shared-branch and force pushes are human-only" in output["permissionDecisionReason"]
