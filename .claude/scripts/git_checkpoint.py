#!/usr/bin/env python3
"""Commit and push the current subtask as a git checkpoint.

Implements the Git Checkpoint Workflow + Branch Strategy rules:
- Refuses to commit/push directly to a shared branch (``development``/``main``/
  ``master``) — real work happens on a feature branch and reaches those via gated PRs.
- Refuses on a detached HEAD (no branch to push).
- Stages tracked changes plus untracked files, but refuses — naming them — when any of
  those paths is local-only state (``tasks/``, ``claude-temp/``, ``claude-memory/``, and
  ``docs/adr/`` exactly when the repo keeps its ADRs local) or an env file (``.env``,
  ``.env.*``); then commits with the given message and pushes the branch (creating the
  upstream on first push).
- With ``--only <path>...`` it commits exactly those paths and nothing else, by refusal: a
  path already staged that is not in the list, a forbidden path in the list, or a staged set
  that ends up empty or wider than the list stops the checkpoint before anything is committed.

The message comes from the first argument, or from stdin when no argument is given — the
``/checkpoint`` command passes it through a file so no shell ever parses it.

Usage:
    python git_checkpoint.py "feat(upload): validate file type and size"
    python git_checkpoint.py "wip" --no-push
    python git_checkpoint.py --message-file msg.txt --only src/a.py src/b.py
    python git_checkpoint.py <<'EOF'
    fix(gate): quote-aware "$( )" scan
    EOF
"""

from __future__ import annotations

import argparse
import posixpath
import subprocess
import sys
from pathlib import Path

# One rule for "is docs/adr/ local-only here", shared with the SessionStart hook so the two can
# never disagree (kasper-v2 class 5). Same import precedent as permission_recorder.py.
sys.path.insert(0, str(Path(__file__).parent))
from project_dirs import adr_local_only  # noqa: E402

# Shared branches (git-workflow): never checkpoint directly here — development is reached only
# via a PR from a feature branch; main/master guard repos without one, qa is a legacy guard.
# Compared case-insensitively.
PROTECTED_BRANCHES: frozenset[str] = frozenset({"master", "main", "development", "qa"})
# Local-only directories a checkpoint must never stage.
FORBIDDEN_DIRS: tuple[str, ...] = ("tasks/", "claude-temp/", "claude-memory/")
# The decision record: refused exactly when the repo's exclude file lists /docs/adr/.
ADR_DIR: str = "docs/adr/"
ENV_FILE: str = ".env"
# Committed env templates (placeholders, no secrets) — exempt from the .env.* refusal.
ENV_TEMPLATES: frozenset[str] = frozenset({".env.example", ".env.sample", ".env.template"})


def is_forbidden(path: str, adr_local: bool = True) -> bool:
    """Whether a repo-relative path must never be staged by a checkpoint.

    Args:
        path: A path as git prints it (forward slashes, relative to the repo root).
        adr_local: Whether this repo keeps ``docs/adr/`` local-only. Defaults to True — when
            the answer is unknown, the decision record is refused rather than risked.

    Returns:
        True for anything under :data:`FORBIDDEN_DIRS`, under :data:`ADR_DIR` when
        ``adr_local``, or named ``.env`` / ``.env.*`` except the templates in
        :data:`ENV_TEMPLATES`.
    """
    name = path.rsplit("/", 1)[-1]
    is_env = name == ENV_FILE or (name.startswith(f"{ENV_FILE}.") and name not in ENV_TEMPLATES)
    is_adr = adr_local and path.startswith(ADR_DIR)
    return path.startswith(FORBIDDEN_DIRS) or is_adr or is_env


def _listing(paths: list[str]) -> str:
    """Indent one path per line for a refusal or report message."""
    return "\n".join(f"    {path}" for path in paths)


class GitCheckpoint:
    """Performs a safe stage → commit → push cycle on a feature branch."""

    def __init__(self, push: bool = True, only: list[str] | None = None) -> None:
        self._push = push
        # Repo-relative, normalised like git prints them ("./a" and "a" are one path).
        self._only = None if only is None else [posixpath.normpath(p) for p in only]

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
        return bool(self._run(["status", "--porcelain", "--untracked-files=all"], capture=True))

    def adr_local(self) -> bool:
        """Whether ``docs/adr/`` is local-only here; an unanswerable question refuses it."""
        root = Path(self._run(["rev-parse", "--show-toplevel"], capture=True))
        return adr_local_only(root) is not False

    def _paths(self, args: list[str]) -> list[str]:
        """Run a NUL-separated git path listing and return the paths it names."""
        listing = self._run([*args, "-z"], capture=True, strip=False)
        return [path for path in listing.split("\0") if path]

    def staged_paths(self) -> list[str]:
        """The paths currently in the index that differ from HEAD.

        ``--no-renames`` lists both sides of a rename or copy: rename detection would hide the
        source path, letting a scoped checkpoint commit an undeclared deletion.
        """
        return self._paths(["diff", "--cached", "--name-only", "--no-renames"])

    def untracked_paths(self) -> list[str]:
        """Untracked files git would stage (ignored and excluded files left out)."""
        return self._paths(["ls-files", "--others", "--exclude-standard"])

    def pending_paths(self) -> list[str]:
        """Every path this checkpoint would commit: already staged, tracked-changed, untracked."""
        changed = self._paths(["diff", "--name-only", "--no-renames"])
        return sorted({*self.staged_paths(), *changed, *self.untracked_paths()})

    def stage(self) -> list[str]:
        """Stage tracked changes (deletions too) and untracked files; return the staged set."""
        self._run(["add", "--update"])
        untracked = self.untracked_paths()
        if untracked:
            self._run(["add", "--", *untracked])
        return self.staged_paths()

    def stage_only(self, only: list[str], adr_local: bool) -> list[str] | None:
        """Stage exactly ``only``, refusing anything that would widen the commit.

        Args:
            only: The repo-relative paths this checkpoint may commit.
            adr_local: Whether ``docs/adr/`` is local-only in this repo.

        Returns:
            The staged set, or None after printing the refusal.
        """
        forbidden = [path for path in only if is_forbidden(path, adr_local)]
        if forbidden:
            print(
                "Refusing to checkpoint: these listed paths are local-only or env files and "
                "must never be committed:\n" + _listing(forbidden),
                file=sys.stderr,
            )
            return None
        allowed = set(only)
        strays = [path for path in self.staged_paths() if path not in allowed]
        if strays:
            print(
                "Refusing to checkpoint: the index already holds paths outside --only; "
                "unstage them first (git restore --staged <path>):\n" + _listing(strays),
                file=sys.stderr,
            )
            return None
        self._run(["add", "--", *only])
        staged = self.staged_paths()
        if not staged or not set(staged) <= allowed:
            print(
                "Refusing to checkpoint: the --only paths staged nothing, or staged something "
                "outside the list:\n" + _listing(staged or ["(nothing staged)"]),
                file=sys.stderr,
            )
            return None
        return staged

    def stage_all(self, adr_local: bool) -> list[str] | None:
        """Stage every pending change unless a forbidden path is among them.

        Args:
            adr_local: Whether ``docs/adr/`` is local-only in this repo.

        Returns:
            The staged set, or None after printing the refusal.
        """
        forbidden = [path for path in self.pending_paths() if is_forbidden(path, adr_local)]
        if forbidden:
            print(
                "Refusing to checkpoint: these paths are local-only or env files and must "
                "never be committed:\n" + _listing(forbidden),
                file=sys.stderr,
            )
            return None
        return self.stage()

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

        if self._only is None and not self.has_changes():
            print("No changes to commit — working tree is clean.")
            return 0

        adr_local = self.adr_local()
        if self._only is None:
            staged = self.stage_all(adr_local)
        else:
            staged = self.stage_only(self._only, adr_local)
        if staged is None:
            return 1
        print("Staged:\n" + _listing(staged))
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
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="PATH",
        help="Commit exactly these repo-relative paths and nothing else (refuses otherwise).",
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
        return GitCheckpoint(push=args.push, only=args.only).run(message)
    except subprocess.CalledProcessError as exc:
        print(f"git command failed (exit {exc.returncode}).", file=sys.stderr)
        return exc.returncode or 1
    except OSError as exc:
        # git missing, or the exclude file unreadable while deciding about docs/adr/.
        print(f"Checkpoint failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
