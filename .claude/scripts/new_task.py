#!/usr/bin/env python3
"""Scaffold a new task document under the project's ``tasks/`` directory.

Implements the Task Documentation rule: every task gets a living doc named
``tasks/<NNN>-<slug>.md`` created from the standard template.

With ``--branch`` it also starts the task's branch, ``feat/<NNN>-<slug>``, but only from a
clean, up-to-date mainline: it refuses (exit 1, the reason on stderr) unless the current
branch is the mainline, ``git status --porcelain`` is empty, and the mainline is not behind
its upstream after a ``git fetch``. An existing branch of that name makes ``git switch -c``
fail and the command exit 1 — nothing is overwritten, and no doc is written.

The title comes from the first argument, or from stdin when no argument is given — the
``/new-task`` command passes it through a file so no shell ever parses it.

Usage:
    python new_task.py "Build the upload service"
    python new_task.py "Build the upload service" --tasks-dir /path/to/tasks
    python new_task.py --branch --title-file title.txt
    python new_task.py <<'EOF'
    Build the "upload" service
    EOF
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

#: Seconds any one git call may take; a hung fetch is a refusal, not a hung command.
GIT_TIMEOUT_SECONDS: float = 10.0

#: The mainline when the repo has it; otherwise origin's HEAD, then the first of these found.
PREFERRED_MAINLINE: str = "development"
FALLBACK_MAINLINES: tuple[str, ...] = ("master", "main")
ORIGIN_HEAD_PREFIX: str = "origin/"

#: The branch prefix a new task starts on (git-workflow).
BRANCH_PREFIX: str = "feat/"

#: The round cap a task doc starts with (kasper-v2 §1).
DEFAULT_ROUND_CAP: int = 3


class TaskDocScaffolder:
    """Creates a numbered task document from the standard template."""

    TEMPLATE: str = (
        "# Task {number:03d}: {title}\n\n"
        "- **Status:** planning\n"
        "- **Branch:** {branch}\n"
        "- **PR:** n/a\n"
        "- **Created:** {date}\n"
        "- **Rounds:** 0/{round_cap}\n\n"
        "## Goal\n"
        '<what "done" looks like>\n\n'
        "## Plan / Subtasks\n"
        "- [ ] 1. <subtask title> — commit: pending\n"
        "      goal:       <observable outcome a verifier can judge>\n"
        "      done-check: `<runnable command>`\n"
        "      cap:        5\n"
        "      owner:      <agent>   verifier: <different agent>\n\n"
        "## Decisions & Notes\n"
        "- \n\n"
        "## Review\n"
        "<filled when done: outcome + verification>\n"
    )

    def __init__(self, tasks_dir: Path, today: datetime.date) -> None:
        self._tasks_dir = tasks_dir
        self._today = today

    @staticmethod
    def slugify(title: str) -> str:
        """Turn a title into a lowercase, hyphenated, filesystem-safe slug."""
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return slug or "task"

    def _next_number(self) -> int:
        """Return the next sequential task number based on existing docs."""
        highest = 0
        for path in self._tasks_dir.glob("[0-9][0-9][0-9]-*.md"):
            match = re.match(r"(\d{3})-", path.name)
            if match:
                highest = max(highest, int(match.group(1)))
        return highest + 1

    def stem(self, title: str) -> str:
        """The next task's ``<NNN>-<slug>`` — the doc's file stem and its branch's suffix."""
        return f"{self._next_number():03d}-{self.slugify(title)}"

    def create(self, title: str, branch: str = "n/a") -> Path:
        """Write the new task doc and return its path. Refuses to overwrite.

        Args:
            title: The task's title.
            branch: The value of the doc's ``Branch:`` line.

        Returns:
            The created doc's path.

        Raises:
            FileExistsError: When the doc already exists.
        """
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        number = self._next_number()
        path = self._tasks_dir / f"{number:03d}-{self.slugify(title)}.md"
        if path.exists():
            raise FileExistsError(f"Task doc already exists: {path}")
        content = self.TEMPLATE.format(
            number=number,
            title=title,
            branch=branch,
            date=self._today.isoformat(),
            round_cap=DEFAULT_ROUND_CAP,
        )
        path.write_text(content, encoding="utf-8")
        return path


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run one git command in the current directory, captured, never raising on its exit code.

    Raises:
        OSError: When git cannot be launched.
        subprocess.TimeoutExpired: When it runs past :data:`GIT_TIMEOUT_SECONDS`.
    """
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, timeout=GIT_TIMEOUT_SECONDS
    )


def _local_branch_exists(name: str) -> bool:
    """Whether ``refs/heads/<name>`` exists."""
    return _git("rev-parse", "--verify", "--quiet", f"refs/heads/{name}").returncode == 0


def mainline() -> str | None:
    """The repo's mainline: ``development``, else origin's HEAD, else ``master``/``main``.

    Returns:
        The branch name, or None when none of them exists.
    """
    if _local_branch_exists(PREFERRED_MAINLINE):
        return PREFERRED_MAINLINE
    origin_head = _git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    name = origin_head.stdout.strip()
    if origin_head.returncode == 0 and name.startswith(ORIGIN_HEAD_PREFIX):
        return name.removeprefix(ORIGIN_HEAD_PREFIX)
    return next((b for b in FALLBACK_MAINLINES if _local_branch_exists(b)), None)


def branch_refusal() -> str | None:
    """Why a task branch must not start here, or None when the baseline is clean and current.

    Checks, in order: the current branch is the mainline; the tree is clean (excluded paths
    never show); after a ``git fetch`` the mainline is not behind its upstream. A mainline with
    no upstream passes with a note on stderr — there is nothing to be behind.

    Returns:
        The refusal reason, or None.

    Raises:
        OSError: When git cannot be launched.
        subprocess.TimeoutExpired: When a git call hangs.
    """
    main_branch = mainline()
    if main_branch is None:
        return "no mainline found (development, origin/HEAD, master or main)"
    current = _git("branch", "--show-current").stdout.strip()
    if current != main_branch:
        return f"not on the mainline: on '{current or 'detached HEAD'}', expected '{main_branch}'"
    if _git("status", "--porcelain").stdout.strip():
        return "dirty baseline: the working tree has uncommitted changes"
    if _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}").returncode != 0:
        print(f"note: '{main_branch}' has no upstream; nothing to be behind.", file=sys.stderr)
        return None
    if _git("fetch", "--quiet").returncode != 0:
        return "mainline behind: unknown — git fetch failed, so freshness cannot be shown"
    behind = _git("rev-list", "--count", "HEAD..@{upstream}").stdout.strip()
    if behind != "0":
        count = behind or "an unknown number of"
        return f"mainline behind: '{main_branch}' is {count} commit(s) behind its upstream"
    return None


def start_branch(branch: str) -> str | None:
    """Refuse on a dirty or stale baseline, else create and switch to ``branch``.

    Args:
        branch: The new branch's name.

    Returns:
        The refusal or failure reason, or None when the branch is checked out.
    """
    try:
        reason = branch_refusal()
        if reason is not None:
            return reason
        switched = _git("switch", "-c", branch)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"git failed: {exc}"
    if switched.returncode != 0:
        return f"git switch -c {branch} failed: {switched.stderr.strip()}"
    return None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scaffold a new task document.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "title", nargs="?", help="Human-readable task title; read from stdin when omitted."
    )
    source.add_argument(
        "--title-file",
        type=Path,
        help="Read the task title from this file (how /new-task passes it).",
    )
    parser.add_argument(
        "--tasks-dir",
        type=Path,
        default=Path("tasks"),
        help="Directory to create the task doc in (default: ./tasks).",
    )
    parser.add_argument(
        "--branch",
        action="store_true",
        help="Also start feat/<NNN>-<slug> — only from a clean, up-to-date mainline.",
    )
    return parser.parse_args(argv)


def resolve_title(title: str | None, title_file: Path | None = None) -> str | None:
    """The task title: the argument, else the ``--title-file`` contents, else stdin.

    The file form exists so arbitrary text never passes through shell syntax at all.

    Args:
        title: The positional argument, or None when it was omitted.
        title_file: The ``--title-file`` path, or None when it was omitted.

    Returns:
        The stripped title, or None when it is empty or stdin is an interactive terminal.

    Raises:
        OSError: When ``title_file`` cannot be read.
    """
    if title is None and title_file is not None:
        title = title_file.read_text(encoding="utf-8")
    if title is None:
        title = "" if sys.stdin.isatty() else sys.stdin.read()
    return title.strip() or None


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        title = resolve_title(args.title, args.title_file)
    except OSError as exc:
        print(f"Cannot read the task title file: {exc}", file=sys.stderr)
        return 2
    if title is None:
        print("A task title is required (argument, --title-file, or stdin).", file=sys.stderr)
        return 2
    scaffolder = TaskDocScaffolder(args.tasks_dir, datetime.date.today())
    branch = "n/a"
    if args.branch:
        branch = f"{BRANCH_PREFIX}{scaffolder.stem(title)}"
        reason = start_branch(branch)
        if reason is not None:
            print(f"Refusing to start the task branch: {reason}", file=sys.stderr)
            return 1
        print(f"Switched to a new branch: {branch}")
    try:
        path = scaffolder.create(title, branch)
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Created task doc: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
