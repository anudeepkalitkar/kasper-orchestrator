#!/usr/bin/env python3
"""Install KASPER's configuration into a Claude home — copy, merge, check, remove (ADR-0006).

``python3 install.py`` copies this repository's ``.claude/`` owned paths (§3) into
``~/.claude/``, merges ``settings.json`` (§4) and the permission ledger (§5) instead of
replacing them, backs up whatever it is about to change (§6), and records what it wrote in
``kasper-manifest.json`` (§7). ``--dry-run`` (§8), ``--status`` (§9) and ``--uninstall``
(§10) read the same manifest.

Stdlib only, by decision: the script has to run on a fresh clone with nothing installed but
Python. The pure logic — rendering, stripping, merging, hashing, planning — is kept free of
I/O so the unit tier can exercise it without a disk.

JSON here is genuinely schema-free (a user's ``settings.json`` may hold any key Claude Code
understands), so the loaded documents are typed ``dict[str, Any]``; the manifest, which this
script alone writes, gets a real :class:`Manifest` shape. What the script *does* rely on —
the hook block's nesting, the ledger's containers, the manifest's ``files`` map — is
shape-checked as each file is loaded, so a hand-edited home file fails with one clear
message before anything is written, never with a traceback half way through.

Exit codes follow §2 — 0 clean, 1 drift or something left behind, 2 usage — with one
reading the ADR leaves open: an *error* (corrupt file, unreadable home) also exits 1, since
§2 names no third code for it.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, TextIO, TypedDict

#: The floor ADR-0006 §1 pins. Checked here, above the first import that needs it, so an
#: older interpreter meets one sentence instead of ``ImportError: cannot import name 'UTC'``.
MINIMUM_PYTHON: tuple[int, int] = (3, 12)

if sys.version_info < MINIMUM_PYTHON:
    _wanted = ".".join(str(part) for part in MINIMUM_PYTHON)
    _found = ".".join(str(part) for part in sys.version_info[:3])
    print(f"install.py needs Python {_wanted} or newer — this is {_found}", file=sys.stderr)
    raise SystemExit(1)

from datetime import UTC, datetime  # noqa: E402 — datetime.UTC is 3.11+, so it waits

#: The paths copied verbatim from the repository's ``.claude/`` — KASPER's own (§3).
OWNED_PATHS: tuple[str, ...] = (
    "agents",
    "commands",
    "rules",
    "scripts",
    "skills",
    "sounds",
    "CLAUDE.md",
)

#: The record of one install, written into the Claude home (§7).
MANIFEST_NAME: str = "kasper-manifest.json"

#: A hook command containing this is KASPER's wiring — current or retired (§4).
HOOK_MARKER: str = "run_hook.py"

#: The permission ledger, merged rather than owned (§5).
LEDGER_NAME: str = "permissions-ledger.json"

#: The Claude settings file, merged rather than owned (§4).
SETTINGS_NAME: str = "settings.json"

#: This script sits at the repository root, and its ``.claude/`` is the install source.
_SOURCE_ROOT: Path = Path(__file__).resolve().parent

#: The interpreter the repository's hook commands are authored with; the only token
#: ``--python`` rewrites (§2), and only at the head of a command (§11 keeps ``$HOME``).
_AUTHORED_PYTHON: str = "python3 "

_MANIFEST_SCHEMA: int = 1
_BACKUP_PREFIX: str = "kasper-backup-"
_BACKUP_STAMP: str = "%Y%m%dT%H%M%SZ"
_PERMISSION_LISTS: tuple[str, str] = ("allow", "deny")
_LEDGER_PATTERN_LISTS: tuple[str, str] = ("allow_patterns", "ask_patterns")
_PYCACHE: str = "__pycache__"
_JSON_INDENT: int = 2
_TEMP_SUFFIX: str = ".kasper-tmp"
_GIT_TIMEOUT_SECONDS: int = 10

#: A settings hook block: event -> matcher groups -> handlers.
HookBlock = dict[str, list[dict[str, Any]]]

#: What an install would do to one owned file (§8), what ``--status`` finds (§9), and what
#: ``--uninstall`` does with it (§10). The last two are the same comparison in two
#: vocabularies, so both are spoken by :func:`_compare_hashes`.
FileKind = Literal["create", "overwrite", "unchanged"]
StatusKind = Literal["ok", "modified", "missing"]
RemovalKind = Literal["delete", "keep", "missing"]
_Comparison = Literal["match", "differ", "gone"]


class _ManifestEscape(ValueError):
    """A manifest key names a path outside the home — fatal in every mode.

    Its own type, not its message, is what lets :func:`_previous_manifest_files` forgive an
    unreadable manifest while never forgiving one that would delete outside the home. Still
    a ``ValueError``, so the CLI reports it like any other bad file.
    """


class Manifest(TypedDict):
    """The ``kasper-manifest.json`` document, schema 1 (§7)."""

    schema: int
    source: str
    source_commit: str | None
    installed_at: str
    python: str
    files: dict[str, str]


# ------------------------------------------------------------------ pure logic


def default_python(platform: str) -> str:
    """The interpreter name to render into the hook commands on a platform.

    Args:
        platform: A ``sys.platform`` string.

    Returns:
        ``python`` on Windows, which ships no ``python3`` on PATH; ``python3`` elsewhere.
    """
    return "python" if platform.startswith("win") else "python3"


def sha256_hex(data: bytes) -> str:
    """The SHA-256 of some bytes, hex encoded.

    Args:
        data: The bytes to hash.

    Returns:
        The 64-character lowercase digest recorded in the manifest.
    """
    return hashlib.sha256(data).hexdigest()


def render_hooks(hooks: HookBlock, python: str) -> HookBlock:
    """Rewrite the interpreter at the head of every hook command.

    Only a leading ``python3 `` is replaced: anything further along the command line —
    ``/usr/bin/env python3 tool.py``, a literal ``$HOME``, the user's own handlers — is
    left exactly as written (§2, §11).

    Args:
        hooks: A hook block; not modified.
        python: The interpreter name to render.

    Returns:
        A deep copy carrying the chosen interpreter.
    """
    rendered = copy.deepcopy(hooks)
    for groups in rendered.values():
        for group in groups:
            for handler in group["hooks"]:
                command = handler.get("command", "")
                if command.startswith(_AUTHORED_PYTHON):
                    handler["command"] = f"{python} {command[len(_AUTHORED_PYTHON) :]}"
    return rendered


def strip_kasper_hooks(hooks: HookBlock) -> HookBlock:
    """Remove every KASPER handler from a hook block, across every event.

    A handler whose command names :data:`HOOK_MARKER` is KASPER's — current wiring or
    retired. A matcher group left with no handlers goes with them, and an event left with
    no groups goes too. Handlers naming any other command are untouched (§4, §10).

    Args:
        hooks: A hook block; not modified.

    Returns:
        A deep copy holding only the handlers KASPER does not own.
    """
    stripped: HookBlock = {}
    for event, groups in hooks.items():
        kept: list[dict[str, Any]] = []
        for group in groups:
            handlers = [h for h in group["hooks"] if HOOK_MARKER not in h.get("command", "")]
            if handlers:
                kept.append({**group, "hooks": handlers})
        if kept:
            stripped[event] = kept
    return copy.deepcopy(stripped)


def merge_settings(
    existing: Mapping[str, Any], kasper: Mapping[str, Any], python: str
) -> dict[str, Any]:
    """Merge KASPER's settings into an existing ``settings.json`` (§4).

    Previous KASPER hooks are stripped before the current ones are appended, so a re-install
    replaces its own wiring instead of doubling it; ``allow`` and ``deny`` become the union
    with the existing order kept; every other key — the user's model, theme, their own hooks
    and permission keys — survives untouched.

    Args:
        existing: The home settings, or ``{}`` when there are none; not modified.
        kasper: The repository's settings.
        python: The interpreter to render into KASPER's hook commands.

    Returns:
        A new settings document, ready to write.
    """
    merged: dict[str, Any] = copy.deepcopy(dict(existing))
    hooks = strip_kasper_hooks(merged.get("hooks", {}))
    for event, groups in render_hooks(kasper.get("hooks", {}), python).items():
        hooks.setdefault(event, []).extend(groups)
    # A file with no hooks on either side gains no hollow key; one that had them keeps the
    # key even when the strip emptied it, because removing it is not this script's business.
    if hooks or "hooks" in merged:
        merged["hooks"] = hooks

    permissions: dict[str, Any] = merged.get("permissions", {})
    kasper_permissions: Mapping[str, Any] = kasper.get("permissions", {})
    for key in _PERMISSION_LISTS:
        entries: list[Any] = list(permissions.get(key, []))
        entries += [entry for entry in kasper_permissions.get(key, []) if entry not in entries]
        if entries:
            permissions[key] = entries
    if permissions:
        merged["permissions"] = permissions
    return merged


def merge_ledger(existing: Mapping[str, Any] | None, seed: Mapping[str, Any]) -> dict[str, Any]:
    """Merge the repository's ledger seed into a home ledger (§5).

    Nothing is ever removed and nothing existing is ever rewritten: absent ``allow_keys``
    are added, ``allow_patterns`` and ``ask_patterns`` entries absent *by pattern* are
    appended, and ``grants``, ``denials`` and ``_doc`` are left exactly as found — grants
    are per-machine, only the seed is portable.

    Args:
        existing: The home ledger, or None when the home has none; not modified.
        seed: The repository's ledger.

    Returns:
        A new ledger document, ready to write.
    """
    if existing is None:
        return copy.deepcopy(dict(seed))
    merged: dict[str, Any] = copy.deepcopy(dict(existing))
    allow_keys: dict[str, Any] = merged.setdefault("allow_keys", {})
    for key, note in seed.get("allow_keys", {}).items():
        allow_keys.setdefault(key, note)
    for name in _LEDGER_PATTERN_LISTS:
        entries: list[Any] = merged.setdefault(name, [])
        known = {entry.get("pattern") for entry in entries}
        entries.extend(
            copy.deepcopy(entry)
            for entry in seed.get(name, [])
            if entry.get("pattern") not in known
        )
    return merged


def plan_files(source: Mapping[str, bytes], existing: Mapping[str, bytes]) -> dict[str, FileKind]:
    """Classify what an install would do to each owned file, by bytes alone (§8).

    Args:
        source: Owned relative path -> the bytes to install.
        existing: The bytes already at those paths, for the paths that exist.

    Returns:
        Relative path -> ``create``, ``overwrite`` or ``unchanged``. Home files the source
        does not own are not the installer's business and never appear.
    """
    plan: dict[str, FileKind] = {}
    for rel, content in source.items():
        if rel not in existing:
            plan[rel] = "create"
        else:
            plan[rel] = "unchanged" if existing[rel] == content else "overwrite"
    return plan


def status_report(
    manifest_files: Mapping[str, str], current: Mapping[str, str | None]
) -> dict[str, StatusKind]:
    """Judge each recorded file against what is on disk now (§9).

    Args:
        manifest_files: Relative path -> the SHA-256 the manifest recorded.
        current: Relative path -> its SHA-256 now, or None when the file is gone.

    Returns:
        Relative path -> ``ok``, ``modified`` or ``missing``.
    """
    words: dict[_Comparison, StatusKind] = {
        "match": "ok",
        "differ": "modified",
        "gone": "missing",
    }
    return {rel: words[state] for rel, state in _compare_hashes(manifest_files, current).items()}


def uninstall_plan(
    manifest_files: Mapping[str, str], current: Mapping[str, str | None]
) -> dict[str, RemovalKind]:
    """Decide what uninstall may remove: only files it installed and nobody edited (§10).

    Args:
        manifest_files: Relative path -> the SHA-256 the manifest recorded.
        current: Relative path -> its SHA-256 now, or None when the file is gone.

    Returns:
        Relative path -> ``delete``, ``keep`` (modified locally) or ``missing``.
    """
    words: dict[_Comparison, RemovalKind] = {
        "match": "delete",
        "differ": "keep",
        "gone": "missing",
    }
    return {rel: words[state] for rel, state in _compare_hashes(manifest_files, current).items()}


def _compare_hashes(
    manifest_files: Mapping[str, str], current: Mapping[str, str | None]
) -> dict[str, _Comparison]:
    """Compare each recorded hash with the file's hash now — the one judgement §9 and §10 share.

    Args:
        manifest_files: Relative path -> the SHA-256 the manifest recorded.
        current: Relative path -> its SHA-256 now, or None when the file is gone.

    Returns:
        Relative path -> ``match`` (untouched since the install), ``differ`` (edited
        locally) or ``gone``. Files the manifest never recorded are not judged.
    """
    states: dict[str, _Comparison] = {}
    for rel, digest in manifest_files.items():
        found = current.get(rel)
        if found is None:
            states[rel] = "gone"
        else:
            states[rel] = "match" if found == digest else "differ"
    return states


# ------------------------------------------------------------------ disk helpers


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object from disk.

    Args:
        path: The file to read.

    Returns:
        The decoded object.

    Raises:
        ValueError: When the file is not valid JSON, or is not a JSON object. A corrupt
            file stops the run — it is never silently overwritten.
        OSError: When the file cannot be read.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON ({exc}) — fix it or move it aside") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must hold a JSON object, found {type(data).__name__}")
    return data


def _check_shape(ok: bool, path: Path, what: str) -> None:
    """Refuse a file whose JSON parses but does not carry the structure this script reads.

    Args:
        ok: The condition that must hold.
        path: The file being judged, named in the message.
        what: What was expected, phrased for a user who has to go and fix it.

    Raises:
        ValueError: When ``ok`` is false.
    """
    if not ok:
        raise ValueError(f"{path} has an unexpected shape: {what}")


def _load_settings(path: Path) -> dict[str, Any]:
    """Load a settings document and check the parts this script merges (§4).

    The hook block's nesting and the two permission lists are read directly, so they are
    verified here — once, at the boundary — rather than crashing in the middle of a merge.

    Args:
        path: The settings file to read.

    Returns:
        The decoded document.

    Raises:
        ValueError: When the file is not valid JSON or its hooks/permissions are malformed.
        OSError: When the file cannot be read.
    """
    data = _load_json(path)
    hooks = data.get("hooks", {})
    _check_shape(isinstance(hooks, dict), path, "'hooks' must be an object")
    for event, groups in hooks.items():
        _check_shape(isinstance(groups, list), path, f"hooks.{event} must be a list of groups")
        for group in groups:
            _check_shape(isinstance(group, dict), path, f"hooks.{event} groups must be objects")
            handlers = group.get("hooks")
            _check_shape(
                isinstance(handlers, list), path, f"hooks.{event} groups need a 'hooks' list"
            )
            for handler in handlers:
                _check_shape(
                    isinstance(handler, dict), path, f"hooks.{event} handlers must be objects"
                )
                _check_shape(
                    isinstance(handler.get("command", ""), str),
                    path,
                    f"hooks.{event} handler commands must be strings",
                )
    permissions = data.get("permissions", {})
    _check_shape(isinstance(permissions, dict), path, "'permissions' must be an object")
    for key in _PERMISSION_LISTS:
        _check_shape(
            isinstance(permissions.get(key, []), list), path, f"permissions.{key} must be a list"
        )
    return data


def _load_ledger(path: Path) -> dict[str, Any]:
    """Load a permission ledger and check the containers this script merges (§5).

    Args:
        path: The ledger to read.

    Returns:
        The decoded document.

    Raises:
        ValueError: When the file is not valid JSON or its containers are malformed.
        OSError: When the file cannot be read.
    """
    data = _load_json(path)
    _check_shape(
        isinstance(data.get("allow_keys", {}), dict), path, "'allow_keys' must be an object"
    )
    for name in _LEDGER_PATTERN_LISTS:
        entries = data.get(name, [])
        _check_shape(isinstance(entries, list), path, f"'{name}' must be a list")
        for entry in entries:
            _check_shape(isinstance(entry, dict), path, f"'{name}' entries must be objects")
            # merge_ledger matches entries by this field and puts it in a set, so a
            # non-string here would be an unhashable-type TypeError past the CLI's handler.
            _check_shape(
                isinstance(entry.get("pattern", ""), str),
                path,
                f"'{name}' entries need a string 'pattern'",
            )
    return data


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    """Write a JSON object atomically: temp file in the same directory, then replace (§4).

    The destination's permission bits are carried across, so a ``chmod 600`` settings file
    does not come back world-readable, and the temp file never survives a failure.

    A symlinked destination is *replaced* by a real file — ``os.replace`` targets the link,
    not its target — so a merged ``settings.json`` or ledger linked into a dotfiles
    repository stops being a link, exactly as an owned path does (:func:`_write_file`). The
    deliberate half of that: nothing outside the home is ever written, and the previous
    content is in the backup directory; the cost: the user re-makes the link.

    Args:
        path: The destination file.
        data: The document to write.
    """
    text = json.dumps(data, indent=_JSON_INDENT, ensure_ascii=False) + "\n"
    temp = path.with_name(path.name + _TEMP_SUFFIX)
    try:
        temp.write_text(text, encoding="utf-8")
        if path.is_file():
            shutil.copymode(path, temp)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _backup_dir(home: Path, moment: datetime) -> Path:
    """The directory this run backs up into, uniquified on collision (§6).

    Two runs inside the same second would otherwise share a name and the second would
    overwrite the first's copies — the very files kept so nothing is lost.

    Args:
        home: The Claude home the directory sits in.
        moment: The instant that names it.

    Returns:
        A path that does not exist yet: the stamped name, else ``-2``, ``-3``, …
    """
    base = home / f"{_BACKUP_PREFIX}{moment.strftime(_BACKUP_STAMP)}"
    candidate = base
    attempt = 1
    while candidate.exists():
        attempt += 1
        candidate = base.with_name(f"{base.name}-{attempt}")
    return candidate


def _write_file(target: Path, content: bytes) -> None:
    """Write an owned file, replacing a symlink instead of writing through it.

    A user who symlinks an owned path into a dotfiles repository would otherwise have this
    script write outside the home entirely; the owned paths are KASPER's, so the link is
    removed and a real file takes its place (its content is in the backup already).

    Args:
        target: The destination inside the Claude home.
        content: The bytes to write.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        target.unlink()
    target.write_bytes(content)


def _back_up(home: Path, backup_dir: Path, rel: str) -> None:
    """Copy one existing home file into the backup directory, byte for byte (§6).

    Args:
        home: The Claude home.
        backup_dir: The timestamped backup directory; created on demand.
        rel: The file's path relative to ``home``, mirrored inside the backup.
    """
    destination = backup_dir / rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes((home / rel).read_bytes())


def _has_kasper_hooks(settings_path: Path) -> bool:
    """Whether a settings file still carries KASPER's hook wiring (§9)."""
    if not settings_path.is_file():
        return False
    hooks = _load_settings(settings_path).get("hooks", {})
    return any(
        HOOK_MARKER in handler.get("command", "")
        for groups in hooks.values()
        for group in groups
        for handler in group["hooks"]
    )


def _manifest_files(home: Path) -> dict[str, str]:
    """The path -> SHA-256 map the home's manifest records, every key checked.

    A manifest key is a path this script will hash, and may delete. An absolute key, a
    ``..`` segment, or anything else that lands outside the home would turn a hand-edited
    or tampered manifest into a delete-anywhere primitive, so such a manifest is refused
    outright rather than partly obeyed.

    Containment is judged on the key itself — joined and normalised lexically, never
    ``resolve()``d. Following symlinks here would reject the ordinary case of a user who
    links an owned path into a dotfiles repository, which is the user's own arrangement and
    :func:`_write_file`'s business, not a tampered manifest.

    The limit of that choice, stated so no reader over-trusts it: the check proves the *key*
    stays inside the home, not the file. If the user has symlinked a directory component —
    ``~/.claude/sounds`` pointing elsewhere — a delete still lands outside, exactly as the
    matching install would have written through the same link. What it does close is the
    tampered-manifest escape, which is what a key can express on its own.

    Args:
        home: The Claude home whose manifest is read, and which every key must stay inside.

    Returns:
        The recorded ``files`` map.

    Raises:
        ValueError: When the file is not a KASPER manifest, or records a path outside the
            home or a hash that is not a string.
        OSError: When the manifest cannot be read.
    """
    manifest_path = home / MANIFEST_NAME
    files = _load_json(manifest_path).get("files")
    if not isinstance(files, dict):
        raise ValueError(f"{manifest_path} has no files map — it is not a KASPER manifest")
    root = Path(os.path.normpath(home))
    for rel, digest in files.items():
        _check_shape(
            isinstance(digest, str), manifest_path, f"the hash of {rel!r} must be a string"
        )
        target = Path(os.path.normpath(home / rel))
        if os.path.isabs(rel) or ".." in Path(rel).parts or not target.is_relative_to(root):
            raise _ManifestEscape(f"{manifest_path} records {rel!r}, which is outside {home}")
    recorded: dict[str, str] = files
    return recorded


def _previous_manifest_files(home: Path) -> dict[str, str]:
    """What the last install recorded, tolerating a manifest this run cannot read.

    An install is the repair path: a home left half-written, or carrying a foreign or
    truncated manifest, must be fixable by running the installer again. So an unreadable
    manifest costs only the stale-file pruning — announced on stderr, not swallowed — while
    ``--status`` and ``--uninstall``, which repair nothing, still refuse it outright.

    A manifest that parses but names a path outside the home stays fatal here too: pruning
    deletes by key, so that key is precisely the danger.

    Args:
        home: The Claude home being installed into.

    Returns:
        The recorded ``files`` map, or an empty map when there is no readable manifest.

    Raises:
        ValueError: When the manifest records a path outside the home.
        OSError: When the manifest exists but cannot be read.
    """
    if not (home / MANIFEST_NAME).is_file():
        return {}
    try:
        return _manifest_files(home)
    except _ManifestEscape:
        raise
    except ValueError as exc:
        print(f"install.py: ignoring an unreadable manifest ({exc})", file=sys.stderr)
        return {}


def _current_hashes(home: Path, rels: Iterable[str]) -> dict[str, str | None]:
    """Hash each recorded path as it stands now, None where the file is gone."""
    return {
        rel: sha256_hex((home / rel).read_bytes()) if (home / rel).is_file() else None
        for rel in rels
    }


def _prune_empty_dirs(root: Path) -> None:
    """Remove every empty directory under an owned root, the root included (§10).

    A directory holding anything the manifest did not record — a user's own file, a
    ``__pycache__`` — is not empty, so it stays. A symlink is never pruned, even when it
    points at an empty directory: ``is_dir()`` follows it but ``rmdir`` does not, so acting
    on one would raise ``NotADirectoryError`` half way through a removal, and the link is
    the user's arrangement rather than something this script created.

    Args:
        root: An owned path in the Claude home; ignored unless it is a real directory.
    """
    if root.is_symlink() or not root.is_dir():
        return
    # os.walk does not follow symlinked directories, so nothing outside the home is walked;
    # bottom-up means a parent is judged after the children that might have emptied it.
    for current, _subdirs, _files in os.walk(root, topdown=False):
        path = Path(current)
        if not any(path.iterdir()):
            path.rmdir()


def _git_commit(source_root: Path) -> str | None:
    """The source checkout's HEAD commit, or None when it cannot be determined (§7).

    A tarball download, a git-less machine, or a source that is not a repository are all
    ordinary — the manifest records null and the install proceeds.

    Args:
        source_root: The repository root being installed from.

    Returns:
        The full commit hash, or None.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def owned_files(source_claude: Path) -> dict[str, bytes]:
    """Read every owned file out of a repository's ``.claude/`` directory (§3).

    Args:
        source_claude: The repository's ``.claude/`` directory.

    Returns:
        POSIX relative path -> the file's bytes, ``__pycache__`` excluded. The merged files
        — ``settings.json`` and the ledger — are not owned and never appear.

    Raises:
        FileNotFoundError: When an owned path is missing from the source checkout.
    """
    files: dict[str, bytes] = {}
    for name in OWNED_PATHS:
        path = source_claude / name
        if path.is_file():
            files[name] = path.read_bytes()
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and _PYCACHE not in child.parts:
                    files[child.relative_to(source_claude).as_posix()] = child.read_bytes()
        else:
            raise FileNotFoundError(f"{path} is missing — is this a complete KASPER checkout?")
    return files


# ------------------------------------------------------------------ the three modes


def install(
    source_root: Path,
    home: Path,
    python: str,
    dry_run: bool = False,
    now: datetime | None = None,
    out: TextIO | None = None,
) -> int:
    """Install (or update) KASPER in a Claude home, reporting every file (§§3-8).

    Owned files are copied verbatim, ``settings.json`` and the ledger are merged, anything
    that would change is copied into ``kasper-backup-<UTC>/`` first, and the manifest
    records what was written. A file the previous manifest recorded that the source no
    longer ships is backed up and removed, so a rename or a deletion upstream does not leave
    a stale copy behind that Claude Code would still read. A run that changes nothing
    creates no backup directory.

    An install owns the owned set outright: a stale file is removed even when it was edited
    locally, because the backup is the safety net and a half-owned tree is not a state this
    script keeps. ``--uninstall`` is deliberately the opposite — it keeps what it did not
    write (§10) — since there is no backup-and-continue there, only removal.

    All parsing and merging finishes before the first byte is written, so a corrupt or
    malformed home file costs nothing. The owned files themselves are then written one at a
    time, not atomically as a set: a failure part way through leaves a partially updated
    home still described by the *previous* manifest, with every replaced file in the backup
    directory — re-running the install repairs it, which is also why an unreadable manifest
    is a warning here rather than the error it is in the two read-only modes.

    A symlinked owned path is replaced by a real file (:func:`_write_file`), and so is a
    symlinked ``settings.json`` or ledger, since the atomic replace targets the link itself;
    nothing outside the home is ever written through.

    Args:
        source_root: The repository root to install from.
        home: The Claude home to install into; created when absent.
        python: The interpreter rendered into the hook commands.
        dry_run: When True, print the plan and write nothing at all.
        now: The instant that names the backup directory and dates the manifest; defaults
            to the current UTC time.
        out: Where the report is printed; ``None`` means this process's ``sys.stdout``,
            resolved now rather than at import so a redirected stream is honoured.

    Returns:
        0 — an install either completes or raises.

    Raises:
        ValueError: When a JSON file involved is corrupt, malformed, or the manifest
            records a path outside the home.
        OSError: When the source is incomplete or the home cannot be written.
    """
    stream = sys.stdout if out is None else out
    source_claude = source_root / ".claude"
    source = owned_files(source_claude)
    existing = {rel: (home / rel).read_bytes() for rel in source if (home / rel).is_file()}
    plan = plan_files(source, existing)

    previous = _previous_manifest_files(home)
    stale = sorted(rel for rel in previous if rel not in source and (home / rel).is_file())

    settings_path = home / SETTINGS_NAME
    home_settings = _load_settings(settings_path) if settings_path.is_file() else {}
    merged_settings = merge_settings(
        home_settings, _load_settings(source_claude / SETTINGS_NAME), python
    )
    ledger_path = home / LEDGER_NAME
    home_ledger = _load_ledger(ledger_path) if ledger_path.is_file() else None
    merged_ledger = merge_ledger(home_ledger, _load_ledger(source_claude / LEDGER_NAME))

    settings_kind = "merge" if merged_settings != home_settings else "unchanged"
    ledger_kind = "merge" if merged_ledger != home_ledger else "unchanged"

    backup_rels = [rel for rel, kind in plan.items() if kind == "overwrite"] + stale
    if settings_kind == "merge" and settings_path.is_file():
        backup_rels.append(SETTINGS_NAME)
    if ledger_kind == "merge" and ledger_path.is_file():
        backup_rels.append(LEDGER_NAME)
    backup_rels.sort()

    for rel in sorted(plan):
        print(f"{plan[rel]}  {rel}", file=stream)
    for rel in stale:
        print(f"remove  {rel}", file=stream)
    print(f"{settings_kind}  {SETTINGS_NAME}", file=stream)
    print(f"{ledger_kind}  {LEDGER_NAME}", file=stream)
    for rel in backup_rels:
        print(f"backup  {rel}", file=stream)

    if dry_run:
        print(f"dry run  nothing written to {home}", file=stream)
        return 0

    moment = datetime.now(UTC) if now is None else now
    home.mkdir(parents=True, exist_ok=True)
    if backup_rels:
        backup_dir = _backup_dir(home, moment)
        for rel in backup_rels:
            _back_up(home, backup_dir, rel)
        print(f"backup dir  {backup_dir.name}", file=stream)

    for rel, kind in plan.items():
        if kind != "unchanged":
            _write_file(home / rel, source[rel])
    for rel in stale:
        (home / rel).unlink()
    if stale:
        for name in OWNED_PATHS:
            _prune_empty_dirs(home / name)
    if settings_kind == "merge":
        _write_json(settings_path, merged_settings)
    if ledger_kind == "merge":
        _write_json(ledger_path, merged_ledger)

    manifest: Manifest = {
        "schema": _MANIFEST_SCHEMA,
        "source": str(source_root),
        "source_commit": _git_commit(source_root),
        "installed_at": moment.isoformat(),
        "python": python,
        "files": {rel: sha256_hex(content) for rel, content in source.items()},
    }
    _write_json(home / MANIFEST_NAME, manifest)
    return 0


def status(home: Path, out: TextIO | None = None) -> int:
    """Report the installed files and the hook wiring against the manifest (§9).

    Unlike an install, this mode repairs nothing, so an unreadable manifest is an error
    rather than a warning — reporting a home it cannot read as clean would be a lie.

    Args:
        home: The Claude home to check.
        out: Where the report is printed; ``None`` means this process's ``sys.stdout``,
            resolved now rather than at import so a redirected stream is honoured.

    Returns:
        0 when every recorded file matches and ``settings.json`` still carries the hooks;
        1 on any drift, and when there is no manifest at all.

    Raises:
        ValueError: When the manifest or ``settings.json`` is corrupt, malformed, or the
            manifest records a path outside the home.
    """
    stream = sys.stdout if out is None else out
    if not (home / MANIFEST_NAME).is_file():
        print(f"not installed  no {MANIFEST_NAME} under {home}", file=stream)
        return 1
    recorded = _manifest_files(home)
    report = status_report(recorded, _current_hashes(home, recorded))
    for rel in sorted(report):
        print(f"{report[rel]}  {rel}", file=stream)
    # Hooks gone from settings means KASPER is installed but not in force: drift, not clean.
    installed_hooks = _has_kasper_hooks(home / SETTINGS_NAME)
    print(f"{'ok' if installed_hooks else 'missing'}  hooks in {SETTINGS_NAME}", file=stream)
    return 0 if installed_hooks and all(kind == "ok" for kind in report.values()) else 1


def uninstall(home: Path, now: datetime | None = None, out: TextIO | None = None) -> int:
    """Remove what the manifest records, keeping anything edited since (§10).

    The permissions block and the ledger stay behind by design — the ledger holds the
    user's own per-machine grants — and the report says so. Everything here is conservative:
    a file edited since the install is kept, and a manifest that cannot be read stops the
    run rather than guessing what it may delete.

    Args:
        home: The Claude home to clean.
        now: The instant that names the settings backup directory; defaults to now, UTC.
        out: Where the report is printed; ``None`` means this process's ``sys.stdout``,
            resolved now rather than at import so a redirected stream is honoured.

    Returns:
        0 when everything recorded was removed; 1 when a modified file was kept, and when
        there is no manifest at all.

    Raises:
        ValueError: When the manifest or ``settings.json`` is corrupt, malformed, or the
            manifest records a path outside the home — nothing is deleted in that case.
    """
    stream = sys.stdout if out is None else out
    manifest_path = home / MANIFEST_NAME
    if not manifest_path.is_file():
        print(f"not installed  no {MANIFEST_NAME} under {home}", file=stream)
        return 1
    recorded = _manifest_files(home)
    plan = uninstall_plan(recorded, _current_hashes(home, recorded))

    settings_path = home / SETTINGS_NAME
    if settings_path.is_file():
        settings = _load_settings(settings_path)
        stripped = strip_kasper_hooks(settings.get("hooks", {}))
        if stripped != settings.get("hooks", {}):
            moment = datetime.now(UTC) if now is None else now
            backup_dir = _backup_dir(home, moment)
            _back_up(home, backup_dir, SETTINGS_NAME)
            print(f"backup  {SETTINGS_NAME}", file=stream)
            print(f"backup dir  {backup_dir.name}", file=stream)
            settings["hooks"] = stripped
            _write_json(settings_path, settings)

    for rel in sorted(plan):
        if plan[rel] == "delete":
            (home / rel).unlink()
        print(f"{plan[rel]}  {rel}", file=stream)
    for name in OWNED_PATHS:
        _prune_empty_dirs(home / name)
    manifest_path.unlink()

    print(
        f"keep  {LEDGER_NAME}  your own grants stay; delete it by hand to be rid of it", file=stream
    )
    print(
        f"keep  {SETTINGS_NAME}  KASPER's hooks are gone; the permissions block stays", file=stream
    )
    return 1 if any(kind == "keep" for kind in plan.values()) else 0


def main(argv: list[str] | None = None) -> int:
    """Run the installer from the command line (§2).

    Args:
        argv: The argument vector (``None`` → ``sys.argv[1:]``).

    Returns:
        0 clean, 1 on drift or something left behind or a file that could not be handled.
        A usage error exits 2 through argparse.
    """
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Install KASPER's configuration into a Claude home directory.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    mode.add_argument("--status", action="store_true", help="report drift against the manifest")
    mode.add_argument("--uninstall", action="store_true", help="remove what the manifest records")
    parser.add_argument(
        "--home",
        type=Path,
        default=Path.home() / ".claude",
        help="the Claude home to act on (default: ~/.claude)",
    )
    parser.add_argument(
        "--python",
        default=default_python(sys.platform),
        help="interpreter rendered into the hook commands (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    home: Path = args.home
    try:
        if args.status:
            return status(home)
        if args.uninstall:
            return uninstall(home)
        return install(_SOURCE_ROOT, home, args.python, dry_run=args.dry_run)
    except (OSError, ValueError) as exc:
        print(f"install.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
