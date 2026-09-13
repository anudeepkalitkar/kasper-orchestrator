"""Integration tier for ``.claude/scripts/project_dirs.py`` — exclude wiring and scratch.

``main()`` is never called **in this process**: it links ``~/.claude/projects/<slug>/memory``
through ``Path.home()``, which the script offers no argument to redirect, so an in-process
call would write into (and ``rmtree`` inside) the user's real global config. The pure
branching — ``_ensure_excluded``, ``_leftovers``, ``_session_id`` — is exercised directly,
and the few end-to-end facts that only ``main()`` can show (ADR-0007 §4: ``keep/`` and
``sessions/<id>/`` are created, the two report lines are printed, nothing is deleted) come
from running the script as its own process with ``HOME`` pointed at a temporary directory,
where the real global config is out of reach by construction.

Integration, not unit: real ``git init`` repositories and real bytes on disk.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from tests.helpers.cli import line_with
from tests.helpers.files import tree_bytes
from tests.helpers.hooks import load_script, run_script

#: What the hook adds, in order (``project_dirs.IGNORE_ENTRIES``).
ENTRIES = ["claude-memory/", "claude-temp/", "/tasks/", "/docs/adr/"]

#: The scratch root and the two names inside it the hook owns (``project_dirs`` constants).
SCRATCH = "claude-temp"
KEEP = "keep"
SESSIONS = "sessions"


@pytest.fixture
def project_dirs(repo_root: Path) -> ModuleType:
    """The hook script under test, loaded from its path in this checkout."""
    return load_script(repo_root, "project_dirs")


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    """The directory ``HOME`` points at for an end-to-end run — never the user's own."""
    return tmp_path / "home"


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """A project root holding an empty ``claude-temp/``, ready to be populated per test."""
    temp = tmp_path / "proj" / SCRATCH
    temp.mkdir(parents=True)
    return temp


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real scratch git repository — the hook asks git where its exclude file lives."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True, check=True)
    return tmp_path


def _exclude(repo: Path) -> Path:
    """The repository's local-only exclude file."""
    return repo / ".git/info/exclude"


def _lines(path: Path) -> list[str]:
    """The file's lines, newline style discarded."""
    return path.read_text(encoding="utf-8").splitlines()


def test_a_fresh_repo_gets_every_entry_in_the_local_exclude_file(
    project_dirs: ModuleType, repo: Path
) -> None:
    """All four entries land in ``.git/info/exclude``, appended after git's own header."""
    project_dirs._ensure_excluded(repo)
    lines = _lines(_exclude(repo))
    assert lines[-4:] == ENTRIES
    assert lines[0].startswith("# git ls-files")


def test_the_projects_gitignore_is_never_touched(project_dirs: ModuleType, repo: Path) -> None:
    """These entries are one developer's local state: they never enter a shared file."""
    gitignore = repo / ".gitignore"
    original = b"*.log\nbuild/\n"
    gitignore.write_bytes(original)
    project_dirs._ensure_excluded(repo)
    assert gitignore.read_bytes() == original
    assert _lines(_exclude(repo))[-4:] == ENTRIES


def test_a_second_run_changes_nothing(project_dirs: ModuleType, repo: Path) -> None:
    """Idempotent: a session start on an already-excluded repo rewrites no byte."""
    project_dirs._ensure_excluded(repo)
    once = _exclude(repo).read_bytes()
    project_dirs._ensure_excluded(repo)
    assert _exclude(repo).read_bytes() == once


def test_an_unanchored_tasks_line_counts_as_covering_the_anchored_entry(
    project_dirs: ModuleType, repo: Path
) -> None:
    """Lines compare stripped of slashes, so an existing ``tasks/`` blocks ``/tasks/``."""
    _exclude(repo).write_text("tasks/\n", encoding="utf-8")
    project_dirs._ensure_excluded(repo)
    lines = _lines(_exclude(repo))
    assert "/tasks/" not in lines
    assert lines == ["tasks/", "claude-memory/", "claude-temp/", "/docs/adr/"]


def test_an_existing_crlf_file_keeps_its_newlines(project_dirs: ModuleType, repo: Path) -> None:
    """A Windows-authored exclude file is not silently converted to LF."""
    _exclude(repo).write_bytes(b"# mine\r\n*.tmp\r\n")
    project_dirs._ensure_excluded(repo)
    raw = _exclude(repo).read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")  # no lone LF anywhere
    assert raw.endswith(b"/docs/adr/\r\n")
    assert _lines(_exclude(repo)) == ["# mine", "*.tmp", *ENTRIES]


def test_a_missing_info_directory_is_created(project_dirs: ModuleType, repo: Path) -> None:
    """A repo whose ``.git/info/`` was never created still gets its entries."""
    _exclude(repo).unlink()
    (repo / ".git/info").rmdir()
    project_dirs._ensure_excluded(repo)
    assert _lines(_exclude(repo)) == ENTRIES


def test_a_directory_that_is_not_a_repo_gets_nothing(
    project_dirs: ModuleType, tmp_path: Path
) -> None:
    """No repository, nothing to exclude — and nothing written anywhere.

    The locator is asserted to return None *first*: if this scratch directory were ever
    inside some other repository, the test fails here rather than writing into it.
    """
    assert project_dirs._exclude_file(tmp_path) is None
    project_dirs._ensure_excluded(tmp_path)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------- leftovers (ADR-0007 §4)


def test_leftovers_ignores_keep_and_this_sessions_own_directory(
    project_dirs: ModuleType, scratch: Path
) -> None:
    """The reserved dir and the caller's own session dir are not somebody's leavings."""
    (scratch / KEEP).mkdir()
    (scratch / SESSIONS / "mine").mkdir(parents=True)
    assert project_dirs._leftovers(scratch, "mine") == []


def test_leftovers_reports_other_session_ids_and_top_level_entries(
    project_dirs: ModuleType, scratch: Path
) -> None:
    """Everything else is reported: plain entries by name, session dirs path-qualified."""
    (scratch / KEEP).mkdir()
    (scratch / "junkdir").mkdir()
    (scratch / "junk.txt").write_text("junk\n", encoding="utf-8")
    (scratch / SESSIONS / "mine").mkdir(parents=True)
    (scratch / SESSIONS / "other").mkdir()
    assert project_dirs._leftovers(scratch, "mine") == [
        "junk.txt",
        "junkdir",
        "sessions/other",
    ]


def test_leftovers_without_a_session_id_reports_every_session_directory(
    project_dirs: ModuleType, scratch: Path
) -> None:
    """With no id to exempt, every session dir belongs to somebody else."""
    (scratch / SESSIONS / "mine").mkdir(parents=True)
    (scratch / SESSIONS / "other").mkdir()
    assert project_dirs._leftovers(scratch, None) == ["sessions/mine", "sessions/other"]


def test_leftovers_of_an_unreadable_scratch_root_is_a_report_not_a_failure(
    project_dirs: ModuleType, tmp_path: Path
) -> None:
    """A scratch root that cannot be listed reports nothing rather than raising."""
    assert project_dirs._leftovers(tmp_path / "not-there", "mine") == []


# ---------------------------------------------------------------- session id (ADR-0007 §6)


@pytest.mark.parametrize(
    "value",
    ["", ".", "..", "...", "a/b", "sub/dir/", "/abs", 17, None, ["s1"]],
)
def test_an_unusable_session_id_is_refused(project_dirs: ModuleType, value: object) -> None:
    """The id is joined into a path, so anything that is not one bare name is refused."""
    assert project_dirs._session_id(value) is None


@pytest.mark.parametrize("value", ["s1", "573b666a-c17a-4e92-a7c8-5318c91c84e9", "kasper-004"])
def test_a_bare_name_is_accepted_as_the_session_id(project_dirs: ModuleType, value: str) -> None:
    """A plain directory name — what the harness actually sends — comes back unchanged."""
    assert project_dirs._session_id(value) == value


# ---------------------------------------------------------------- the scratch dirs, end to end


def test_session_start_creates_keep_and_the_sessions_directory(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """§4: both dirs exist after a start, so the sweep and the reports have their places."""
    root = tmp_path / "proj"
    root.mkdir()
    result = run_script(
        repo_root, "project_dirs", {"session_id": "s1", "cwd": str(root)}, fake_home
    )
    assert result.returncode == 0, result.stderr
    assert (root / SCRATCH / KEEP).is_dir()
    assert (root / SCRATCH / SESSIONS / "s1").is_dir()


def test_session_start_prints_the_scratch_line(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """§4: SessionStart stdout is context — the session is told where its scratch is."""
    root = tmp_path / "proj"
    root.mkdir()
    result = run_script(
        repo_root, "project_dirs", {"session_id": "s1", "cwd": str(root)}, fake_home
    )
    assert line_with(result.stdout, "Session scratch:", "claude-temp/sessions/s1/") is not None


def test_a_clean_scratch_root_produces_no_leftover_line(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """The second line appears only when there is something to report."""
    root = tmp_path / "proj"
    root.mkdir()
    result = run_script(
        repo_root, "project_dirs", {"session_id": "s1", "cwd": str(root)}, fake_home
    )
    assert "Leftovers" not in result.stdout


def test_a_killed_sessions_leavings_are_reported_and_left_on_disk(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """§4: SessionStart reports and stops there — deleting at start is what ADR-0004 rejected."""
    root = tmp_path / "proj"
    temp = root / SCRATCH
    (temp / "reports").mkdir(parents=True)
    (temp / "reports" / "evidence.md").write_text("# from a killed session\n", encoding="utf-8")
    before = tree_bytes(temp)
    result = run_script(
        repo_root, "project_dirs", {"session_id": "s1", "cwd": str(root)}, fake_home
    )
    assert line_with(result.stdout, "Leftovers in claude-temp/", "reports") is not None
    assert tree_bytes(temp) == before


def test_a_resumed_session_does_not_report_its_own_directory(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """A second start under the same id — resume or compact — reports the same two lines."""
    root = tmp_path / "proj"
    root.mkdir()
    payload = {"session_id": "s1", "cwd": str(root)}
    first = run_script(repo_root, "project_dirs", payload, fake_home)
    second = run_script(repo_root, "project_dirs", payload, fake_home)
    assert second.stdout == first.stdout
    assert "Leftovers" not in second.stdout


def test_a_payload_with_an_unusable_session_id_still_gets_keep_but_no_scratch_line(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """§6: the id is not guessed at — no session dir, no scratch line, and no path escape."""
    root = tmp_path / "proj"
    root.mkdir()
    result = run_script(
        repo_root, "project_dirs", {"session_id": "../evil", "cwd": str(root)}, fake_home
    )
    assert result.returncode == 0, result.stderr
    assert "Session scratch:" not in result.stdout
    assert (root / SCRATCH / KEEP).is_dir()
    assert not (root / SCRATCH / SESSIONS).exists()
    assert not (tmp_path / "evil").exists()
