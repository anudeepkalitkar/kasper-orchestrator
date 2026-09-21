"""Visibility and status reporting with processes, paths, and streams faked in memory."""

import io
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import ANY, Mock

import pytest


@pytest.mark.parametrize(
    ("output", "returncode", "expected"),
    [
        ("PRIVATE\n", 0, True),
        (" \tPRIVATE\r\n", 0, True),
        ("PUBLIC", 0, False),
        ("INTERNAL", 0, False),
        ("", 0, False),
        (" \n", 0, False),
        ("private", 0, False),
        ("PRIVATE extra", 0, False),
        ("PRIVATE\n", 1, False),
    ],
)
def test_only_successful_private_visibility_enables_tracking(
    project_dirs: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    returncode: int,
    expected: bool,
) -> None:
    """Unknown, failed, or non-private responses must keep ADRs local-only."""
    monkeypatch.setenv("GH_REPO", "other/private-repo")
    run = Mock(return_value=subprocess.CompletedProcess([], returncode, stdout=output))
    monkeypatch.setattr(project_dirs.subprocess, "run", run)
    root = Path("/fake/project with spaces")

    assert project_dirs._is_private_repo(root) is expected
    run.assert_called_once_with(
        ["gh", "repo", "view", "--json", "visibility", "-q", ".visibility"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=5.0,
        env=ANY,
    )
    assert "GH_REPO" not in run.call_args.kwargs["env"]
    assert project_dirs.VISIBILITY_TIMEOUT_S == 5.0


@pytest.mark.parametrize("error", [FileNotFoundError("gh"), subprocess.TimeoutExpired("gh", 5)])
def test_unavailable_visibility_keeps_adrs_local_only(
    project_dirs: ModuleType, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """A missing CLI or timed-out lookup uses the safe fallback."""
    monkeypatch.setattr(project_dirs.subprocess, "run", Mock(side_effect=error))
    assert project_dirs._is_private_repo(Path("/fake/project")) is False


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("launch failed")])
def test_visibility_launch_errors_keep_adrs_local_only_and_report_the_error(
    project_dirs: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: OSError,
) -> None:
    """Launch failures preserve local-only ADRs and explain the fallback on stderr."""
    monkeypatch.setattr(project_dirs.subprocess, "run", Mock(side_effect=error))

    assert project_dirs._is_private_repo(Path("/fake/project")) is False
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"Repo visibility unknown ({type(error).__name__}); ADRs stay local-only.\n"
    )


def test_visibility_launch_error_with_broken_stderr_keeps_adrs_local_only(
    project_dirs: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken diagnostic stream must not propagate or undo the safe fallback."""
    monkeypatch.setattr(
        project_dirs.subprocess, "run", Mock(side_effect=PermissionError("denied"))
    )
    stderr = SimpleNamespace(write=Mock(side_effect=BrokenPipeError("broken pipe")))
    monkeypatch.setattr(sys, "stderr", stderr)

    assert project_dirs._is_private_repo(Path("/fake/project")) is False
    stderr.write.assert_called_once()


def test_visibility_filters_gh_repo_without_changing_the_parent_environment(
    project_dirs: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ignore another repo's override while preserving unrelated child environment values."""
    monkeypatch.setenv("GH_REPO", "other/private-repo")
    monkeypatch.setenv("KASPER_TEST_ENV", "preserved")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="PUBLIC\n"))
    monkeypatch.setattr(project_dirs.subprocess, "run", run)
    root = Path("/fake/public-checkout")

    assert project_dirs._is_private_repo(root) is False

    run.assert_called_once()
    assert run.call_args.kwargs["cwd"] == root
    child_env = run.call_args.kwargs["env"]
    assert "GH_REPO" not in child_env
    assert child_env["KASPER_TEST_ENV"] == "preserved"
    assert os.environ["GH_REPO"] == "other/private-repo"


def test_non_repository_does_not_query_visibility(
    project_dirs: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No git repository means no exclusion work or GitHub lookup."""
    monkeypatch.setattr(project_dirs, "_exclude_file", Mock(return_value=None))
    run = Mock(side_effect=AssertionError("unexpected subprocess"))
    monkeypatch.setattr(project_dirs.subprocess, "run", run)

    assert project_dirs._ensure_excluded(Path("/fake/project")) is None
    run.assert_not_called()


@pytest.mark.parametrize(
    ("private", "expected"),
    [(True, "ADRs: tracked (private repo)\n"), (False, "ADRs: local-only\n"), (None, "")],
)
def test_main_reports_adr_policy_only_for_git_repositories(
    project_dirs: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    private: bool | None,
    expected: str,
) -> None:
    """Session context states the selected policy, with every external effect replaced."""
    root = Path("/fake/project")
    monkeypatch.setattr(
        project_dirs,
        "sys",
        SimpleNamespace(stdin=io.StringIO('{"cwd":"/fake/project"}'), stderr=sys.stderr),
    )
    monkeypatch.setattr(Path, "resolve", lambda path: path)
    monkeypatch.setattr(Path, "home", Mock(return_value=Path("/fake/home")))
    monkeypatch.setattr(Path, "mkdir", Mock())
    monkeypatch.setattr(project_dirs, "_link_harness_memory", Mock())
    monkeypatch.setattr(project_dirs, "_exclude_file", Mock(return_value=None))
    excluded = Mock(return_value=private)
    monkeypatch.setattr(project_dirs, "_ensure_excluded", excluded)
    monkeypatch.setattr(
        project_dirs.subprocess, "run", Mock(side_effect=AssertionError("unexpected subprocess"))
    )

    project_dirs.main()

    excluded.assert_called_once_with(root)
    captured = capsys.readouterr()
    assert captured.out == expected
    assert captured.err == ""
