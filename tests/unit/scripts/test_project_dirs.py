"""Visibility and status reporting with processes, paths, and streams faked in memory."""

import io
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import ANY, Mock

import pytest

from tests.helpers.hooks import load_script


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
    monkeypatch.setattr(project_dirs.subprocess, "run", Mock(side_effect=PermissionError("denied")))
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
    monkeypatch.setattr(project_dirs, "project_root", Mock(return_value=root))
    monkeypatch.setattr(
        project_dirs,
        "sys",
        SimpleNamespace(stdin=io.StringIO('{"cwd":"/fake/project"}'), stderr=sys.stderr),
    )
    monkeypatch.setattr(Path, "resolve", lambda path: path)
    monkeypatch.setattr(Path, "home", Mock(return_value=Path("/fake/home")))
    monkeypatch.setattr(Path, "mkdir", Mock())
    monkeypatch.setattr(Path, "is_file", lambda path: False)
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


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/docs/adr/", True),
        ("docs/adr/", True),
        ("docs/adr", True),
        ("  /docs/adr/  ", True),
        ("# /docs/adr/", False),
        ("docs/adrs/", False),
        ("", False),
    ],
)
def test_adr_exclude_equivalence(
    project_dirs: ModuleType, monkeypatch: pytest.MonkeyPatch, line: str, expected: bool
) -> None:
    """The hook and checkpoint share exact slash-normalized entries."""
    monkeypatch.setattr(project_dirs, "_exclude_file", lambda root: Path("/exclude"))
    monkeypatch.setattr(Path, "is_file", lambda path: True)
    monkeypatch.setattr(Path, "read_bytes", lambda path: line.encode())
    assert project_dirs._entry_present([line], "/docs/adr/") is expected
    assert project_dirs.adr_local_only(Path("/project")) is expected


def test_adr_off_repo_is_unknown(project_dirs: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Off-repository is distinct from a repository that tracks ADRs."""
    monkeypatch.setattr(project_dirs, "_exclude_file", lambda root: None)
    assert project_dirs.adr_local_only(Path("/project")) is None


@pytest.mark.parametrize("occupied", [0, 1, 2, 3])
def test_memory_backup_chooses_first_free_name(
    project_dirs: ModuleType, monkeypatch: pytest.MonkeyPatch, occupied: int
) -> None:
    """Existing original and backup names can never be overwritten."""
    names = ["note.md", "note.from-harness.md", "note.from-harness-2.md", "note.from-harness-3.md"]
    monkeypatch.setattr(Path, "exists", lambda path: path.name in names[:occupied])
    assert project_dirs._free_name(Path("/memory"), "note.md") == Path("/memory") / names[occupied]


@pytest.mark.parametrize("on", [True, False])
def test_session_start_prints_overnight_header_only_when_on(
    project_dirs: ModuleType, monkeypatch: pytest.MonkeyPatch, on: bool
) -> None:
    """A new session sees the old session id and deadline so it can end an abandoned night."""
    text = f"overnight: {'on' if on else 'off'}\nsession: previous\nuntil: 2026-10-23T01:00\n"
    monkeypatch.setattr(Path, "is_file", lambda path: True)
    monkeypatch.setattr(Path, "read_bytes", lambda path: text.encode())
    assert project_dirs.overnight_line(Path("/project")) == (
        "Overnight: on (session previous, until 2026-10-23T01:00)" if on else None
    )


@pytest.mark.parametrize("override", [None, "/stable/project"])
def test_project_root_prefers_session_override_then_git(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, override: str | None
) -> None:
    """A nested cwd must not redirect state away from the stable session root."""
    module = load_script(repo_root, "project_dirs")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    if override is not None:
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", override)
    run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="/git/top\n"))
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.project_root("/git/top/package") == Path(override or "/git/top")
    if override is not None:
        run.assert_not_called()
    else:
        run.assert_called_once_with(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path("/git/top/package"),
            capture_output=True,
            text=True,
            check=True,
            timeout=5.0,
        )


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("git"),
        subprocess.CalledProcessError(128, "git"),
        subprocess.TimeoutExpired("git", 5),
    ],
)
@pytest.mark.parametrize("cwd", [None, "/payload"])
def test_project_root_falls_back_to_cwd_when_git_unavailable(
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    cwd: str | None,
) -> None:
    """Off-repository payloads and missing payload cwd retain their own directory."""
    module = load_script(repo_root, "project_dirs")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setattr(Path, "cwd", lambda: Path("/process"))
    monkeypatch.setattr(module.subprocess, "run", Mock(side_effect=error))
    assert module.project_root(cwd) == Path(cwd or "/process")
