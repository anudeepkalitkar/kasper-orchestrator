"""Integration tier for ``.claude/scripts/project_dirs.py`` — exclude wiring and scratch.

``main()`` is never called **in this process**: it links ``~/.claude/projects/<slug>/memory``
through ``Path.home()``, which the script offers no argument to redirect, so an in-process
call would write into (and ``rmtree`` inside) the user's real global config. The pure
branching — ``_ensure_excluded``, ``_session_id`` — is exercised directly, and the few
end-to-end facts that only ``main()`` can show (``sessions/<id>/`` is created, the one
scratch line is printed, and nothing else in ``claude-temp/`` is created, reported, or
deleted) come from running the script as its own process with ``HOME`` pointed at a
temporary directory, where the real global config is out of reach by construction.

Integration, not unit: real ``git init`` repositories and real bytes on disk.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from tests.helpers.files import tree_bytes
from tests.helpers.hooks import load_script, run_script

#: What the hook adds, in order (``project_dirs.IGNORE_ENTRIES``).
ENTRIES = ["claude-memory/", "claude-temp/", "/tasks/", "/docs/adr/"]

#: The scratch root and the one container inside it the hook owns (``project_dirs``).
SCRATCH = "claude-temp"
SESSIONS = "sessions"


@pytest.fixture
def project_dirs(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load the hook with an offline, non-private visibility default."""
    module = load_script(repo_root, "project_dirs")
    monkeypatch.setattr(module, "_is_private_repo", Mock(return_value=False))
    return module


@pytest.fixture
def private_project_dirs(project_dirs: ModuleType, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Select private visibility without invoking GitHub."""
    monkeypatch.setattr(project_dirs, "_is_private_repo", Mock(return_value=True))
    return project_dirs


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    """The directory ``HOME`` points at for an end-to-end run — never the user's own."""
    return tmp_path / "home"


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
    assert project_dirs._ensure_excluded(repo) is False
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
    assert project_dirs._ensure_excluded(tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_a_private_repo_excludes_only_the_three_local_directories(
    private_project_dirs: ModuleType, repo: Path
) -> None:
    """Private ADRs remain stageable and no shared gitignore is created."""
    before = _exclude(repo).read_bytes()
    assert private_project_dirs._ensure_excluded(repo) is True
    assert _exclude(repo).read_bytes() == before + b"claude-memory/\nclaude-temp/\n/tasks/\n"
    assert not (repo / ".gitignore").exists()
    checked = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        input="docs/adr/0001.md\ntasks/010.md\nclaude-memory/note.md\nclaude-temp/scratch\n",
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout.splitlines() == [
        "tasks/010.md",
        "claude-memory/note.md",
        "claude-temp/scratch",
    ]


@pytest.mark.parametrize("adr", ["/docs/adr/", "docs/adr/", "docs/adr", "/docs/adr"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_private_migration_removes_only_the_adr_entry(
    private_project_dirs: ModuleType, repo: Path, adr: str, newline: str
) -> None:
    """Both old spellings disappear while comments, other patterns, and CRLF survive."""
    kept = ["# /docs/adr/", "*.log", *ENTRIES[:3], "docs/adr-extra/", "!docs/adr/keep.md"]
    _exclude(repo).write_bytes((newline.join([*kept, adr]) + newline).encode())
    gitignore = repo / ".gitignore"
    original = b"*.log\r\nbuild/\r\n"
    gitignore.write_bytes(original)

    assert private_project_dirs._ensure_excluded(repo) is True

    assert _exclude(repo).read_bytes() == (newline.join(kept) + newline).encode()
    assert gitignore.read_bytes() == original


@pytest.mark.parametrize("private", [True, False])
def test_a_second_run_does_not_write_the_exclude_file(
    project_dirs: ModuleType, repo: Path, monkeypatch: pytest.MonkeyPatch, private: bool
) -> None:
    """Idempotence means no write at all, not merely rewriting identical bytes."""
    monkeypatch.setattr(project_dirs, "_is_private_repo", Mock(return_value=private))
    project_dirs._ensure_excluded(repo)
    before = _exclude(repo).read_bytes()
    write = Mock(side_effect=AssertionError("unchanged exclude must not be written"))
    monkeypatch.setattr(Path, "write_bytes", write)

    assert project_dirs._ensure_excluded(repo) is private

    write.assert_not_called()
    assert _exclude(repo).read_bytes() == before


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


# ---------------------------------------------------------------- the scratch dir, end to end

#: Everything a SessionStart prints for session ``s1`` — one line, nothing else.
SCRATCH_LINE = f"Session scratch: {SCRATCH}/{SESSIONS}/s1/\n"


def test_session_start_creates_this_sessions_directory_and_nothing_else_in_scratch(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """The session gets ``sessions/<id>/``; no ``keep/`` — there is no sweep to survive."""
    root = tmp_path / "proj"
    root.mkdir()
    result = run_script(
        repo_root, "project_dirs", {"session_id": "s1", "cwd": str(root)}, fake_home
    )
    assert result.returncode == 0, result.stderr
    assert (root / SCRATCH / SESSIONS / "s1").is_dir()
    assert [entry.name for entry in (root / SCRATCH).iterdir()] == [SESSIONS]


def test_session_start_prints_exactly_the_scratch_line(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """SessionStart stdout is context: the scratch path, with no promise of a sweep."""
    root = tmp_path / "proj"
    root.mkdir()
    result = run_script(
        repo_root, "project_dirs", {"session_id": "s1", "cwd": str(root)}, fake_home
    )
    assert result.stdout == SCRATCH_LINE


def test_an_earlier_sessions_leavings_are_neither_reported_nor_touched(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """Older scratch entries are now ordinary files: no leftovers line, and nothing deleted."""
    root = tmp_path / "proj"
    temp = root / SCRATCH
    (temp / "reports").mkdir(parents=True)
    (temp / "reports" / "evidence.md").write_text("# from a killed session\n", encoding="utf-8")
    (temp / SESSIONS / "older").mkdir(parents=True)
    (temp / SESSIONS / "older" / "notes.md").write_text("# older\n", encoding="utf-8")
    before = tree_bytes(temp)
    result = run_script(
        repo_root, "project_dirs", {"session_id": "s1", "cwd": str(root)}, fake_home
    )
    assert result.stdout == SCRATCH_LINE
    assert not (temp / "keep").exists()
    assert tree_bytes(temp) == before


def test_a_resumed_session_prints_the_same_single_line(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """A second start under the same id — resume or compact — reports exactly what the first did."""
    root = tmp_path / "proj"
    root.mkdir()
    payload = {"session_id": "s1", "cwd": str(root)}
    first = run_script(repo_root, "project_dirs", payload, fake_home)
    second = run_script(repo_root, "project_dirs", payload, fake_home)
    assert first.stdout == SCRATCH_LINE
    assert second.stdout == SCRATCH_LINE


def test_a_payload_with_an_unusable_session_id_gets_no_scratch_dir_and_prints_nothing(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """§6: the id is not guessed at — no session dir, no scratch line, and no path escape."""
    root = tmp_path / "proj"
    root.mkdir()
    result = run_script(
        repo_root, "project_dirs", {"session_id": "../evil", "cwd": str(root)}, fake_home
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert list((root / SCRATCH).iterdir()) == []
    assert not (tmp_path / "evil").exists()
