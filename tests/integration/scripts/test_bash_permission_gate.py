"""Real seed-ledger decisions with an isolated home and no Git execution."""

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.argv_shim import GUARDED_SHIMS, eligible, run_shims
from tests.helpers.hooks import load_script, run_script
from tests.helpers.permission_cases import FEATURE_PUSH_CASES, HOOK_BYPASS_CASES, SWEEP_CASES


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


@pytest.mark.parametrize("shell", ["/bin/zsh", "/bin/bash"])
@pytest.mark.parametrize(
    "label,command,expected",
    [row for row in SWEEP_CASES if eligible(row[1])]
    + [
        ("short-circuit-or", "true || git merge x", "ask"),
        ("short-circuit-and", "false && git merge x; git status", "ask"),
        ("allowed-short-circuit", "true || git status", "allow"),
        ("pipeline", "git status | wc -l", "allow"),
        ("double-quote-escape", r'git commit -m "a\q"', "allow"),
        ("single-quote-escape", r"git commit -m 'a\q'", "allow"),
        ("outside-escape", r"git commit -m a\q", "allow"),
        ("quoted-head", "\"git\" sta''tus", "allow"),
        ("inline-python", "python3 -c 'print(1)'", "ask"),
    ],
)
def test_shell_argv_differential(
    # Any: the seed is heterogeneous JSON consumed by the script API.
    repo_root: Path,
    tmp_path: Path,
    gate_seed: dict[str, Any],
    shell: str,
    label: str,
    command: str,
    expected: str,
) -> None:
    """Check executed argv only; an observed guarded non-form requires an ask verdict."""
    gate = load_script(repo_root, "bash_permission_gate")
    observed = run_shims(shell, command, tmp_path)
    verdict, reason = gate.verdict(command, gate_seed)
    assert verdict == expected, (label, command, reason)
    if verdict == "allow":
        modeled = [part.argv for part in gate.model_command(command)]
        # Unexecuted short-circuit branches and shell builtins produce no records.
        for argv in observed:
            assert argv in modeled, (shell, command, argv, modeled)
    for argv in observed:
        if argv[0] in GUARDED_SHIMS:
            if gate.verdict(shlex.join(argv), gate_seed)[0] != "allow":
                assert verdict == "ask", (shell, command, argv, reason)
    if label == "short-circuit-or" or label == "allowed-short-circuit":
        assert observed == []
    elif label == "short-circuit-and":
        assert observed == [("git", "status")]
    elif label in {"double-quote-escape", "single-quote-escape"}:
        assert observed == [("git", "commit", "-m", r"a\q")]
    elif label == "outside-escape":
        assert observed == [("git", "commit", "-m", "aq")]
    elif label == "quoted-head":
        assert observed == [("git", "status")]
    else:
        assert observed, (label, "differential row exercised no shim")


@pytest.mark.parametrize(
    "command",
    [
        "/usr/bin/git merge x",
        "command -p git merge x",
        "env git status",
        "PATH=tools; git status",
        "printf x",
        "set -e; git status",
        "typeset x",
        "read x",
        "git status > .git/config",
        "$(cat f) merge x",
        "exec git status",
        "git status; /bin/echo x",
        "hash -p /usr/bin/git git",
        "builtin eval x",
    ],
)
def test_differential_harness_refuses_unsafe_rows(command: str) -> None:
    """Rejected text is parse-only and can never reach a shell in this harness."""
    assert not eligible(command)


def test_bash_is_required_32() -> None:
    """The differential result must be from the requested macOS Bash 3.2."""
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-c", 'echo "$BASH_VERSION"'],
        env={},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout.startswith("3.2."), result.stdout
