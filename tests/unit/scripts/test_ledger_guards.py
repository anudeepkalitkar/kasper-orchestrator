"""Seed coverage, closed form boundaries, and precedence at the hook entrypoint."""

import json
from io import StringIO
from types import ModuleType
from typing import Any
from unittest.mock import Mock

import pytest

from tests.helpers.permission_cases import FEATURE_PUSH_CASES, HOOK_BYPASS_CASES
from tests.helpers.permission_forms import FORM_EXAMPLES, forbidden_cases

# Any: ledger values are heterogeneous JSON, exactly as accepted by the public seam.


@pytest.mark.parametrize("key,command", FORM_EXAMPLES.items(), ids=list(FORM_EXAMPLES))
def test_every_seeded_form_has_positive_example(
    permission_gate: ModuleType,
    gate_seed: dict[str, Any],
    key: str,
    command: str,
) -> None:
    """The independently transcribed form must be present and usable on the seed."""
    assert key in gate_seed["allow_keys"]
    actual, reason = permission_gate.verdict(command, gate_seed)
    assert actual == "allow", reason


def test_seed_names_exactly_reviewed_forms(gate_seed: dict[str, Any]) -> None:
    """Writer, installer and whole-interpreter keys must never enter the seed table."""
    assert set(gate_seed["allow_keys"]) == set(FORM_EXAMPLES)


@pytest.mark.parametrize("key,command,expected", forbidden_cases())
def test_forbidden_option_classes(
    permission_gate: ModuleType,
    gate_seed: dict[str, Any],
    key: str,
    command: str,
    expected: str,
) -> None:
    """Config, execution and write options remain excluded on every applicable form."""
    actual, reason = permission_gate.verdict(command, gate_seed)
    assert actual == expected, (key, command, reason)


@pytest.mark.parametrize("key,command", FORM_EXAMPLES.items(), ids=list(FORM_EXAMPLES))
def test_no_seeded_form_can_redirect_to_a_file(
    permission_gate: ModuleType,
    gate_seed: dict[str, Any],
    key: str,
    command: str,
) -> None:
    """The output role is forbidden for every form, even with its standing grant."""
    guarded = key.split()[0] in {
        "git",
        "gh",
        "aws",
        "docker",
        "terraform",
        "python3",
        "python",
        "pytest",
        "mypy",
        "ruff",
        "sed",
        "awk",
    }
    assert permission_gate.verdict(command + " > result.txt", gate_seed)[0] == (
        "ask" if guarded else "none"
    )
    assert permission_gate.verdict(command + " > .claude/settings.json", gate_seed)[0] == "ask"


@pytest.mark.parametrize("command,guarded", HOOK_BYPASS_CASES)
def test_hook_bypasses_still_ask(
    permission_gate: ModuleType,
    gate_seed: dict[str, Any],
    command: str,
    guarded: bool,
) -> None:
    """Every pre-existing bypass keeps its exact seed verdict."""
    assert permission_gate.verdict(command, gate_seed)[0] == ("ask" if guarded else "allow")


@pytest.mark.parametrize("command,allowed", FEATURE_PUSH_CASES)
def test_feature_push_scope(
    permission_gate: ModuleType,
    gate_seed: dict[str, Any],
    command: str,
    allowed: bool,
) -> None:
    """Only feature branch pushes match the reviewed exception."""
    assert permission_gate.verdict(command, gate_seed)[0] == ("allow" if allowed else "ask")


@pytest.mark.parametrize("source", ["allow_keys", "grants", "allow_patterns"])
@pytest.mark.parametrize(
    "command,expected",
    [
        ("python3 -c code", "ask"),
        ("git merge x", "ask"),
        ("terraform plan", "ask"),
        ("mkdir d", "none"),
        ("tee out", "none"),
        ("sort f", "none"),
        ("mystery x", "none"),
    ],
)
def test_broad_authorization_never_widens_forms(
    permission_gate: ModuleType,
    source: str,
    command: str,
    expected: str,
) -> None:
    """A migrated grant or catch-all pattern selects within the closed table only."""
    ledger: dict[str, Any] = {
        source: (
            {command.split()[0]: "broad"}
            if source == "allow_keys"
            else [{"key": command.split()[0]}]
            if source == "grants"
            else [{"pattern": ".*"}]
        )
    }
    assert permission_gate.verdict(command, ledger)[0] == expected


@pytest.mark.parametrize("source", ["ask_patterns", "denials"])
def test_regex_guard_wins_over_key(permission_gate: ModuleType, source: str) -> None:
    """Raw and dequoted part views both preserve the regex guard layer."""
    ledger = {
        "allow_keys": {"git status": "read"},
        source: [{"pattern": "git status", "note": "no"}],
    }
    assert permission_gate.verdict('g""it status', ledger) == ("ask", "no")


@pytest.mark.parametrize(
    "command,expected",
    [
        ("git status", "allow"),
        ("git merge x", "ask"),
        ("unknown_literal", "none"),
    ],
)
def test_hook_emits_three_verdict_contract(
    permission_gate: ModuleType,
    permission_unknown_log: Mock,
    gate_seed: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    expected: str,
) -> None:
    """None emits no decision and logs; ask/allow emit explicit hook JSON."""
    monkeypatch.setattr(permission_gate, "load_ledger", lambda: gate_seed)
    monkeypatch.setattr(
        permission_gate.sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": command},
                }
            )
        ),
    )
    permission_gate.main()
    output = capsys.readouterr().out
    if expected == "none":
        assert output == ""
        permission_unknown_log.open.assert_called_once_with("a", encoding="utf-8")
    else:
        assert json.loads(output)["hookSpecificOutput"]["permissionDecision"] == expected
        permission_unknown_log.open.assert_not_called()


def test_word_rules_keep_each_required_guard(gate_seed: dict[str, Any]) -> None:
    """The grammar supplements the required word rules; it does not replace them."""
    rules = gate_seed["ask_words"]
    assert {"git", "gh", "aws", "docker", "terraform", "dropdb"} <= {r["tool"] for r in rules}
    assert any(r["tool"] == "gh" and r["all"] == [["pr"], ["merge"]] for r in rules)
    assert any(
        r["tool"] == "aws"
        and {"cp", "mv", "sync", "run-", "invoke-", "delete-"} <= set(r["all"][0])
        for r in rules
    )
    assert [r["all"] for r in rules if r["tool"] == "terraform"] == [
        [["apply", "destroy", "import", "taint", "untaint", "force-unlock", "console", "test"]],
        [["state"], ["rm", "mv", "push", "pull", "replace-provider"]],
        [["workspace"], ["delete", "new", "select"]],
    ]
    for rule in rules:
        assert isinstance(rule["tool"], str) and rule["tool"]
        assert isinstance(rule["all"], list)
        for group in rule["all"]:
            assert isinstance(group, list) and group
            assert all(isinstance(word, str) and word for word in group)
    assert r"\bgh\s+pr\s+merge\b" in {r["pattern"] for r in gate_seed["ask_patterns"]}
