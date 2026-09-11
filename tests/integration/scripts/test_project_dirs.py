"""Integration tier for ``.claude/scripts/project_dirs.py`` — the local-exclude wiring only.

``main()`` is deliberately *not* exercised: it links ``~/.claude/projects/<slug>/memory``
through ``Path.home()``, which the script offers no argument or environment switch to
redirect, so running it would write into (and ``rmtree`` inside) the user's real global
config. ``_ensure_excluded`` is the branching worth pinning, and it touches nothing but the
scratch repository's own ``info/exclude``.

Integration, not unit: real ``git init`` repositories and real bytes on disk.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

#: What the hook adds, in order (``project_dirs.IGNORE_ENTRIES``).
ENTRIES = ["claude-memory/", "claude-temp/", "/tasks/", "/docs/adr/"]


def _load_project_dirs(repo_root: Path) -> ModuleType:
    """Import ``project_dirs.py`` by path — ``.claude/`` is not an importable package."""
    path = repo_root / ".claude/scripts/project_dirs.py"
    spec = importlib.util.spec_from_file_location("project_dirs_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project_dirs(repo_root: Path) -> ModuleType:
    """The hook script under test, loaded from its path in this checkout."""
    return _load_project_dirs(repo_root)


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
