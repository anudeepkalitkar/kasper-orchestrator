#!/usr/bin/env python3
"""Commit and push the current subtask as a git checkpoint.

Implements the Git Checkpoint Workflow + Branch Strategy rules:
- Refuses to commit/push directly to a shared branch (``development``/``main``/
  ``master``) — real work happens on a feature branch and reaches those via gated PRs.
- Refuses on a detached HEAD (no branch to push).
- Stages tracked changes plus untracked files, but refuses — naming them — when any of
  those paths is local-only state (``tasks/``, ``claude-temp/``, ``claude-memory/``,
  ``docs/adr/``) or an env file (``.env``, ``.env.*``); then commits with the given message
  and pushes the branch (creating the upstream on first push).

The message comes from the first argument, or from stdin when no argument is given — the
``/checkpoint`` command passes it through a quoted heredoc so no shell ever parses it.

Usage:
    python git_checkpoint.py "feat(upload): validate file type and size"
    python git_checkpoint.py "wip" --no-push
    python git_checkpoint.py <<'EOF'
    fix(gate): quote-aware "$( )" scan
    EOF
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Shared branches (git-workflow): never checkpoint directly here — development is reached only
# via a PR from a feature branch; main/master guard repos without one, qa is a legacy guard.
# Compared case-insensitively.
PROTECTED_BRANCHES: frozenset[str] = frozenset({"master", "main", "development", "qa"})
# Local-only directories a checkpoint must never stage. docs/adr/ is tracked in a PRIVATE repo,
# but telling which needs a network call to gh, so it is always refused here: a private repo
# commits its ADRs by hand in a chore commit of their own (git-workflow).
FORBIDDEN_DIRS: tuple[str, ...] = ("tasks/", "claude-temp/", "claude-memory/", "docs/adr/")
ENV_FILE: str = ".env"
# Committed env templates (placeholders, no secrets) — exempt from the .env.* refusal.
ENV_TEMPLATES: frozenset[str] = frozenset({".env.example", ".env.sample", ".env.template"})


def is_forbidden(path: str) -> bool:
    """Whether a repo-relative path must never be staged by a checkpoint.

    Args:
        path: A path as git prints it (forward slashes, relative to the repo root).

    Returns:
        True for anything under :data:`FORBIDDEN_DIRS` or named ``.env`` / ``.env.*``,
        except the committed templates in :data:`ENV_TEMPLATES`.
    """
    name = path.rsplit("/", 1)[-1]
    is_env = name == ENV_FILE or (name.startswith(f"{ENV_FILE}.") and name not in ENV_TEMPLATES)
    return path.startswith(FORBIDDEN_DIRS) or is_env


class GitCheckpoint:
    """Performs a safe stage → commit → push cycle on a feature branch."""

    def __init__(self, push: bool = True) -> None:
        self._push = push

    @staticmethod
    def _run(args: list[str], capture: bool = False, strip: bool = True) -> str:
        """Run a git command, raising on failure. Returns stdout when captured.

        ``strip=False`` keeps the output verbatim — required for path listings, where
        stripping would eat a filename's leading or trailing whitespace.
        """
        result = subprocess.run(
            ["git", *args],
            check=True,
            text=True,
            capture_output=capture,
        )
        output = result.stdout or ""
        return (output.strip() if strip else output) if capture else ""

    def current_branch(self) -> str:
        # ``branch --show-current`` works even on an unborn branch (no commits
        # yet) and returns an empty string when HEAD is detached.
        return self._run(["branch", "--show-current"], capture=True)

    def has_changes(self) -> bool:
        return bool(self._run(["status", "--porcelain"], capture=True))

    def _paths(self, args: list[str]) -> list[str]:
        """Run a NUL-separated git path listing and return the paths it names."""
        listing = self._run([*args, "-z"], capture=True, strip=False)
        return [path for path in listing.split("\0") if path]

    def untracked_paths(self) -> list[str]:
        """Untracked files git would stage (ignored and excluded files left out)."""
        return self._paths(["ls-files", "--others", "--exclude-standard"])

    def pending_paths(self) -> list[str]:
        """Every path this checkpoint would commit: already staged, tracked-changed, untracked."""
        staged = self._paths(["diff", "--cached", "--name-only"])
        changed = self._paths(["diff", "--name-only"])
        return sorted({*staged, *changed, *self.untracked_paths()})

    def stage(self) -> list[str]:
        """Stage tracked changes (deletions too) and untracked files; return the staged set."""
        self._run(["add", "--update"])
        untracked = self.untracked_paths()
        if untracked:
            self._run(["add", "--", *untracked])
        return self._paths(["diff", "--cached", "--name-only"])

    def run(self, message: str) -> int:
        """Execute the checkpoint. Returns a process exit code."""
        branch = self.current_branch()
        if not branch:
            print(
                "Refusing to checkpoint on a detached HEAD. Switch to a feature branch first:\n"
                "    git switch -c feat/<task-slug>",
                file=sys.stderr,
            )
            return 1
        if branch.lower() in PROTECTED_BRANCHES:
            print(
                f"Refusing to checkpoint on protected branch '{branch}'. "
                f"Create a feature branch first:\n"
                f"    git switch -c feat/<task-slug>",
                file=sys.stderr,
            )
            return 1

        if not self.has_changes():
            print("No changes to commit — working tree is clean.")
            return 0

        forbidden = [path for path in self.pending_paths() if is_forbidden(path)]
        if forbidden:
            print(
                "Refusing to checkpoint: these paths are local-only or env files and must "
                "never be committed:\n" + "\n".join(f"    {path}" for path in forbidden),
                file=sys.stderr,
            )
            return 1

        staged = self.stage()
        print("Staged:\n" + "\n".join(f"    {path}" for path in staged))
        self._run(["commit", "-m", message])
        print(f"Committed on '{branch}': {message}")

        if self._push:
            self._run(["push", "-u", "origin", branch])
            print(f"Pushed '{branch}' to origin.")
        return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Git checkpoint for a subtask.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "message",
        nargs="?",
        help="Commit message (imperative, scoped); read from stdin when omitted.",
    )
    source.add_argument(
        "--message-file",
        type=Path,
        help="Read the commit message from this file (how /checkpoint passes it).",
    )
    parser.add_argument(
        "--no-push",
        action="store_false",
        dest="push",
        help="Commit only; do not push.",
    )
    return parser.parse_args(argv)


def resolve_message(message: str | None, message_file: Path | None = None) -> str | None:
    """The commit message: the argument, else the ``--message-file`` contents, else stdin.

    The file form exists so arbitrary text never passes through shell syntax at all.

    Args:
        message: The positional argument, or None when it was omitted.
        message_file: The ``--message-file`` path, or None when it was omitted.

    Returns:
        The stripped message, or None when it is empty or stdin is an interactive terminal.

    Raises:
        OSError: When ``message_file`` cannot be read.
    """
    if message is None and message_file is not None:
        message = message_file.read_text(encoding="utf-8")
    if message is None:
        message = "" if sys.stdin.isatty() else sys.stdin.read()
    return message.strip() or None


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        message = resolve_message(args.message, args.message_file)
    except OSError as exc:
        print(f"Cannot read the commit message file: {exc}", file=sys.stderr)
        return 2
    if message is None:
        print("A commit message is required (argument, --message-file, or stdin).", file=sys.stderr)
        return 2
    try:
        return GitCheckpoint(push=args.push).run(message)
    except subprocess.CalledProcessError as exc:
        print(f"git command failed (exit {exc.returncode}).", file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
