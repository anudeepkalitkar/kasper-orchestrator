#!/usr/bin/env python3
"""SessionStart hook — keep Claude's memory and temp files inside the project root.

Creates ``<root>/claude-memory/`` and ``<root>/claude-temp/`` and points the harness memory
directory (``~/.claude/projects/<slug>/memory``) at ``<root>/claude-memory`` — so auto-memory
reads and writes land in the project, travel with its backups, and die with it, never in the
global state dir. Those two dirs, plus ``/tasks/`` — the local task
docs — are excluded locally via ``.git/info/exclude``, never in the project's ``.gitignore``:
they are one developer's working files, not a fact about the repo. ``/docs/adr/`` joins them
only where the decision record stays local — a public repo, or one whose visibility cannot be
read; in a private repo the ADRs are committed, so the entry is left out and an earlier run's
line is removed. The doc dirs are only excluded, never created, and the entries are
root-anchored where it matters, so a project's own ``src/tasks/`` keeps being tracked.
Idempotent: safe to run on every session start.

Inside ``claude-temp/`` it also prepares this session's scratch —
``claude-temp/sessions/<session_id>/``, so two agents' files in one project cannot collide by
name — and prints that path into Claude's context (SessionStart stdout is context).

The wiring is a symlink where the OS gives one for free, and an NTFS junction on Windows,
where creating a symlink needs Developer Mode or an elevated shell (D14). A junction is an
ordinary user's right, so the hook never asks the user to change a system setting.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":  # stdlib, but Windows-only — no import to make elsewhere
    import _winapi

IGNORE_ENTRIES: tuple[str, ...] = ("claude-memory/", "claude-temp/", "/tasks/", "/docs/adr/")

#: The decision record — excluded in a public repo, committed (so never excluded) in a private one.
ADR_ENTRY: str = "/docs/adr/"

#: Seconds the visibility lookup may take before the hook falls back to "not private".
VISIBILITY_TIMEOUT_S: float = 5.0

#: The scratch root, and the container inside it this hook gives each session.
SCRATCH_DIR: str = "claude-temp"
SESSIONS_DIR: str = "sessions"


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


def _is_private_repo(root: Path) -> bool:
    """Say whether ``root``'s GitHub repository is private.

    Asks the ``gh`` CLI, the only thing on this machine that knows a remote's visibility.
    Every other outcome — a public or internal repo, ``gh`` missing or unrunnable, no remote,
    not logged in, a call that hangs — answers "not private", because that fallback keeps the
    decision record local-only rather than risking it being staged into a public repo. The
    lookup never raises: a SessionStart that died here would leave the repo with no exclusions
    and no memory link at all, so a launch failure is reported on stderr and swallowed.

    Args:
        root: The project root; the lookup runs there so gh resolves that repo's remote.

    Returns:
        True only when gh reports ``PRIVATE``.
    """
    try:
        done = subprocess.run(
            ["gh", "repo", "view", "--json", "visibility", "-q", ".visibility"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=VISIBILITY_TIMEOUT_S,
            # An inherited GH_REPO would answer for *that* repo, not the checkout at ``root``.
            env={k: v for k, v in os.environ.items() if k != "GH_REPO"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # Best-effort diagnostic: a broken or closed stderr must not undo the fallback.
        with contextlib.suppress(OSError, ValueError):
            print(
                f"Repo visibility unknown ({type(exc).__name__}); ADRs stay local-only.",
                file=sys.stderr,
            )
        return False
    return done.returncode == 0 and done.stdout.strip() == "PRIVATE"


def _ensure_excluded(root: Path) -> bool | None:
    """Add the local-only dirs to ``.git/info/exclude`` — never the project's ``.gitignore``.

    The entries are one developer's local state, so they belong in the repo's private exclude
    file, which is never committed and never shows up in anyone else's diff. ``/docs/adr/`` is
    the one conditional entry: a private repo commits its ADRs, so there the entry is left out
    and a line an earlier run wrote is dropped. Bytes are read and written raw so an existing
    file keeps its own newline style (CRLF stays CRLF), and a run that changes nothing rewrites
    nothing.

    Args:
        root: The project root; a non-repo root is left untouched.

    Returns:
        Whether the repo is private, or None when ``root`` is not a git repo at all.
    """
    path = _exclude_file(root)
    if path is None:
        return None
    private = _is_private_repo(root)
    text = path.read_bytes().decode("utf-8") if path.is_file() else ""
    lines = text.splitlines()
    adr = ADR_ENTRY.strip("/")
    # Compare stripped of slashes so an existing ``tasks/`` counts as covering ``/tasks/``.
    kept = [line for line in lines if not (private and line.strip().strip("/") == adr)]
    present = {line.strip().strip("/") for line in kept}
    wanted = IGNORE_ENTRIES if not private else tuple(e for e in IGNORE_ENTRIES if e != ADR_ENTRY)
    missing = [e for e in wanted if e.strip("/") not in present]
    if kept == lines and not missing:
        return private
    newline = "\r\n" if "\r\n" in text else "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = kept + missing
    path.write_bytes((newline.join(body) + newline if body else "").encode("utf-8"))
    return private


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


def _session_id(value: object) -> str | None:
    """Validate the payload's ``session_id`` as a directory name.

    The id is joined into a path, so it is accepted only as a plain name: no separators, no
    drive, and no dot-only name. ``Path(value).name`` refuses the first two but *keeps*
    ``".."`` — which would make the scratch path ``claude-temp/sessions/../``, i.e. the
    scratch root itself — so dot-only names (``.``, ``..``, ``...``) are refused by name.

    Args:
        value: The raw ``session_id`` from the hook payload, of whatever type it arrived as.

    Returns:
        The id when it names exactly one new directory, else None (the session simply gets
        no scratch dir — ADR-0007 §6: a malformed payload is never guessed at).
    """
    if not isinstance(value, str) or not value or set(value) == {"."}:
        return None
    return value if value == Path(value).name else None


def main() -> None:
    """Create the project-local claude dirs, wire the harness memory path, report scratch.

    Prints, for a git repo, where this project's ADRs live — the exclude file's split is
    invisible otherwise, and the session needs to know before it writes one.
    """
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}
    root = Path(data.get("cwd") or Path.cwd()).resolve()
    if root == (Path.home() / ".claude").resolve():
        return  # summoned inside the global config itself — nothing to wire
    mem = root / "claude-memory"
    mem.mkdir(exist_ok=True)
    temp = root / SCRATCH_DIR
    temp.mkdir(exist_ok=True)
    session_id = _session_id(data.get("session_id"))
    if session_id is not None:
        (temp / SESSIONS_DIR / session_id).mkdir(parents=True, exist_ok=True)
        print(f"Session scratch: {SCRATCH_DIR}/{SESSIONS_DIR}/{session_id}/")
    private = _ensure_excluded(root)
    if private is not None:
        print("ADRs: tracked (private repo)" if private else "ADRs: local-only")
    _link_harness_memory(root, mem)


if __name__ == "__main__":
    main()
