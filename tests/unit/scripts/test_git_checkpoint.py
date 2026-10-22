"""Checkpoint policy checks without subprocesses or filesystem access."""

from io import StringIO
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize(
    "path",
    [
        "tasks/a.md",
        "tasks/nested/a.md",
        "claude-temp/a",
        "claude-memory/a",
        "docs/adr/a",
        ".env",
        ".env.local",
        ".env.",
        "app/.env",
        "a/b/.env.production",
        "tasks/.env.example",
    ],
)
def test_local_state_and_env_files_are_forbidden(checkpoint: ModuleType, path: str) -> None:
    """Local directories remain forbidden even for otherwise exempt templates."""
    assert checkpoint.is_forbidden(path)


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        ".env.sample",
        ".env.template",
        "app/.env.example",
        "a/b/.env.sample",
        "a/b/.env.template",
        "tasks-old/a",
        "docs/adrs/a",
        "src/tasks/a",
        "environment",
        "app.env",
        "src/main.py",
    ],
)
def test_templates_and_similarly_named_paths_are_allowed(
    checkpoint: ModuleType,
    path: str,
) -> None:
    """The guard respects exact root directory prefixes and template basenames."""
    assert not checkpoint.is_forbidden(path)


@pytest.mark.parametrize("branch", ["", "main", "MAIN", "Master", "DEVELOPMENT", "Qa"])
def test_unsafe_branch_refused_before_any_staging(
    checkpoint: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    branch: str,
) -> None:
    """Detached and case-insensitive shared branches refuse before querying changes."""
    operation = checkpoint.GitCheckpoint()
    monkeypatch.setattr(operation, "current_branch", lambda: branch)
    run = Mock(side_effect=AssertionError("must not run Git"))
    monkeypatch.setattr(operation, "_run", run)
    assert operation.run("message") == 1
    assert (branch or "detached HEAD") in capsys.readouterr().err
    run.assert_not_called()


@pytest.mark.parametrize(
    ("argument", "stdin", "expected"),
    [
        (None, 'quoted "text" $( ) `literal`\n', 'quoted "text" $( ) `literal`'),
        ("explicit", "ignored", "explicit"),
        (None, " \n", None),
        ("", "ignored", None),
    ],
)
def test_message_argument_or_stdin(
    checkpoint: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    argument: str | None,
    stdin: str,
    expected: str | None,
) -> None:
    """Argument precedence and empty input are independent of shell parsing."""
    monkeypatch.setattr(checkpoint.sys, "stdin", StringIO(stdin))
    assert checkpoint.resolve_message(argument) == expected


@pytest.mark.parametrize("staged", [["stray.py"], ["allowed.py", "stray.py"]])
def test_only_refuses_preexisting_strays_without_staging(
    checkpoint: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    staged: list[str],
) -> None:
    """An unrelated staged file stays untouched and is named in the refusal."""
    operation = checkpoint.GitCheckpoint(push=False, only=["allowed.py"])
    monkeypatch.setattr(operation, "staged_paths", lambda: staged)
    run = Mock()
    monkeypatch.setattr(operation, "_run", run)
    assert operation.stage_only(["allowed.py"], False) is None
    assert "stray.py" in capsys.readouterr().err
    run.assert_not_called()


@pytest.mark.parametrize("staged", [[], ["stray.py"], ["allowed.py", "stray.py"], ["allowed.py"]])
def test_only_requires_nonempty_subset_after_staging(
    checkpoint: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    staged: list[str],
) -> None:
    """The post-add check catches empty and widened indexes but accepts a subset."""
    operation = checkpoint.GitCheckpoint(push=False)
    monkeypatch.setattr(operation, "staged_paths", Mock(side_effect=[[], staged]))
    run = Mock()
    monkeypatch.setattr(operation, "_run", run)
    assert operation.stage_only(["allowed.py", "unchanged.py"], False) == (
        staged if staged == ["allowed.py"] else None
    )
    run.assert_called_once_with(["add", "--", "allowed.py", "unchanged.py"])


@pytest.mark.parametrize("local", [True, False, None])
def test_adr_policy_uses_shared_answer_and_fails_closed(
    checkpoint: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    local: bool | None,
) -> None:
    """A missing repository answer cannot accidentally publish local ADRs."""
    operation = checkpoint.GitCheckpoint(push=False)
    monkeypatch.setattr(operation, "_run", Mock(return_value="/project"))
    monkeypatch.setattr(checkpoint, "adr_local_only", Mock(return_value=local))
    assert operation.adr_local() is (local is not False)
    assert checkpoint.is_forbidden("docs/adr/decision.md", operation.adr_local()) is (
        local is not False
    )


@pytest.mark.parametrize("path", ["tasks/task.md", ".env", "docs/adr/decision.md"])
def test_only_refuses_forbidden_before_index_access(
    checkpoint: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """Explicit selection does not bypass local-state protection."""
    operation = checkpoint.GitCheckpoint(push=False)
    run = Mock(side_effect=AssertionError("forbidden selection reached git"))
    monkeypatch.setattr(operation, "_run", run)
    assert operation.stage_only([path], True) is None
    run.assert_not_called()
