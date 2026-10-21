#!/usr/bin/env python3
"""Portable hook entrypoint: ``run_hook.py <name>`` dispatches to one hook script (D11).

Every hook command in ``settings.json`` runs through this dispatcher instead of carrying
its own POSIX shell prelude
(``s="$CLAUDE_PROJECT_DIR/.claude/scripts/x.py"; [ -f "$s" ] || s="$HOME/…"; python3 "$s"``)
— a form no native-Windows shell can run, and the last POSIX-only surface in the hook
wiring. The user's home copy (``~/.claude/scripts/<name>.py``) is what runs. The project's
own ``.claude/scripts/<name>.py`` wins only when the project root is the KASPER source repo
the home was installed from — the ``source`` path ``install.py`` records in
``~/.claude/kasper-manifest.json``. That record lives outside every clone, so config
development is tested live in the source checkout while no other repo can swap in its own
permission gate or recorder; with no readable record, the home copy always runs.

The target runs **in this process** via ``runpy``, so it inherits this process's stdin
(the hook payload), its stdout, and its exit code exactly as it did when the shell
invoked it directly. Nothing is buffered, parsed, or re-emitted in between.
"""

from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path

#: Exit code for a dispatch failure (bad name, no script installed). Deliberately NOT 2:
#: Claude Code reads 2 as a *blocking* error, so a missing script would block every Bash
#: call (PreToolUse) or refuse to let the session stop (Stop). 1 is a non-blocking error —
#: the breakage is shown to the user instead of wedging the session.
_DISPATCH_ERROR = 1

#: The install record ``install.py`` writes into the Claude home; its ``source`` is trusted.
_MANIFEST_NAME = "kasper-manifest.json"


def is_kasper_source(project_dir: Path, home: Path) -> bool:
    """Whether ``project_dir`` is the source repo this home was installed from.

    Args:
        project_dir: The project root (``$CLAUDE_PROJECT_DIR``).
        home: The user's home directory, whose ``.claude/`` holds the install manifest.

    Returns:
        True only when the manifest is readable and its ``source`` resolves to the same
        directory as ``project_dir``; any missing or malformed record is False (fail closed).
    """
    try:
        manifest = json.loads((home / ".claude" / _MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    source = manifest.get("source") if isinstance(manifest, dict) else None
    if not isinstance(source, str) or not source:
        return False
    return Path(source).resolve() == project_dir.resolve()


def hook_script(name: str, project_dir: str | None, home: Path) -> Path | None:
    """Resolve a hook name to the script that should run.

    The project copy is a candidate only in the recorded source repo (:func:`is_kasper_source`);
    everywhere else the home copy is the only one — a hook is a security boundary, and a
    cloned repo must not be able to replace it.

    Args:
        name: The hook script's stem, as written in the settings command.
        project_dir: ``$CLAUDE_PROJECT_DIR``, or None when the harness did not set it.
        home: The user's home directory (``%USERPROFILE%`` on Windows).

    Returns:
        The first candidate that exists, or None when no eligible copy is installed.
    """
    candidates: list[Path] = []
    if project_dir and is_kasper_source(Path(project_dir), home):
        candidates.append(Path(project_dir) / ".claude" / "scripts" / f"{name}.py")
    candidates.append(home / ".claude" / "scripts" / f"{name}.py")
    return next((path for path in candidates if path.is_file()), None)


def main(argv: list[str] | None = None) -> int:
    """Dispatch to one hook script.

    Args:
        argv: The argument vector — exactly one element, the hook name (``None`` →
            ``sys.argv[1:]``).

    Returns:
        0 when the target ran to completion, :data:`_DISPATCH_ERROR` when it could not be
        dispatched at all. A target that ends in ``sys.exit``/``SystemExit`` — the normal
        case, and how a hook signals 2 to block a tool call — raises straight through this
        function, so the harness sees the target's own exit code, never a code invented
        here.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("run_hook: usage: run_hook.py <hook-name>", file=sys.stderr)
        return _DISPATCH_ERROR
    name = args[0]
    if name != Path(name).name:
        # The name is joined into a path, so anything that is not a bare stem — a
        # separator, a parent ref, a drive — is refused rather than resolved.
        print(f"run_hook: refusing hook name {name!r}", file=sys.stderr)
        return _DISPATCH_ERROR
    target = hook_script(name, os.environ.get("CLAUDE_PROJECT_DIR"), Path.home())
    if target is None:
        print(f"run_hook: no hook script named {name!r}", file=sys.stderr)
        return _DISPATCH_ERROR
    # The target sees the argv it would have had if the shell had run it directly; hooks
    # take no arguments, and the two command helpers that do read argv[1:] see it empty.
    sys.argv = [str(target)]
    runpy.run_path(str(target), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
