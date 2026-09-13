"""Unit tier for ``parse_worktrees`` in ``.claude/scripts/session_cleanup.py``.

The function is pure — ``git worktree list --porcelain`` in, ``(path, locked)`` pairs out —
so the tier's no-I/O rule holds: the only disk touch is loading the script, which is what an
``import`` would be if ``.claude/`` were an importable package (the sibling unit file
imports ``install`` the ordinary way for the same reason). What the parse must survive is
git's real output, so :data:`PORCELAIN` is captured verbatim from a repository with a
detached checkout, a path containing spaces, a bare lock and a lock carrying a reason —
the shapes the sweep's locked-versus-not decision rests on.

The file is …_parse.py rather than …_cleanup.py because pytest imports test
modules by basename, and the integration tier already owns that name for the same
source file; the directory still mirrors .claude/scripts/.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from tests.helpers.hooks import load_script

#: Real ``git worktree list --porcelain`` output (git 2.54), paths shortened to ``/repo``.
PORCELAIN = (
    "worktree /repo\n"
    "HEAD dfee62a23c786cad8a6ba1de907c8ba9966a715e\n"
    "branch refs/heads/main\n"
    "\n"
    "worktree /repo/claude-temp/detached wt\n"
    "HEAD dfee62a23c786cad8a6ba1de907c8ba9966a715e\n"
    "detached\n"
    "locked agent still running\n"
    "\n"
    "worktree /repo/.claude/worktrees/my agent\n"
    "HEAD dfee62a23c786cad8a6ba1de907c8ba9966a715e\n"
    "branch refs/heads/worktree-agent\n"
    "locked\n"
    "\n"
)


@pytest.fixture
def session_cleanup(repo_root: Path) -> ModuleType:
    """The hook script under test, loaded from its path in this checkout."""
    return load_script(repo_root, "session_cleanup")


def test_the_main_worktree_is_first_and_every_block_is_one_entry(
    session_cleanup: ModuleType,
) -> None:
    """Order is git's, and callers drop ``[0]`` — so ``[0]`` must be the main worktree."""
    assert [path for path, _ in session_cleanup.parse_worktrees(PORCELAIN)] == [
        Path("/repo"),
        Path("/repo/claude-temp/detached wt"),
        Path("/repo/.claude/worktrees/my agent"),
    ]


def test_a_path_containing_spaces_survives_whole(session_cleanup: ModuleType) -> None:
    """The path is everything after ``worktree ``; a split on whitespace would lose it."""
    paths = [path for path, _ in session_cleanup.parse_worktrees(PORCELAIN)]
    assert paths[2].name == "my agent"


def test_a_bare_locked_line_marks_the_worktree_locked(session_cleanup: ModuleType) -> None:
    """``git worktree lock`` with no reason writes the word alone."""
    assert session_cleanup.parse_worktrees(PORCELAIN)[2][1] is True


def test_a_locked_line_with_a_reason_marks_it_locked_too(session_cleanup: ModuleType) -> None:
    """``--reason`` puts text after the word — still a lock, still a veto (ADR-0007 §5)."""
    assert session_cleanup.parse_worktrees(PORCELAIN)[1][1] is True


def test_an_unlocked_worktree_is_not_marked(session_cleanup: ModuleType) -> None:
    """No ``locked`` line means unlocked — the only state the sweep may remove."""
    assert session_cleanup.parse_worktrees(PORCELAIN)[0][1] is False


def test_a_detached_checkout_is_still_a_worktree(session_cleanup: ModuleType) -> None:
    """``detached`` replaces ``branch``; it says nothing about removability."""
    detached = "worktree /repo/claude-temp/wt\nHEAD abc123\ndetached\n\n"
    assert session_cleanup.parse_worktrees(detached) == [(Path("/repo/claude-temp/wt"), False)]


def test_a_bare_repository_block_is_read_like_any_other(session_cleanup: ModuleType) -> None:
    """A bare main repository has no ``HEAD`` line; the block is still one worktree."""
    bare = "worktree /repo.git\nbare\n\nworktree /repo/wt\nHEAD abc123\nbranch refs/heads/x\n\n"
    assert session_cleanup.parse_worktrees(bare) == [
        (Path("/repo.git"), False),
        (Path("/repo/wt"), False),
    ]


def test_output_with_no_worktrees_parses_to_nothing(session_cleanup: ModuleType) -> None:
    """Empty stdout, and the trailing blank block every listing ends with, yield no entry."""
    assert session_cleanup.parse_worktrees("") == []
