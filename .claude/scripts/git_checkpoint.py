#!/usr/bin/env python3
"""Commit and push the current subtask as a git checkpoint.

Implements the Git Checkpoint Workflow + Branch Strategy rules:
- Refuses to commit/push directly to a shared branch (``development``/``main``/
  ``master``) — real work happens on a feature branch and reaches those via gated PRs.
- Stages all changes, commits with the given message, and pushes the branch
  (creating the upstream on first push).

Usage:
    python git_checkpoint.py "feat(upload): validate file type and size"
    python git_checkpoint.py "wip" --no-push
"""

from __future__ import annotations

import argparse
import subprocess
import sys


# Shared branches in the promotion pipeline (git-workflow): never checkpoint directly here —
# they're reached only via PRs (feature → development); qa kept as a legacy guard.
PROTECTED_BRANCHES: frozenset[str] = frozenset({"master", "main", "development", "qa"})


class GitCheckpoint:
    """Performs a safe stage → commit → push cycle on a feature branch."""

    def __init__(self, push: bool = True) -> None:
        self._push = push

    @staticmethod
    def _run(args: list[str], capture: bool = False) -> str:
        """Run a git command, raising on failure. Returns stdout when captured."""
        result = subprocess.run(
            ["git", *args],
            check=True,
            text=True,
            capture_output=capture,
        )
        return (result.stdout or "").strip() if capture else ""

    def current_branch(self) -> str:
        # ``branch --show-current`` works even on an unborn branch (no commits
        # yet) and returns an empty string when HEAD is detached.
        return self._run(["branch", "--show-current"], capture=True)

    def has_changes(self) -> bool:
        return bool(self._run(["status", "--porcelain"], capture=True))

    def run(self, message: str) -> int:
        """Execute the checkpoint. Returns a process exit code."""
        branch = self.current_branch()
        if branch in PROTECTED_BRANCHES:
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

        self._run(["add", "-A"])
        self._run(["commit", "-m", message])
        print(f"Committed on '{branch}': {message}")

        if self._push:
            self._run(["push", "-u", "origin", branch])
            print(f"Pushed '{branch}' to origin.")
        return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Git checkpoint for a subtask.")
    parser.add_argument("message", help="Commit message (imperative, scoped).")
    parser.add_argument(
        "--no-push",
        action="store_false",
        dest="push",
        help="Commit only; do not push.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        return GitCheckpoint(push=args.push).run(args.message)
    except subprocess.CalledProcessError as exc:
        print(f"git command failed (exit {exc.returncode}).", file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
