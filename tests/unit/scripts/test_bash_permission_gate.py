"""Spec-driven verdicts and shell word semantics, with no command execution."""

from types import ModuleType
from typing import Any

import pytest

from tests.helpers.permission_cases import (
    BROAD_GRANTS,
    BROAD_GRANTS_B,
    GUARD_REGRESSION_CASES,
    SWEEP_CASES,
)

# Any: heterogeneous JSON policy at the gate's public seam.


@pytest.mark.parametrize("extra", [[], BROAD_GRANTS, BROAD_GRANTS_B], ids=["R", "A", "B"])
@pytest.mark.parametrize("label,command,expected", SWEEP_CASES, ids=[r[0] for r in SWEEP_CASES])
def test_sweep_verdict(
    permission_gate: ModuleType,
    gate_seed: dict[str, Any],
    extra: list[str],
    label: str,
    command: str,
    expected: str,
) -> None:
    """A broad grant cannot widen the model to authorize a corpus attack."""
    ledger = {**gate_seed, "grants": gate_seed["grants"] + [{"key": k} for k in extra]}
    actual, reason = permission_gate.verdict(command, ledger)
    assert actual == expected, (label, command, reason)


@pytest.mark.parametrize("extra", [[], BROAD_GRANTS_B], ids=["seed", "broad"])
@pytest.mark.parametrize(
    "label,command,expected,intended_change",
    GUARD_REGRESSION_CASES,
    ids=[r[0] for r in GUARD_REGRESSION_CASES],
)
def test_old_guards_survive(
    permission_gate: ModuleType,
    gate_seed: dict[str, Any],
    extra: list[str],
    label: str,
    command: str,
    expected: str,
    intended_change: str,
) -> None:
    """Every old guard stays explicit, including documented intentional changes."""
    ledger = {**gate_seed, "grants": gate_seed["grants"] + [{"key": k} for k in extra]}
    actual, reason = permission_gate.verdict(command, ledger)
    assert actual == expected, (label, command, intended_change, reason)
    assert bool(permission_gate.ask_match(command, ledger)) == (expected == "ask")


@pytest.mark.parametrize(
    "command,expected",
    [
        ("true", "allow"),
        ("false", "allow"),
        (":", "allow"),
        ("exit 0", "allow"),
        ("set -e", "allow"),
        ("set -u", "allow"),
        ("set -x", "allow"),
        ("set -o pipefail", "allow"),
        ("set pipefail", "none"),
        ("set -o", "none"),
        ("set -e pipefail", "none"),
        ("set -euo pipefail", "allow"),
        ("cd x && git status", "none"),
        ("pwd", "none"),
        ("printf x", "none"),
        ("read x", "none"),
        ("typeset x", "none"),
        ("export X=1", "none"),
        ("[ -f f ]", "none"),
        ("test -f f", "none"),
        ("which git", "none"),
        ("command -v git", "none"),
        ("sort f", "none"),
        ("sort -o out f", "none"),
        ("sort --compress-program=cmd f", "ask"),
        ("tar -cf host:a.tar x", "ask"),
        ("TAR_OPTIONS=x tar -tf a.tar", "none"),
        ("env --chdir=x ls", "ask"),
        ("watch 'ls -la'", "ask"),
        ("xargs wc -l", "ask"),
        ("PATH=tools; cat README.md", "none"),
        ("tools/cat README.md", "none"),
        ("./x", "none"),
        ("/usr/local/bin/dropdb db", "ask"),
        ("claude-temp/venv/bin/python -m mypy .", "ask"),
        ("unzip a.zip", "none"),
        ("uniq input output", "none"),
        ("echo x > result.txt", "none"),
        ("echo x > .git/hooks/pre-commit", "ask"),
        ("ln .claude/settings.json alias", "ask"),
        ("mv .claude/settings.json saved.json", "ask"),
        ("echo x 2>&1", "allow"),
        ("echo x >/dev/null", "allow"),
        ("cat < f", "allow"),
        ("cat <<'EOF'\n$(git status)\nEOF", "allow"),
        ("cat <<EOF\n$(git merge x)\nEOF", "ask"),
        ("echo $((1+2))", "none"),
        ('echo "$(git rev-parse HEAD)"', "none"),
        ("echo $((x))", "ask"),
        ("echo ${!x}", "ask"),
        ("typeset -n x=y", "ask"),
        ("mystery 'g\\i\\t merge x'", "ask"),
        ("mystery '$x'", "ask"),
        ("head -20 f", "allow"),
        ("tail -5 f", "allow"),
        ("git log -3", "allow"),
        ("git show -3", "allow"),
        ("git diff -3", "allow"),
    ],
)
def test_amendments_and_boundaries(
    permission_gate: ModuleType,
    gate_seed: dict[str, Any],
    command: str,
    expected: str,
) -> None:
    """Mandatory amendments override old builtin and archive examples."""
    actual, reason = permission_gate.verdict(command, gate_seed)
    assert actual == expected, reason


@pytest.mark.parametrize("head", "pip pip3 brew apt apt-get gem conda mamba pipenv".split())
def test_installers_ask(permission_gate: ModuleType, head: str) -> None:
    """Installer broad grants cannot create a form."""
    assert permission_gate.verdict(head + " install x", {"allow_keys": {head: "broad"}})[0] == "ask"


@pytest.mark.parametrize("head", ["git", "python3", "mypy", "brew", "unknown"])
@pytest.mark.parametrize("option", ["--help", "--version"])
def test_accepted_version_deviation(permission_gate: ModuleType, head: str, option: str) -> None:
    """The brief explicitly accepts deferring version and help queries."""
    assert permission_gate.verdict(f"{head} {option}", {})[0] == "none"


@pytest.mark.parametrize(
    "command,argv",
    [
        (r'echo "a\q"', ("echo", r"a\q")),
        ("echo 'a\\q'", ("echo", r"a\q")),
        (r"echo a\q", ("echo", "aq")),
        ("gi\"\"t sta''tus", ("git", "status")),
        ("git sta\\\ntus", ("git", "status")),
        ("echo 'a\\\nb'", ("echo", "a\\\nb")),
        ("git status\rx", ("git", "status\rx")),
        (r'echo "\$x"', ("echo", "$x")),
    ],
)
def test_quote_state_matches_shell_words(
    permission_gate: ModuleType,
    command: str,
    argv: tuple[str, ...],
) -> None:
    """Backslash rules depend on quote context; carriage return is never whitespace."""
    parts = permission_gate.model_command(command)
    assert len(parts) == 1
    assert parts[0].argv == argv
    assert all(word.literal for word in parts[0].words)


@pytest.mark.parametrize("command", ["echo '$(x)'", r'echo "\$(x)"', "echo '`x`'"])
def test_literal_payload_stays_data(permission_gate: ModuleType, command: str) -> None:
    """Quoted payload-looking text is not recursively evaluated on an inert reader."""
    assert permission_gate.verdict(command, {"allow_keys": {"echo": "read"}})[0] == "allow"
    assert len(permission_gate.model_command(command)) == 1
