#!/usr/bin/env python3
"""SessionEnd hook — empty the project's scratch except ``keep/``, drop spent worktrees.

Reads a Claude Code hook event as JSON on stdin and leaves the project as the session found
it (ADR-0007). Three steps, in this order because a worktree may live inside the scratch root:

- **Worktrees.** ``git worktree list --porcelain`` in the project root; a listed worktree is
  removed only when it sits under ``<root>/.claude/worktrees/`` (where the Agent tool's
  ``isolation: worktree`` puts them) or under ``<root>/claude-temp/``, is not locked (the
  harness locks a subagent's worktree while that agent runs), and has nothing uncommitted.
  ``git worktree remove`` is called without ``--force``, so a checkout that turned dirty
  between the check and the call is refused by git rather than destroyed, and **no branch is
  ever deleted** — the work survives the checkout.
- **The sweep.** Every entry directly under ``<root>/claude-temp/`` goes, except ``keep/``,
  which is the keep-list: whatever a later session needs is moved there before this hook
  runs. Not "this session's entries" — all of them; hooks receive no PID, so ownership is
  undecidable, and the human runs one session per project (ADR-0007 §2). The one exception
  is a worktree the step above deliberately spared: ADR-0007 §5 leaves a dirty or locked
  worktree in place, so a scratch entry that *is* or *contains* one is stepped over whole —
  the sweep must not delete through the back door what the worktree rule refused.
- **The prune.** ``git worktree prune`` runs last, so the metadata of every worktree whose
  directory has just gone — swept or removed — is cleared in the same session.

Safety is the same shape as the rest of the hooks: a payload whose ``cwd`` is missing or
malformed makes the hook a no-op rather than a guess, a root resolving to ``~/.claude``
itself is skipped exactly as ``project_dirs.py`` skips it, a symlink is unlinked and never
followed into its target, and one unremovable entry is noted on stderr while the rest of the
sweep continues. The exit code is always 0 — SessionEnd output is shown to nobody and must
never wedge a session ending; the only visible evidence of a failed sweep is the leftover
line the next SessionStart prints.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

#: The scratch root and the one name inside it the sweep never touches — the same names
#: ``project_dirs.py`` creates at SessionStart.
SCRATCH_DIR: Final[str] = "claude-temp"
KEEP_DIR: Final[str] = "keep"

#: The only places a worktree may be removed from, as path parts under the project root:
#: the harness's own worktree location, and the scratch root (ADR-0007 §5).
WORKTREE_ROOTS: Final[tuple[tuple[str, ...], ...]] = ((".claude", "worktrees"), (SCRATCH_DIR,))


def _git(args: list[str], cwd: Path) -> str | None:
    """Run one git command and return its stdout.

    Args:
        args: The git arguments, without the leading ``git``.
        cwd: The directory to run in.

    Returns:
        The command's stdout, or None when git is missing, the directory is not a repo, or
        the command failed — every one of which means "skip this step", never "fail".
    """
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout


def parse_worktrees(porcelain: str) -> list[tuple[Path, bool]]:
    """Parse ``git worktree list --porcelain`` into one ``(path, locked)`` pair per worktree.

    Blocks are separated by a blank line and always open with ``worktree <path>``; the other
    lines (``HEAD``, ``branch``, ``bare``, ``detached``) carry nothing this hook decides on,
    except ``locked``, which may appear bare or with a reason after it.

    Args:
        porcelain: The command's stdout.

    Returns:
        The worktrees in git's order — the main worktree first, which callers must skip.
    """
    found: list[tuple[Path, bool]] = []
    for block in porcelain.split("\n\n"):
        path: Path | None = None
        locked = False
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree "))
            elif line == "locked" or line.startswith("locked "):
                locked = True
        if path is not None:
            found.append((path, locked))
    return found


def _removable(resolved: Path, roots: list[Path], locked: bool, root: Path) -> bool:
    """Say whether one listed worktree qualifies for removal (ADR-0007 §5).

    Args:
        resolved: The worktree's resolved path.
        roots: The locations a removable worktree may live under.
        locked: Whether git reported it locked.
        root: The project root, where the status call is issued from.

    Returns:
        True only when the worktree is unlocked, inside one of ``roots``, and clean.
    """
    if locked or not any(resolved.is_relative_to(known) for known in roots):
        return False
    # Empty stdout is a clean checkout; None is a status that could not be taken, and an
    # unknown state is never treated as clean.
    return _git(["-C", str(resolved), "status", "--porcelain"], root) == ""


def remove_spent_worktrees(root: Path) -> set[Path]:
    """Remove every clean, unlocked worktree in a known location; name the ones left behind.

    Args:
        root: The project root. A root that is not a git repo — or a host with no git — is
            left alone; the sweep still runs.

    Returns:
        The resolved paths of the worktrees still on disk — out of the known locations,
        locked, dirty, or refused by git. The sweep needs them so it does not delete a
        checkout this step deliberately spared (ADR-0007 §5).
    """
    listing = _git(["worktree", "list", "--porcelain"], root)
    if listing is None:
        return set()
    # The roots stay unresolved on purpose: a *resolved* path is compared against them, so a
    # symlinked ``.claude/worktrees`` leads out of the root, matches nothing, and its
    # worktrees are spared rather than followed (ADR-0007 §6, same stance as the scratch root).
    roots = [root.joinpath(*parts) for parts in WORKTREE_ROOTS]
    spared: set[Path] = set()
    for path, locked in parse_worktrees(listing)[1:]:  # [0] is the main worktree
        try:
            resolved = path.resolve()
        except OSError:
            continue  # a path that cannot even be resolved is nothing the sweep can match
        removed = _removable(resolved, roots, locked, root) and (
            _git(["worktree", "remove", str(resolved)], root) is not None
        )
        if not removed:
            spared.add(resolved)
    return spared


def _shelters_worktree(entry: Path, spared: set[Path]) -> bool:
    """Whether a scratch entry is, or contains, a worktree the worktree step left in place.

    A worktree can sit a level or two down — ``claude-temp/sessions/<id>/wt`` — so the whole
    top-level entry is stepped over, coarse on purpose: the alternative is deleting around a
    live checkout.

    Args:
        entry: A path directly under the scratch root.
        spared: The resolved worktree paths from :func:`remove_spent_worktrees`.

    Returns:
        True when the entry must not be swept.
    """
    if not spared:
        return False
    try:
        resolved = entry.resolve()
    except OSError:
        return False
    return any(worktree.is_relative_to(resolved) for worktree in spared)


def _clear_readonly(func: Callable[[str], object], path: str, error: BaseException) -> None:
    """``shutil.rmtree`` error handler: retry a Windows read-only file, re-raise the rest.

    Windows refuses to delete a file carrying the read-only bit — git's object store is full
    of them — so the bit is cleared and the failed operation retried, the same win32 care
    ``project_dirs.py`` takes over symlinks. Everywhere else the original error stands.

    Args:
        func: The operation that failed (``os.unlink``, ``os.rmdir``, ``os.scandir``).
        path: The path it failed on.
        error: The exception it raised.

    Raises:
        BaseException: ``error`` itself, whenever the retry does not apply.
    """
    if sys.platform != "win32" or not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _remove(entry: Path) -> None:
    """Delete one scratch entry — a symlink is unlinked, a directory is removed whole.

    Symlinks are checked before directories because ``is_dir()`` follows them. Inside a
    directory ``shutil.rmtree`` is already symlink-aware: it unlinks a symlinked
    subdirectory rather than descending into its target.

    Args:
        entry: The path to remove.

    Raises:
        OSError: If the entry cannot be removed.
    """
    if entry.is_symlink() or not entry.is_dir():
        entry.unlink()
    else:
        shutil.rmtree(entry, onexc=_clear_readonly)


def sweep_scratch(temp: Path, spared: set[Path]) -> None:
    """Empty the scratch root except ``keep/``, surviving anything that will not go.

    Args:
        temp: The scratch root, ``<root>/claude-temp``.
        spared: Worktrees the worktree step left in place; an entry holding one is skipped.
    """
    try:
        entries = sorted(temp.iterdir())
    except OSError as error:
        print(f"session_cleanup: cannot read {temp}: {error}", file=sys.stderr)
        return
    for entry in entries:
        if entry.name == KEEP_DIR or _shelters_worktree(entry, spared):
            continue
        try:
            _remove(entry)
        except OSError as error:
            # One stuck entry is reported and stepped over; it must not abort the sweep.
            print(f"session_cleanup: could not remove {entry}: {error}", file=sys.stderr)


def main() -> int:
    """Entry point: read the payload, drop spent worktrees, empty the scratch root, prune.

    Returns:
        Always 0 — SessionEnd cannot block anything, and a hook that fails loudly here would
        only add noise nobody is shown.
    """
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    cwd = data.get("cwd") if isinstance(data, dict) else None
    if not isinstance(cwd, str) or not cwd:
        return 0
    try:
        root = Path(cwd).resolve()
        if root == (Path.home() / ".claude").resolve():
            return 0  # the global config itself is never swept
        temp = root / SCRATCH_DIR
        # A symlinked scratch root would sweep its target, outside the root the hook is
        # allowed to touch — fail closed rather than follow it (ADR-0007 §6).
        if temp.is_symlink() or not temp.is_dir():
            return 0
    except OSError:
        return 0
    spared = remove_spent_worktrees(root)  # first: a worktree may live inside the scratch
    sweep_scratch(temp, spared)
    # Last, so metadata for a worktree the sweep just deleted goes in this run too.
    _git(["worktree", "prune"], root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
