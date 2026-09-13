"""Real git repositories and worktrees for the integration tier.

The SessionEnd sweep decides what to remove from ``git worktree list --porcelain`` and
``git status --porcelain``, so the tests give it real repositories rather than a fake:
these helpers build one, hang worktrees off it, and read back what git says about them.
Every repository is local-config only — identity and signing are set per repository so a
developer's global git config cannot change what a test sees.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    """Run one git command in ``repo`` and return its stdout.

    Args:
        repo: The directory to run in.
        args: The git arguments, without the leading ``git``.

    Returns:
        The command's stdout.

    Raises:
        CalledProcessError: If git exits non-zero — a broken fixture, never a finding.
    """
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return done.stdout


def init_repo(path: Path) -> Path:
    """Create a git repository at ``path`` with one commit on ``main``.

    Args:
        path: The directory to initialise; created if absent.

    Returns:
        ``path``.
    """
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "tester@example.invalid")
    git(path, "config", "user.name", "Tester")
    git(path, "config", "commit.gpgsign", "false")
    (path / "README.md").write_text("# scratch repo\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-q", "-m", "init")
    return path


def add_worktree(repo: Path, path: Path, branch: str) -> Path:
    """Add a worktree of ``repo`` at ``path``, on a new branch.

    Args:
        repo: The repository the worktree belongs to.
        path: Where the checkout goes; its parent is created if absent.
        branch: The branch name to create — what must survive a removal.

    Returns:
        ``path``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "-b", branch, str(path))
    return path


def worktree_paths(repo: Path) -> set[Path]:
    """The resolved paths git currently lists as worktrees of ``repo``.

    Args:
        repo: The repository to ask.

    Returns:
        Every listed worktree path, the main one included, resolved so a temporary
        directory behind a symlink (``/tmp`` on macOS) compares equal.
    """
    listing = git(repo, "worktree", "list", "--porcelain")
    return {
        Path(line.removeprefix("worktree ")).resolve()
        for line in listing.splitlines()
        if line.startswith("worktree ")
    }


def branch_names(repo: Path) -> set[str]:
    """Every branch in ``repo`` — the sweep must never delete one.

    Args:
        repo: The repository to ask.

    Returns:
        The short branch names.
    """
    return set(git(repo, "branch", "--format=%(refname:short)").split())
