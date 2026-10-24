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


@pytest.mark.parametrize("operand", ["feat: x", "removed.py"])
@pytest.mark.parametrize("stdin", ["checkpoint message", ""])
def test_only_unknown_operand_refuses_without_staging(
    checkpoint: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operand: str,
    stdin: str,
) -> None:
    """A trailing message or a file removed from index and worktree cannot be selected."""
    monkeypatch.setattr(checkpoint.argparse, "_", lambda message: message)
    monkeypatch.setattr(checkpoint.sys, "stdin", StringIO(stdin))
    monkeypatch.setattr(checkpoint, "resolved_mainline", lambda: "trunk")
    monkeypatch.setattr(checkpoint.Path, "exists", lambda path: str(path) == "README.md")
    monkeypatch.setattr(checkpoint.GitCheckpoint, "current_branch", lambda self: "feat/test")
    run = Mock(return_value="")
    monkeypatch.setattr(checkpoint.GitCheckpoint, "_run", run)
    assert checkpoint.main(["--no-push", "--only", "README.md", operand]) == 1
    error = capsys.readouterr().err
    assert operand in error
    assert "--message-file" in error
    assert "stdin" in error
    assert len(run.call_args_list) == 1
    command = run.call_args.args[0]
    assert command[0] == "ls-files"
    assert command[command.index("--") + 1 :] == [operand]


@pytest.mark.parametrize("exists", [True, False])
def test_only_existing_or_tracked_deleted_file_can_be_committed(
    checkpoint: ModuleType, monkeypatch: pytest.MonkeyPatch, exists: bool
) -> None:
    """A feature branch can checkpoint an existing file or a tracked deletion."""
    operation = checkpoint.GitCheckpoint(push=False, only=["allowed.py"])
    monkeypatch.setattr(checkpoint, "resolved_mainline", lambda: "trunk")
    monkeypatch.setattr(checkpoint.Path, "exists", lambda path: exists)
    monkeypatch.setattr(operation, "current_branch", lambda: "feat/test")
    monkeypatch.setattr(operation, "adr_local", lambda: False)
    monkeypatch.setattr(operation, "staged_paths", Mock(side_effect=[[], ["allowed.py"]]))
    run = Mock(return_value="allowed.py\0")
    monkeypatch.setattr(operation, "_run", run)
    assert operation.run("record change") == 0
    commands = [call.args[0] for call in run.call_args_list]
    assert ["add", "--", "allowed.py"] in commands
    assert ["commit", "-m", "record change"] in commands
    assert sum(command[0] == "ls-files" for command in commands) == (0 if exists else 1)


@pytest.mark.parametrize("branch", ["trunk", "main", "master", "development", "qa"])
@pytest.mark.parametrize("method", ["run", "refusal"])
def test_mainline_and_fixed_names_refused_before_staging(
    checkpoint: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    branch: str,
    method: str,
) -> None:
    """Fixed names remain protected without resolution and are checked before resolving."""
    resolution = Mock(return_value="trunk" if branch == "trunk" else None)
    monkeypatch.setattr(checkpoint, "resolved_mainline", resolution)
    operation = checkpoint.GitCheckpoint(only=["allowed.py"])
    monkeypatch.setattr(operation, "current_branch", lambda: branch)
    run = Mock(side_effect=AssertionError("refusal must precede Git access"))
    monkeypatch.setattr(operation, "_run", run)
    monkeypatch.setattr(
        operation, "unknown_paths", Mock(side_effect=AssertionError("must refuse first"))
    )
    result = operation.run("record change") if method == "run" else operation.refusal()
    assert result == 1
    assert branch in capsys.readouterr().err
    run.assert_not_called()
    if branch == "trunk":
        resolution.assert_called_once_with()
    else:
        resolution.assert_not_called()


@pytest.mark.parametrize("result", ["trunk", None])
def test_resolved_mainline_preserves_resolver_answer(
    checkpoint: ModuleType, monkeypatch: pytest.MonkeyPatch, result: str | None
) -> None:
    """Resolution delegates to the shared mainline policy without invoking Git here."""
    resolver = Mock(return_value=result)
    monkeypatch.setattr(checkpoint, "mainline", resolver)
    assert checkpoint.resolved_mainline() == result
    resolver.assert_called_once_with()


@pytest.mark.parametrize("timeout", [False, True])
def test_resolved_mainline_tolerates_git_launch_failure_and_timeout(
    checkpoint: ModuleType, monkeypatch: pytest.MonkeyPatch, timeout: bool
) -> None:
    """Unavailable Git and timed-out resolution leave the fixed protections usable."""
    error = checkpoint.subprocess.TimeoutExpired("git", 10) if timeout else OSError("no git")
    monkeypatch.setattr(checkpoint, "mainline", Mock(side_effect=error))
    assert checkpoint.resolved_mainline() is None


def test_unknown_paths_batches_missing_operands_and_preserves_order(
    checkpoint: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing paths need no index lookup; tracked descendants cover deleted directories."""
    monkeypatch.setattr(checkpoint.Path, "exists", lambda path: str(path) == "existing.py")
    operation = checkpoint.GitCheckpoint()
    run = Mock(return_value="deleted.py\0directory/child.py\0prefix-other.py\0")
    monkeypatch.setattr(operation, "_run", run)
    missing = ["unknown.py", "deleted.py", "directory", "prefix"]
    assert operation.unknown_paths(["existing.py", *missing]) == ["unknown.py", "prefix"]
    run.assert_called_once()
    command = run.call_args.args[0]
    # Git parses every token after -- as a pathspec, including tokens starting with '-'.
    separator = command.index("--")
    assert command[0] == "ls-files"
    assert "-z" in command[1:separator]
    assert command[separator + 1 :] == missing


@pytest.mark.parametrize("unknown", [True, False])
def test_operand_refusal_precedes_unreadable_message_file(
    checkpoint: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unknown: bool,
) -> None:
    """Bad operands win over file errors; valid operands still expose unreadable messages."""
    monkeypatch.setattr(checkpoint.argparse, "_", lambda message: message)
    monkeypatch.setattr(checkpoint, "resolved_mainline", lambda: "trunk")
    monkeypatch.setattr(checkpoint.GitCheckpoint, "current_branch", lambda self: "feat/test")
    monkeypatch.setattr(checkpoint.Path, "exists", lambda path: str(path) == "a")
    read = Mock(side_effect=PermissionError("unreadable message"))
    monkeypatch.setattr(checkpoint.Path, "read_text", read)
    run = Mock(return_value="")
    monkeypatch.setattr(checkpoint.GitCheckpoint, "_run", run)
    args = ["--message-file", "unreadable", "--only", "a"]
    if unknown:
        args.append("msg")
    assert checkpoint.main(args) == (1 if unknown else 2)
    error = capsys.readouterr().err
    if unknown:
        assert "msg" in error
        assert "--only operands" in error
        assert "Cannot read" not in error
        read.assert_not_called()
        run.assert_called_once_with(["ls-files", "-z", "--", "msg"], capture=True, strip=False)
    else:
        assert "Cannot read the commit message file" in error
        read.assert_called_once_with(encoding="utf-8")
        run.assert_not_called()


def test_valid_only_without_message_reports_missing_message(
    checkpoint: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Passing operand validation does not permit a commit with an empty message."""
    monkeypatch.setattr(checkpoint.argparse, "_", lambda message: message)
    monkeypatch.setattr(checkpoint.sys, "stdin", StringIO(""))
    monkeypatch.setattr(checkpoint, "resolved_mainline", lambda: "trunk")
    monkeypatch.setattr(checkpoint.GitCheckpoint, "current_branch", lambda self: "feat/test")
    monkeypatch.setattr(checkpoint.Path, "exists", lambda path: True)
    run = Mock(side_effect=AssertionError("missing message must prevent staging"))
    monkeypatch.setattr(checkpoint.GitCheckpoint, "_run", run)
    assert checkpoint.main(["--no-push", "--only", "README.md"]) == 2
    assert "A commit message is required" in capsys.readouterr().err
    run.assert_not_called()


@pytest.mark.parametrize("branch", ["", "feat/test"])
def test_refusal_alone_never_stages_or_requires_a_message(
    checkpoint: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    branch: str,
) -> None:
    """Detached HEAD refuses; a valid feature branch passes without invoking commit work."""
    operation = checkpoint.GitCheckpoint(only=["README.md"])
    monkeypatch.setattr(operation, "current_branch", lambda: branch)
    monkeypatch.setattr(checkpoint, "resolved_mainline", lambda: "trunk")
    monkeypatch.setattr(checkpoint.Path, "exists", lambda path: True)
    run = Mock(side_effect=AssertionError("refusal must not stage"))
    monkeypatch.setattr(operation, "_run", run)
    assert operation.refusal() == (None if branch else 1)
    assert ("detached HEAD" in capsys.readouterr().err) is (not branch)
    run.assert_not_called()


@pytest.mark.parametrize("push", [True, False])
def test_commit_alone_stages_commits_and_optionally_pushes(
    checkpoint: ModuleType, monkeypatch: pytest.MonkeyPatch, push: bool
) -> None:
    """After refusal passes, commit preserves the message and obeys the push setting."""
    operation = checkpoint.GitCheckpoint(push=push, only=["a"])
    monkeypatch.setattr(operation, "current_branch", lambda: "feat/test")
    monkeypatch.setattr(operation, "adr_local", lambda: False)
    monkeypatch.setattr(operation, "staged_paths", Mock(side_effect=[[], ["a"]]))
    monkeypatch.setattr(
        operation, "refusal", Mock(side_effect=AssertionError("already validated"))
    )
    run = Mock(return_value="")
    monkeypatch.setattr(operation, "_run", run)
    assert operation.commit('feat: "literal" message') == 0
    expected = [["add", "--", "a"], ["commit", "-m", 'feat: "literal" message']]
    if push:
        expected.append(["push", "-u", "origin", "feat/test"])
    assert [call.args[0] for call in run.call_args_list] == expected


@pytest.mark.parametrize("clean", [True, False])
def test_commit_alone_stops_for_clean_tree_or_staging_refusal(
    checkpoint: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    clean: bool,
) -> None:
    """Neither a clean tree nor rejected staging can reach commit or push."""
    operation = checkpoint.GitCheckpoint()
    monkeypatch.setattr(operation, "current_branch", lambda: "feat/test")
    monkeypatch.setattr(operation, "has_changes", lambda: not clean)
    monkeypatch.setattr(operation, "adr_local", lambda: False)
    stage = Mock(return_value=None)
    monkeypatch.setattr(operation, "stage_all", stage)
    run = Mock(side_effect=AssertionError("must not commit or push"))
    monkeypatch.setattr(operation, "_run", run)
    assert operation.commit("record change") == (0 if clean else 1)
    if clean:
        assert "working tree is clean" in capsys.readouterr().out
        stage.assert_not_called()
    else:
        stage.assert_called_once_with(False)
    run.assert_not_called()
