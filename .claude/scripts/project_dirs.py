#!/usr/bin/env python3
"""SessionStart hook — keep Claude's memory and temp files inside the project root.

Creates ``<root>/claude-memory/`` and ``<root>/claude-temp/`` and points the harness memory
directory (``~/.claude/projects/<slug>/memory``) at ``<root>/claude-memory`` — so auto-memory
reads and writes land in the project, travel with its backups, and die with it, never in the
global state dir. Those two dirs, plus ``/tasks/`` and ``/docs/adr/`` — the local task docs
and decision records — are excluded locally via ``.git/info/exclude``, never in the project's
``.gitignore``: they are one developer's working files, not a fact about the repo. The two
doc dirs are only excluded, never created, and all four entries are root-anchored where it
matters, so a project's own ``src/tasks/`` keeps being tracked. Idempotent: safe to run on
every session start.

The wiring is a symlink where the OS gives one for free, and an NTFS junction on Windows,
where creating a symlink needs Developer Mode or an elevated shell (D14). A junction is an
ordinary user's right, so the hook never asks the user to change a system setting.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":  # stdlib, but Windows-only — no import to make elsewhere
    import _winapi

IGNORE_ENTRIES: tuple[str, ...] = ("claude-memory/", "claude-temp/", "/tasks/", "/docs/adr/")


def _exclude_file(root: Path) -> Path | None:
    """Locate git's local-only exclude file for ``root``.

    Asks git rather than assuming ``<root>/.git/info/exclude``: in a worktree or a submodule
    ``.git`` is a file pointing elsewhere, and only git knows where the real dir lives.

    Args:
        root: The project root.

    Returns:
        The path to ``info/exclude``, or None when ``root`` is not a git repo (or git is
        not installed) — in which case there is nothing to exclude and the hook does nothing.
    """
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--git-path", "info/exclude"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    rel = done.stdout.strip()
    if not rel:
        return None
    path = Path(rel)
    return path if path.is_absolute() else root / path


def _ensure_excluded(root: Path) -> None:
    """Add the local-only dirs to ``.git/info/exclude`` — never the project's ``.gitignore``.

    The entries are one developer's local state, so they belong in the repo's private exclude
    file, which is never committed and never shows up in anyone else's diff. Bytes are read
    and written raw so an existing file keeps its own newline style (CRLF stays CRLF).

    Args:
        root: The project root; a non-repo root is left untouched.
    """
    path = _exclude_file(root)
    if path is None:
        return
    text = path.read_bytes().decode("utf-8") if path.is_file() else ""
    # Compare stripped of slashes so an existing ``tasks/`` counts as covering ``/tasks/``.
    present = {line.strip().strip("/") for line in text.splitlines()}
    missing = [e for e in IGNORE_ENTRIES if e.strip("/") not in present]
    if not missing:
        return
    newline = "\r\n" if "\r\n" in text else "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    out = newline.join(text.splitlines() + missing) + newline
    path.write_bytes(out.encode("utf-8"))


def _harness_slug(root: Path) -> str:
    """Return the harness's project-directory name for a project root.

    The harness slugs a path by replacing its separators with ``-``. On POSIX that is
    exactly today's behaviour (``/Users/me/proj`` → ``-Users-me-proj``). On Windows both
    separators fold, and so does the drive colon, which cannot otherwise appear in a path
    component: ``C:\\Users\\me\\proj`` → ``C--Users-me-proj``. The colon is folded *only*
    there, so a POSIX directory with a colon in its name still slugs as it does today.

    Args:
        root: The resolved project root.

    Returns:
        The slug — the directory name under ``~/.claude/projects/``.
    """
    slug = str(root).replace(os.sep, "-")
    if sys.platform == "win32":
        slug = slug.replace(os.altsep or "/", "-").replace(":", "-")
    return slug


def _already_wired(harness_mem: Path, mem: Path) -> bool:
    """Say whether the harness path already points at the project's memory dir.

    ``is_symlink`` alone is not enough: on Windows the wiring is a junction, a different
    kind of reparse point that reads as a plain directory to some checks. Mistaking one
    for a real directory would migrate the memory dir into itself and then delete it, so
    the resolved target is what decides.

    Args:
        harness_mem: The harness's memory path for this project.
        mem: The project's own memory directory.

    Returns:
        True when the link already exists and leads to ``mem``.
    """
    if harness_mem.is_symlink():
        return True
    try:
        return harness_mem.exists() and harness_mem.resolve() == mem.resolve()
    except OSError:
        return False


def _link(harness_mem: Path, mem: Path) -> None:
    """Point ``harness_mem`` at ``mem`` — a symlink, or an NTFS junction on Windows (D14).

    Args:
        harness_mem: The path to create.
        mem: The project's memory directory it should lead to.

    Raises:
        OSError: If neither form can be created.
    """
    try:
        harness_mem.symlink_to(mem)
    except OSError:
        if sys.platform == "win32":
            # Windows without Developer Mode: symlinks need a privilege a junction does
            # not. The guard is positive, and identical to the import's, so the name is
            # only ever read on the platform that binds it.
            _winapi.CreateJunction(str(mem), str(harness_mem))
        else:
            raise  # a POSIX symlink failure is a real error, not a platform quirk


def _link_harness_memory(root: Path, mem: Path) -> None:
    """Link the harness memory dir for ``root`` to ``mem``, migrating any existing files."""
    harness_mem = Path.home() / ".claude" / "projects" / _harness_slug(root) / "memory"
    if _already_wired(harness_mem, mem):
        return
    if harness_mem.is_dir():  # migrate whatever the harness already stored
        for f in harness_mem.iterdir():
            target = mem / f.name
            if not target.exists():
                shutil.move(str(f), target)
        shutil.rmtree(harness_mem)
    harness_mem.parent.mkdir(parents=True, exist_ok=True)
    _link(harness_mem, mem)


def main() -> None:
    """Create the project-local claude dirs and wire the harness memory path to them."""
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}
    root = Path(data.get("cwd") or Path.cwd()).resolve()
    if root == (Path.home() / ".claude").resolve():
        return  # summoned inside the global config itself — nothing to wire
    mem = root / "claude-memory"
    mem.mkdir(exist_ok=True)
    (root / "claude-temp").mkdir(exist_ok=True)
    _ensure_excluded(root)
    _link_harness_memory(root, mem)


if __name__ == "__main__":
    main()
