"""Grant derivation and promotion with in-memory recorder collaborators."""

import json
from io import StringIO
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("brew --version", "brew"),
        ("codex login", "codex"),
        ("git worktree add /tmp/worktree feature", "git worktree"),
        ("python3 script.py", "python3"),
        ("timeout -s KILL 5 git status", "git status"),
    ],
)
def test_legitimate_heads_keep_their_grant_keys(
    permission_recorder: ModuleType, command: str, expected: str
) -> None:
    """Recognized heads retain the required operation prefix without argument leakage."""
    assert permission_recorder._grant_key(command) == expected


def test_opaque_substitution_marker_is_never_promoted(
    permission_recorder: ModuleType, permission_gate: ModuleType
) -> None:
    """A fail-closed parser marker must not become a standing ledger grant."""
    assert permission_recorder._grant_key(permission_gate._OPAQUE_SUBSTITUTION) is None


def test_opaque_substitution_leaves_entire_ledger_untouched(
    permission_recorder: ModuleType, recorder_effects: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Approving an opaque body must not grant the apparent od command after its closer."""
    command = 'echo "it\'s $(echo ")") ; od -c; echo "'
    monkeypatch.setattr(
        permission_recorder.sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "tool_name": "Bash",
                    "permission_mode": "default",
                    "tool_input": {"command": command},
                }
            )
        ),
    )

    permission_recorder.main()

    assert recorder_effects.ledger == {"grants": [{"key": "echo"}]}
    recorder_effects.write.assert_not_called()
    recorder_effects.replace.assert_not_called()


def test_unreadable_wrapper_never_creates_a_grant(
    permission_recorder: ModuleType, recorder_effects: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Human approval cannot teach a standing grant from an unreadable wrapper."""
    command = "xargs -Z git status"
    assert permission_recorder._grant_key(command) is None
    monkeypatch.setattr(
        permission_recorder.sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "tool_name": "Bash",
                    "permission_mode": "default",
                    "tool_input": {"command": command},
                }
            )
        ),
    )

    permission_recorder.main()

    assert recorder_effects.ledger == {"grants": [{"key": "echo"}]}
    recorder_effects.write.assert_not_called()
    recorder_effects.replace.assert_not_called()


@pytest.mark.parametrize("command", ["echo ready | od -c", 'echo "$(od -c)"'])
def test_real_unallowed_head_promotes_with_its_part_as_example(
    permission_recorder: ModuleType,
    recorder_effects: Mock,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    """Plain commands and readable substitutions preserve promotion of the real head."""
    monkeypatch.setattr(
        permission_recorder.sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "tool_name": "Bash",
                    "permission_mode": "default",
                    "tool_input": {"command": command},
                }
            )
        ),
    )

    permission_recorder.main()

    assert recorder_effects.ledger == {
        "grants": [
            {"key": "echo"},
            {
                "key": "od",
                "note": "auto-recorded: user approved this command",
                "example": "od -c",
                "added": "2026-09-18",
            },
        ]
    }
    recorder_effects.write.assert_called_once_with(
        json.dumps(recorder_effects.ledger, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    recorder_effects.replace.assert_called_once_with(
        Path("/fake/ledger.json.tmp"), Path("/fake/ledger.json")
    )


@pytest.mark.parametrize(
    "mode",
    ["default", "acceptEdits", "plan", "auto", "bypassPermissions", "dontAsk", "unknown", "", None],
)
def test_only_human_prompt_modes_record_grants(
    permission_recorder: ModuleType,
    recorder_effects: Mock,
    monkeypatch: pytest.MonkeyPatch,
    mode: str | None,
) -> None:
    """Missing and non-human modes leave both ledger and persistence untouched."""
    payload: dict[str, object] = {"tool_name": "Bash", "tool_input": {"command": "od -c"}}
    if mode is not None:
        payload["permission_mode"] = mode
    monkeypatch.setattr(permission_recorder.sys, "stdin", StringIO(json.dumps(payload)))
    permission_recorder.main()
    if mode in ("default", "acceptEdits", "plan"):
        assert recorder_effects.ledger["grants"][-1]["key"] == "od"
        recorder_effects.write.assert_called_once()
        recorder_effects.replace.assert_called_once()
    else:
        assert recorder_effects.ledger == {"grants": [{"key": "echo"}]}
        recorder_effects.write.assert_not_called()
        recorder_effects.replace.assert_not_called()


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan"])
def test_word_guarded_approval_never_promotes(
    permission_recorder: ModuleType,
    recorder_effects: Mock,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """Word rules alone prevent promotion even after a human approves execution."""
    ledger = {
        "grants": [],
        "ask_words": [{"tool": "git", "all": [["merge", "clean"]]}],
    }
    recorder_effects.ledger = ledger
    command = "git merge feature"
    assert permission_recorder._grant_key(command) == "git merge"
    assert permission_recorder.unallowed_parts(command, ledger) == [command]
    monkeypatch.setattr(
        permission_recorder.sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "tool_name": "Bash",
                    "permission_mode": mode,
                    "tool_input": {"command": command},
                }
            )
        ),
    )

    permission_recorder.main()

    assert ledger == {
        "grants": [],
        "ask_words": [{"tool": "git", "all": [["merge", "clean"]]}],
    }
    recorder_effects.write.assert_not_called()
    recorder_effects.replace.assert_not_called()
