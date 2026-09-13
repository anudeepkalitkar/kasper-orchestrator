"""Integration tier for ``.claude/scripts/session_cleanup.py`` — the SessionEnd sweep.

ADR-0007 §§2, 5 and 6 are the requirements: everything directly under ``claude-temp/`` goes
except ``keep/``; a clean, unlocked worktree in one of two known locations goes with it and
its branch does not; a dirty, locked or foreign worktree stays; and a payload the hook
cannot trust, a root that is ``~/.claude``, or a root with no scratch dir is a no-op.

Real ``git init`` repositories and real worktrees, because every one of those decisions is
read out of ``git worktree list --porcelain`` and ``git status --porcelain`` — a fake would
only pin the test's idea of git. ``main()`` runs as its own process with ``HOME`` pointed at
a temporary directory (:func:`tests.helpers.hooks.run_script`), which is what makes the
``~/.claude`` guard testable without going anywhere near the real one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers.files import tree_bytes
from tests.helpers.gitrepo import add_worktree, branch_names, git, init_repo, worktree_paths
from tests.helpers.hooks import run_script
from tests.helpers.symlinks import symlink_or_skip

#: The scratch root's name, and the one entry inside it the sweep never touches.
SCRATCH = "claude-temp"
KEEP = "keep"


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    """The directory ``HOME`` points at for every run — never the user's own."""
    return tmp_path / "home"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A git project root whose scratch dir holds a populated ``keep/`` and plain junk."""
    root = init_repo(tmp_path / "proj")
    temp = root / SCRATCH
    (temp / KEEP / "notes").mkdir(parents=True)
    (temp / KEEP / "precious.txt").write_bytes(b"promoted before the session ended\n")
    (temp / KEEP / "notes" / "deep.md").write_bytes(b"# kept\n")
    (temp / "junkdir" / "nested").mkdir(parents=True)
    (temp / "junkdir" / "nested" / "waste.log").write_bytes(b"waste\n")
    (temp / "junk.txt").write_bytes(b"junk\n")
    return root


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A directory outside the project, holding the file a symlink test must not lose."""
    target = tmp_path / "outside"
    target.mkdir()
    (target / "target.txt").write_bytes(b"not the session's to delete\n")
    return target


def _scratch(project: Path) -> Path:
    """The project's scratch root."""
    return project / SCRATCH


def _names(temp: Path) -> list[str]:
    """What is left directly under the scratch root, sorted."""
    return sorted(entry.name for entry in temp.iterdir())


def _sweep(repo_root: Path, project: Path, fake_home: Path) -> subprocess.CompletedProcess[str]:
    """Fire one SessionEnd event at ``project`` and assert the hook never fails the exit.

    Args:
        repo_root: The checkout the hook script is read from.
        project: The project root the payload names as ``cwd``.
        fake_home: The stand-in for the user's home.

    Returns:
        The finished process, for tests that read its stderr.
    """
    payload = {"session_id": "s-end", "cwd": str(project), "reason": "clear"}
    result = run_script(repo_root, "session_cleanup", payload, fake_home)
    assert result.returncode == 0, result.stderr
    return result


# ---------------------------------------------------------------- the sweep (§2)


def test_everything_directly_under_the_scratch_root_goes_except_keep(
    repo_root: Path, project: Path, fake_home: Path
) -> None:
    """§2: not "this session's entries" — all of them, with ``keep/`` the one exception."""
    _sweep(repo_root, project, fake_home)
    assert _names(_scratch(project)) == [KEEP]


def test_the_keep_directory_survives_byte_identical(
    repo_root: Path, project: Path, fake_home: Path
) -> None:
    """§3: the keep-list is never touched — not emptied, not rewritten, not re-created."""
    before = tree_bytes(_scratch(project) / KEEP)
    _sweep(repo_root, project, fake_home)
    assert tree_bytes(_scratch(project) / KEEP) == before
    assert before  # the fixture really did put something there to compare


def test_a_symlink_in_the_scratch_root_is_unlinked_and_its_target_is_untouched(
    repo_root: Path, project: Path, fake_home: Path, outside: Path
) -> None:
    """§6: a symlink is unlinked, never followed into what it points at."""
    temp = _scratch(project)
    symlink_or_skip(temp / "file-link", outside / "target.txt")
    symlink_or_skip(temp / "dir-link", outside, target_is_directory=True)
    before = tree_bytes(outside)
    _sweep(repo_root, project, fake_home)
    assert not os.path.lexists(temp / "file-link")
    assert not os.path.lexists(temp / "dir-link")
    assert tree_bytes(outside) == before


def test_a_symlink_nested_in_a_swept_directory_is_not_followed(
    repo_root: Path, project: Path, fake_home: Path, outside: Path
) -> None:
    """The recursive delete unlinks a symlinked subdirectory rather than descending it."""
    symlink_or_skip(
        _scratch(project) / "junkdir" / "nested" / "escape", outside, target_is_directory=True
    )
    before = tree_bytes(outside)
    _sweep(repo_root, project, fake_home)
    assert not (_scratch(project) / "junkdir").exists()
    assert tree_bytes(outside) == before


def test_a_root_that_is_not_a_repository_still_gets_its_scratch_swept(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """The worktree step degrades to nothing when git has no repository to answer about."""
    root = tmp_path / "plain"
    temp = root / SCRATCH
    (temp / KEEP).mkdir(parents=True)
    (temp / "junk.txt").write_bytes(b"junk\n")
    result = run_script(
        repo_root, "session_cleanup", {"session_id": "s1", "cwd": str(root)}, fake_home
    )
    assert result.returncode == 0, result.stderr
    assert _names(temp) == [KEEP]


# ---------------------------------------------------------------- worktrees (§5)


@pytest.fixture
def clean_worktrees(project: Path) -> dict[str, Path]:
    """One clean worktree in each removable location: the harness's, and the scratch root."""
    return {
        "harness": add_worktree(
            project, project / ".claude" / "worktrees" / "agent", "worktree-agent"
        ),
        "scratch": add_worktree(project, _scratch(project) / "agent-scratch", "worktree-scratch"),
    }


def test_clean_unlocked_worktrees_in_both_known_locations_are_removed_and_pruned(
    repo_root: Path, project: Path, fake_home: Path, clean_worktrees: dict[str, Path]
) -> None:
    """§5: both locations qualify, and the prune leaves no metadata behind in the same run."""
    _sweep(repo_root, project, fake_home)
    assert not clean_worktrees["harness"].exists()
    assert not clean_worktrees["scratch"].exists()
    assert worktree_paths(project) == {project.resolve()}


def test_removing_a_worktree_never_deletes_its_branch(
    repo_root: Path, project: Path, fake_home: Path, clean_worktrees: dict[str, Path]
) -> None:
    """§5: "branches are never deleted" — the checkout goes, the work stays reachable."""
    _sweep(repo_root, project, fake_home)
    assert {"worktree-agent", "worktree-scratch"} <= branch_names(project)


def test_a_dirty_worktree_is_left_in_place(repo_root: Path, project: Path, fake_home: Path) -> None:
    """§5: uncommitted work disqualifies a worktree, wherever it sits."""
    dirty = add_worktree(project, project / ".claude" / "worktrees" / "dirty", "worktree-dirty")
    (dirty / "uncommitted.txt").write_text("dirt\n", encoding="utf-8")
    _sweep(repo_root, project, fake_home)
    assert (dirty / "uncommitted.txt").read_text(encoding="utf-8") == "dirt\n"
    assert dirty.resolve() in worktree_paths(project)


def test_a_locked_worktree_in_the_scratch_root_is_left_in_place(
    repo_root: Path, project: Path, fake_home: Path
) -> None:
    """§5: the harness locks a subagent's worktree while it runs — a lock is a veto."""
    locked = add_worktree(project, _scratch(project) / "locked-wt", "worktree-locked")
    git(project, "worktree", "lock", str(locked))
    _sweep(repo_root, project, fake_home)
    assert (locked / "README.md").is_file()
    assert locked.resolve() in worktree_paths(project)


def test_a_worktree_locked_with_a_reason_is_left_in_place(
    repo_root: Path, project: Path, fake_home: Path
) -> None:
    """The porcelain writes the reason on the ``locked`` line; it is still a lock."""
    locked = add_worktree(project, project / ".claude" / "worktrees" / "busy", "worktree-busy")
    git(project, "worktree", "lock", "--reason", "agent still running", str(locked))
    _sweep(repo_root, project, fake_home)
    assert (locked / "README.md").is_file()
    assert locked.resolve() in worktree_paths(project)


def test_a_dirty_worktree_nested_in_the_scratch_survives_while_the_rest_is_swept(
    repo_root: Path, project: Path, fake_home: Path
) -> None:
    """§5 beats §2 where they meet: the sweep may not delete what the worktree rule spared.

    The shelter is deliberately coarse — the whole top-level entry (``sessions/``) is
    stepped over so the checkout two levels down can survive — while every other entry in
    the scratch root goes as usual.
    """
    temp = _scratch(project)
    nested = add_worktree(project, temp / "sessions" / "x" / "wt", "worktree-nested")
    (nested / "uncommitted.txt").write_text("dirt\n", encoding="utf-8")
    _sweep(repo_root, project, fake_home)
    assert (nested / "uncommitted.txt").read_text(encoding="utf-8") == "dirt\n"
    assert nested.resolve() in worktree_paths(project)
    assert _names(temp) == [KEEP, "sessions"]


def test_a_clean_worktree_outside_both_locations_is_left_alone(
    repo_root: Path, project: Path, fake_home: Path
) -> None:
    """§5: location is a condition, not a detail — a worktree elsewhere is not the hook's."""
    elsewhere = add_worktree(project, project / "elsewhere-wt", "worktree-elsewhere")
    _sweep(repo_root, project, fake_home)
    assert (elsewhere / "README.md").is_file()
    assert elsewhere.resolve() in worktree_paths(project)


def test_the_metadata_of_a_worktree_whose_directory_is_gone_is_pruned(
    repo_root: Path, project: Path, fake_home: Path
) -> None:
    """§5: ``git worktree prune`` closes out whatever left its directory behind."""
    gone = add_worktree(project, _scratch(project) / "gone-wt", "worktree-gone")
    shutil.rmtree(gone)
    assert gone.resolve() in worktree_paths(project)  # git still lists it before the run
    _sweep(repo_root, project, fake_home)
    assert worktree_paths(project) == {project.resolve()}


# ---------------------------------------------------------------- the guards (§6)


@pytest.mark.parametrize(
    ("case", "payload"),
    [
        ("malformed", "{not json at all"),
        ("empty", ""),
        ("no cwd", {"session_id": "s1", "reason": "clear"}),
        ("cwd is not a string", {"cwd": 17}),
        ("cwd is empty", {"cwd": ""}),
    ],
)
def test_a_payload_the_hook_cannot_trust_is_a_no_op(
    repo_root: Path, project: Path, fake_home: Path, case: str, payload: object
) -> None:
    """§6: a missing or malformed payload makes the hook a no-op rather than a guess."""
    before = tree_bytes(_scratch(project))
    result = run_script(repo_root, "session_cleanup", payload, fake_home)
    assert result.returncode == 0, result.stderr
    assert tree_bytes(_scratch(project)) == before, case


def test_a_root_without_a_scratch_dir_is_a_no_op(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """Nothing to sweep, and nothing created either — the hook does not make the dir."""
    root = tmp_path / "no-scratch"
    root.mkdir()
    (root / "work.txt").write_bytes(b"the project itself\n")
    result = run_script(repo_root, "session_cleanup", {"cwd": str(root)}, fake_home)
    assert result.returncode == 0, result.stderr
    assert sorted(entry.name for entry in root.iterdir()) == ["work.txt"]


def test_the_global_config_root_is_never_swept(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """§6: a root resolving to ``~/.claude`` is skipped whole, as ``project_dirs.py`` skips it."""
    config = fake_home / ".claude"
    temp = config / SCRATCH
    temp.mkdir(parents=True)
    (temp / "junk.txt").write_bytes(b"not this one\n")
    before = tree_bytes(config)
    result = run_script(repo_root, "session_cleanup", {"cwd": str(config)}, fake_home)
    assert result.returncode == 0, result.stderr
    assert tree_bytes(config) == before


@pytest.mark.skipif(sys.platform == "win32", reason="a read-only bit is not a refusal on win32")
def test_a_stuck_entry_is_reported_once_on_stderr_and_the_sweep_continues(
    repo_root: Path, project: Path, fake_home: Path
) -> None:
    """One entry that will not go is noted and stepped over; the rest of the sweep runs."""
    if os.geteuid() == 0:
        pytest.skip("root may delete through a read-only directory")
    temp = _scratch(project)
    stuck = temp / "aaa-stuck"  # sorts first, so the sweep meets it before the junk
    stuck.mkdir()
    (stuck / "inner.txt").write_text("wedged\n", encoding="utf-8")
    stuck.chmod(0o555)
    try:
        result = _sweep(repo_root, project, fake_home)
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        assert len(lines) == 1
        assert "could not remove" in lines[0] and stuck.name in lines[0]
        assert (stuck / "inner.txt").is_file()
        assert _names(temp) == sorted([KEEP, stuck.name])  # every other entry went
    finally:
        stuck.chmod(0o755)


# ---------------------------------------------------------------- symlinked locations (§6)


def test_a_symlinked_scratch_root_is_left_alone_whole(
    repo_root: Path, tmp_path: Path, fake_home: Path
) -> None:
    """§6: ``claude-temp`` pointing out of the project is a no-op, not a sweep of its target.

    The hook touches nothing outside ``<root>/claude-temp/``; following the link would put
    the sweep in a directory the payload never named.
    """
    root = init_repo(tmp_path / "linked-proj")
    store = tmp_path / "outside-scratch"
    (store / KEEP).mkdir(parents=True)
    (store / "junk.txt").write_bytes(b"outside the root\n")
    (store / "junkdir").mkdir()
    symlink_or_skip(root / SCRATCH, store, target_is_directory=True)
    before = tree_bytes(store)
    result = run_script(repo_root, "session_cleanup", {"cwd": str(root)}, fake_home)
    assert result.returncode == 0, result.stderr
    assert (root / SCRATCH).is_symlink()
    assert tree_bytes(store) == before
    assert sorted(entry.name for entry in store.iterdir()) == sorted([KEEP, "junk.txt", "junkdir"])


def test_worktrees_under_a_symlinked_worktree_store_are_spared(
    repo_root: Path, project: Path, fake_home: Path
) -> None:
    """§6: a resolved worktree path is matched against the *unresolved* known locations.

    A ``.claude/worktrees`` symlinked out of the project therefore matches nothing, and the
    checkouts behind it are spared — while the scratch root is still swept in the same run,
    so this is a containment rule and not a hook that quietly did nothing.
    """
    store = project.parent / "worktree-store"
    store.mkdir()
    (project / ".claude").mkdir(exist_ok=True)
    symlink_or_skip(project / ".claude" / "worktrees", store, target_is_directory=True)
    outside = add_worktree(project, store / "agent", "worktree-agent")
    _sweep(repo_root, project, fake_home)
    assert (outside / "README.md").is_file()
    assert outside.resolve() in worktree_paths(project)
    assert _names(_scratch(project)) == [KEEP]
