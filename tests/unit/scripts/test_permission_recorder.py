"""Grant derivation and promotion with in-memory recorder collaborators."""

import json
from io import StringIO
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


def test_opaque_substitution_leaves_entire_ledger_untouched(
    permission_recorder: ModuleType, recorder_effects: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Approving an opaque body must not grant the apparent od command after its closer."""
    command = 'echo "it\'s $(echo ")") ; wc -l; echo "'
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


@pytest.mark.parametrize("command", ["echo ready | wc -l", "wc -l"])
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
                "key": "wc",
                "note": "auto-recorded: user approved this command",
                "example": "wc -l",
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
    payload: dict[str, object] = {"tool_name": "Bash", "tool_input": {"command": "wc -l"}}
    if mode is not None:
        payload["permission_mode"] = mode
    monkeypatch.setattr(permission_recorder.sys, "stdin", StringIO(json.dumps(payload)))
    permission_recorder.main()
    if mode in ("default", "acceptEdits", "plan"):
        assert recorder_effects.ledger["grants"][-1]["key"] == "wc"
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


@pytest.mark.parametrize(
    "command",
    [
        "od -c",
        "git status",
        "python3 script.py",
        "brew --version",
        "codex login",
        "git worktree add /tmp/worktree feature",
        "timeout -s KILL 5 git status",
        "mkdir d",
        "tee out",
        "sort f",
        "printf x",
        "python3 -m pytest",
        "wc $f",
        "wc -l > out",
        "X=1 wc -l",
        "PATH=tools; wc -l",
        "./wc -l",
        "wc -l; git status",
        "wc -l; unknown",
        "wc -l; echo $(od -c)",
        'echo "$(wc -l)"',
        "wc -l; git merge x",
    ],
)
def test_nonpromotable_part_blocks_entire_command(
    permission_recorder: ModuleType,
    recorder_effects: Mock,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    """Approval never teaches writers, project forms, unknown heads, or partial pipelines."""
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


@pytest.mark.parametrize("command,key", [("wc -l", "wc"), ("aws s3 ls", "aws s3 ls")])
def test_grant_key_comes_from_unkeyed_inert_form(
    permission_gate: ModuleType,
    permission_recorder: ModuleType,
    command: str,
    key: str,
) -> None:
    """Operation-scoped inert keys are derived without leaking input arguments."""
    forms = permission_gate.unkeyed_inert_forms(command, {})
    assert forms is not None and len(forms) == 1
    assert permission_recorder._grant_key(forms[0][0]) == key


def test_repeat_approval_does_not_duplicate_grant(
    permission_recorder: ModuleType,
    recorder_effects: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already keyed reader remains a no-op in every persistence collaborator."""
    monkeypatch.setattr(
        permission_recorder.sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "tool_name": "Bash",
                    "permission_mode": "default",
                    "tool_input": {"command": "echo x"},
                }
            )
        ),
    )
    permission_recorder.main()
    assert recorder_effects.ledger == {"grants": [{"key": "echo"}]}
    recorder_effects.write.assert_not_called()
    recorder_effects.replace.assert_not_called()
