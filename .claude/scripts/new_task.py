#!/usr/bin/env python3
"""Scaffold a new task document under the project's ``tasks/`` directory.

Implements the Task Documentation rule: every task gets a living doc named
``tasks/<NNN>-<slug>.md`` created from the standard template.

The title comes from the first argument, or from stdin when no argument is given — the
``/new-task`` command passes it through a quoted heredoc so no shell ever parses it.

Usage:
    python new_task.py "Build the upload service"
    python new_task.py "Build the upload service" --tasks-dir /path/to/tasks
    python new_task.py <<'EOF'
    Build the "upload" service
    EOF
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path


class TaskDocScaffolder:
    """Creates a numbered task document from the standard template."""

    TEMPLATE: str = (
        "# Task {number:03d}: {title}\n\n"
        "- **Status:** planning\n"
        "- **Branch:** n/a\n"
        "- **PR:** n/a\n"
        "- **Created:** {date}\n\n"
        "## Goal\n"
        "<what \"done\" looks like>\n\n"
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

    def create(self, title: str) -> Path:
        """Write the new task doc and return its path. Refuses to overwrite."""
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        number = self._next_number()
        path = self._tasks_dir / f"{number:03d}-{self.slugify(title)}.md"
        if path.exists():
            raise FileExistsError(f"Task doc already exists: {path}")
        content = self.TEMPLATE.format(
            number=number, title=title, date=self._today.isoformat()
        )
        path.write_text(content, encoding="utf-8")
        return path


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
    try:
        path = scaffolder.create(title)
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Created task doc: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
