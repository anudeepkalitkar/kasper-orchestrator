#!/usr/bin/env python3
"""Stop hook — keep an overnight session walking the backlog until the night is over.

Wired as the second ``Stop`` handler (``run_hook.py overnight``). It reads the hook payload
(``cwd``, ``session_id``, ``transcript_path``) from stdin and
``tasks/BACKLOG.md``'s header, keeps a continuation
counter and an ending marker in this session's scratch dir
(``claude-temp/sessions/<id>/overnight.count`` and ``overnight.ended``; when that marker is
refused, ``claude-temp/overnight-<id>.ended`` instead), and decides by the order below
(kasper-v2 §2). Only a stat of this session's own ending markers precedes the ownership check
(its sole outcome is a silent pass); the backlog's ownership is settled before the counter is
read, so a daytime or unrelated session is never blocked, instructed, or touched.

Every on-disk lookup that gates a block — both markers, the counter, the transcript, the
backlog header — reads "absent" only from ``FileNotFoundError``; any other ``OSError`` makes
the answer unknown, and unknown never re-arms a block. An unknown marker passes silently (a
block may already have gone out); an unknown counter ends the night through the marker rules,
and an unknown transcript count leaves the marker rules to decide — both stay bounded because
a block needs a persisted marker. (``Path.exists``/``is_file`` are not used for this: they
report a failed stat as absent.)

1. **Not ours** — ``session_id`` is invalid, either ending marker exists or cannot be checked
   (both checked before ownership), or the backlog is absent, unreadable, not
   ``overnight: on``, or names another session: nothing. This is the whole behaviour by day,
   and no counter is read.
2. **Terminal** — the counter already holds :data:`MAX_CONTINUATIONS` or more (the marker was
   lost): this is an ending condition too, handled as in 3.
3. **Ending** — no open ``- [ ]`` item, ``until`` passed, or this continuation reaches
   :data:`MAX_CONTINUATIONS`: write the marker and block with the "end the night" instruction,
   so the cap's last continuation is the one that writes the morning report. If the marker
   write is refused by permissions (``PermissionError``) while the backlog file and its
   directory permit writes, the fallback marker is written instead and, only once that write
   has landed, the block is sent. A block is therefore only ever sent after a marker
   persisted, and the next Stop finds it: the number of ending blocks is bounded without any
   write having to succeed. Silent exits: the transcript already holds
   :data:`MAX_ENDING_BLOCKS` ending blocks (a write-free ceiling, which holds only while the
   transcript itself persists); the marker write fails with any other ``OSError`` (a full
   disk, quota, read-only or I/O error — the report and ``overnight: off`` would fail too);
   the marker is refused and the backlog is not writable; or the fallback marker fails too.
4. **Continue** — block with the next-item instruction.

Every reason starts with a tool call, so the harness's own cap on consecutive blocks without
a tool call also ends a night that does no work.

State lives under the stable project root (``project_dirs.project_root``) — never the payload's
``cwd``, which follows the session into a package or worktree and would lose the backlog. Once
the session owns the night, any I/O error on the counter ends the night with the
morning-report instruction, except the silent exits above; nothing propagates out of the hook.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path
from typing import TypedDict

# A hook runs via ``runpy``, which does not put its own directory on the path.
sys.path.insert(0, str(Path(__file__).parent))
from project_dirs import (  # noqa: E402
    _HEADER_LINE,
    BACKLOG_PATH,
    SCRATCH_DIR,
    SESSIONS_DIR,
    _session_id,
    project_root,
)

#: Continuations one overnight session may take; the last one is the ending block.
MAX_CONTINUATIONS: int = 60

#: Ending blocks one session may be sent, counted in its transcript (needs no write to hold).
MAX_ENDING_BLOCKS: int = 2

#: Opens every ending reason. Its occurrences in the transcript are an upper bound on the ending
#: blocks sent, not an exact count: a session that reads this source inflates them, which errs
#: toward silence, never toward repetition.
ENDING_SENTINEL: str = "[overnight-ending]"

#: Bytes read per chunk while scanning the whole transcript for the sentinel.
_TRANSCRIPT_CHUNK_BYTES: int = 1024 * 1024

COUNT_NAME: str = "overnight.count"
ENDED_NAME: str = "overnight.ended"
#: The fallback ending marker, directly under ``claude-temp/`` — still the hook's own scratch
#: area (a hook never writes ``tasks/``), but outside the session dir that refused the marker.
FALLBACK_ENDED_NAME: str = "overnight-{session_id}.ended"

_OPEN_ITEM: str = "- [ ] "


class Backlog(TypedDict):
    """What the hook reads from ``tasks/BACKLOG.md``."""

    on: bool
    session: str | None
    until: str | None
    open_items: int


def parse_backlog(text: str) -> Backlog:
    """Read the backlog's header and count its open items.

    Only the first ``overnight:``, ``session:`` and ``until:`` lines count; everything that is
    neither a header line nor an item is free text and ignored.

    Args:
        text: The backlog file's contents.

    Returns:
        The parsed header and the number of ``- [ ]`` lines.
    """
    header: dict[str, str] = {}
    open_items = 0
    for line in text.splitlines():
        match = _HEADER_LINE.match(line)
        if match:
            header.setdefault(match.group(1), match.group(2))
        elif line.startswith(_OPEN_ITEM):
            open_items += 1
    return {
        "on": header.get("overnight") == "on",
        "session": header.get("session"),
        "until": header.get("until"),
        "open_items": open_items,
    }


def until_passed(until: str | None, now: datetime.datetime) -> bool:
    """Whether the human's optional stop time has passed.

    An unreadable ``until`` counts as passed: unattended work stops when the human's limit
    cannot be read, rather than running to the cap.

    Args:
        until: The header's ISO 8601 local time, or None when absent.
        now: The current local time (naive).

    Returns:
        True when ``until`` is set and is not after ``now``.
    """
    if until is None:
        return False
    try:
        deadline = datetime.datetime.fromisoformat(until)
    except ValueError:
        print(f"overnight: unreadable until {until!r}; ending the night.", file=sys.stderr)
        return True
    if deadline.tzinfo is not None:  # an explicit offset is honoured, not dropped
        return deadline <= now.astimezone()
    return deadline <= now


def _read_count(path: Path) -> int | None:
    """The continuations taken so far: 0 when absent, None when the counter is corrupt.

    Only ``FileNotFoundError`` means absent: a failed lookup must never reset the count (it
    propagates, and the caller ends the night through the marker rules).

    Raises:
        OSError: When the counter cannot be read for any reason but being absent.
    """
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        return 0
    except ValueError:  # non-numeric or undecodable (UnicodeDecodeError is a ValueError)
        print(f"overnight: unreadable counter {path}; ending the night.", file=sys.stderr)
        return None


def _block(reason: str) -> str:
    """The Stop hook's block decision as one JSON line."""
    return json.dumps({"decision": "block", "reason": reason})


def _backlog_writable(backlog_path: Path) -> bool:
    """Whether the backlog file and its directory permit writes (to end and report the night).

    A permission prediction only, never proof the disk takes writes: it guards the fallback
    marker attempt, whose own success is what licenses the block.
    """
    return os.access(backlog_path, os.W_OK) and os.access(backlog_path.parent, os.W_OK)


def _fallback_ended(ended: Path) -> Path:
    """The fallback for a session's ending marker, directly under ``claude-temp/``.

    Args:
        ended: The session's marker, ``claude-temp/sessions/<id>/overnight.ended``.

    Returns:
        ``claude-temp/overnight-<id>.ended``.
    """
    session_dir = ended.parent
    return session_dir.parent.parent / FALLBACK_ENDED_NAME.format(session_id=session_dir.name)


def _ending_blocks_sent(transcript: Path | None) -> int | None:
    """Count the ending blocks already sent, scanning the whole session transcript (read-only).

    The transcript is streamed in :data:`_TRANSCRIPT_CHUNK_BYTES` chunks, carrying the last
    ``len(ENDING_SENTINEL) - 1`` bytes across each boundary so a sentinel split between chunks
    still counts once; the scan stops as soon as :data:`MAX_ENDING_BLOCKS` is reached.

    Args:
        transcript: The payload's ``transcript_path``, or None when absent.

    Returns:
        The number of :data:`ENDING_SENTINEL` occurrences seen before the scan stopped (at
        least :data:`MAX_ENDING_BLOCKS` once that is reached), or None when there is no
        transcript or it cannot be read (the count is unknown; the marker rules decide).
    """
    if transcript is None:
        return None
    sentinel = ENDING_SENTINEL.encode("utf-8")
    keep = len(sentinel) - 1  # too short to hold a whole sentinel, so never counted twice
    count = 0
    carry = b""
    try:
        with transcript.open("rb") as handle:
            while count < MAX_ENDING_BLOCKS and (chunk := handle.read(_TRANSCRIPT_CHUNK_BYTES)):
                window = carry + chunk
                count += window.count(sentinel)
                carry = window[-keep:]
    except OSError as exc:
        print(f"overnight: cannot read transcript ({exc}).", file=sys.stderr)
        return None
    return count


def _end_night(
    ended: Path,
    backlog_path: Path,
    why: str,
    now: datetime.datetime,
    transcript: Path | None,
) -> str | None:
    """Write the ending marker and return the "end the night" block.

    First the write-free ceiling: when the transcript already holds
    :data:`MAX_ENDING_BLOCKS` ending blocks the hook passes silently, however the marker fares.
    An absent or unreadable transcript leaves the count unknown, and the marker rules decide
    exactly as for a count of 0: the block still needs a persisted marker.

    The block is returned only after a marker write has landed, so the next Stop finds that
    marker and passes silently: however every write fails, at most one block per persisted
    marker is ever sent. Any non-permission ``OSError`` on the marker (a full disk, quota,
    read-only or I/O error) passes silently — the report and the header write would fail too.
    When the marker is refused by permissions (``PermissionError``) and the backlog permits
    writes (``overnight: off`` there ends ownership), the fallback marker is written instead
    and its success, not the ``os.access`` prediction, licenses the block; a refused marker
    with an unwritable backlog, or a fallback that fails with any ``OSError``, passes silently.

    Args:
        ended: The session's ending marker (its fallback is derived by :func:`_fallback_ended`).
        backlog_path: The backlog file, where ``overnight: off`` is set (the report goes to
            ``tasks/morning-<date>.md``).
        why: The reason the night ends, quoted in the instruction.
        now: The current local time.
        transcript: The session transcript, for the ceiling; None or unreadable falls back to
            the marker rules.

    Returns:
        The block decision, or None when the ceiling is reached or the night cannot be ended.
    """
    sent = _ending_blocks_sent(transcript)
    if sent is not None and sent >= MAX_ENDING_BLOCKS:
        print(f"overnight: {sent} ending blocks already sent; passing silently.", file=sys.stderr)
        return None
    try:
        ended.parent.mkdir(parents=True, exist_ok=True)
        ended.write_text(now.isoformat(), encoding="utf-8")
    except PermissionError as exc:
        print(f"overnight: cannot write {ended} ({exc}).", file=sys.stderr)
        if not _backlog_writable(backlog_path):
            print("overnight: backlog not writable either; passing silently.", file=sys.stderr)
            return None
        try:
            _fallback_ended(ended).write_text(now.isoformat(), encoding="utf-8")
        except OSError as fallback_exc:
            print(
                f"overnight: cannot write fallback marker ({fallback_exc}); passing silently.",
                file=sys.stderr,
            )
            return None
    except OSError as exc:
        print(f"overnight: cannot write marker ({exc}); passing silently.", file=sys.stderr)
        return None
    return _block(
        f"{ENDING_SENTINEL} Overnight ended ({why}). Read tasks/BACKLOG.md, then end the night: "
        "append "
        f"tasks/morning-{now.date().isoformat()}.md and set `overnight: off`; then stop."
    )


def _owned_backlog(root: Path, session_id: str) -> Backlog | None:
    """Read the backlog when its header hands the night to this session.

    Args:
        root: The project root.
        session_id: The validated session id.

    Returns:
        The parsed backlog, or None when it is absent, unreadable, not ``overnight: on``, or
        names another session — every case where the night is not this session's to run.
    """
    backlog_path = root.joinpath(*BACKLOG_PATH)
    try:
        if not backlog_path.is_file():
            return None
        backlog = parse_backlog(backlog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:  # an undecodable backlog is unreadable too
        print(f"overnight: backlog unreadable ({exc}); not this session's night.", file=sys.stderr)
        return None
    return backlog if backlog["on"] and backlog["session"] == session_id else None


def _walk(
    scratch: Path,
    backlog_path: Path,
    backlog: Backlog,
    now: datetime.datetime,
    transcript: Path | None,
) -> str | None:
    """Apply the owner's rules — ending, continue — incrementing the counter, then testing it.

    Args:
        scratch: This session's scratch dir.
        backlog_path: The backlog file.
        backlog: The backlog, already confirmed to hand the night to this session.
        now: The current local time.
        transcript: The session transcript, for the ending-block ceiling.

    Returns:
        The JSON block decision to print, or None when the night cannot be ended.

    Raises:
        OSError: When the counter cannot be read (other than being absent) or written.
    """
    count_path = scratch / COUNT_NAME
    taken = _read_count(count_path)
    count = MAX_CONTINUATIONS if taken is None else taken + 1
    scratch.mkdir(parents=True, exist_ok=True)
    count_path.write_text(str(count), encoding="utf-8")

    ended = scratch / ENDED_NAME
    if backlog["open_items"] == 0:
        return _end_night(ended, backlog_path, "backlog empty", now, transcript)
    if until_passed(backlog["until"], now):
        return _end_night(ended, backlog_path, "until passed", now, transcript)
    if count >= MAX_CONTINUATIONS:
        return _end_night(ended, backlog_path, "cap reached", now, transcript)
    return _block(
        f"Overnight: {backlog['open_items']} open backlog item(s). Read tasks/BACKLOG.md, take "
        "the first open one, run it as a task; park it if you cannot finish; then stop."
    )


def decide(payload: dict[str, object], now: datetime.datetime) -> str | None:
    """Apply the four rules to one Stop event, writing the counter and marker as they require.

    Ownership comes first: only a session the backlog names reads its counter or gets an
    instruction. A marker that cannot be checked passes silently (a block may already have
    gone out); after ownership, any I/O error — an unreadable counter included — ends the night
    with the morning-report instruction instead of propagating (or passes silently when the
    night cannot be ended at all).

    Args:
        payload: The hook payload (``cwd``, ``session_id``, ``transcript_path``,
            ``stop_hook_active``).
        now: The current local time.

    Returns:
        The JSON block decision to print, or None to let the session stop.
    """
    session_id = _session_id(payload.get("session_id"))
    if session_id is None:
        return None
    root = project_root(payload.get("cwd"))
    scratch = root / SCRATCH_DIR / SESSIONS_DIR / session_id
    ended = scratch / ENDED_NAME
    for marker in (ended, _fallback_ended(ended)):
        # minimal: a stat of this session's own markers, whose only outcome is a silent pass —
        # so it may precede the ownership check without touching another session. Not
        # ``exists()``: it reports a failed stat as absent, which would re-arm the ending block.
        try:
            marker.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            print(f"overnight: cannot check {marker} ({exc}); passing silently.", file=sys.stderr)
            return None
        return None  # the marker exists: the night already ended
    backlog = _owned_backlog(root, session_id)
    if backlog is None:
        return None
    raw_transcript = payload.get("transcript_path")
    transcript = (
        Path(raw_transcript) if isinstance(raw_transcript, str) and raw_transcript else None
    )
    try:
        return _walk(scratch, root.joinpath(*BACKLOG_PATH), backlog, now, transcript)
    except OSError as exc:
        print(f"overnight: state unreadable ({exc}); ending the night.", file=sys.stderr)
        return _end_night(ended, root.joinpath(*BACKLOG_PATH), "state unreadable", now, transcript)


def main() -> None:
    """Read the Stop payload from stdin and print a block decision when the night goes on."""
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}
    payload: dict[str, object] = data if isinstance(data, dict) else {}
    decision = decide(payload, datetime.datetime.now())
    if decision is not None:
        print(decision)


if __name__ == "__main__":
    main()
