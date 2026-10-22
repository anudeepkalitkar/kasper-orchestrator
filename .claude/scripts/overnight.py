#!/usr/bin/env python3
"""Stop hook — keep an overnight session walking the backlog until the night is over.

Wired as the second ``Stop`` handler (``run_hook.py overnight``). It reads the hook payload
(``cwd``, ``session_id``) from stdin and ``tasks/BACKLOG.md``'s header, keeps a continuation
counter and an ending marker in this session's scratch dir
(``claude-temp/sessions/<id>/overnight.count`` and ``overnight.ended``), and decides by the
first rule that applies (kasper-v2 §2):

1. **Terminal** — the ending marker exists, or ``session_id`` is invalid: nothing.
2. **Off** — no backlog, header not ``overnight: on``, or ``session:`` names another session:
   nothing. This is the whole behaviour by day.
3. **Ending** — no open ``- [ ]`` item, ``until`` passed, or this continuation reaches
   :data:`MAX_CONTINUATIONS`: block once with the "end the night" instruction and write the
   marker, so the cap's last continuation is the one that writes the morning report.
4. **Continue** — block with the next-item instruction.

Every reason starts with a tool call, so the harness's own cap on consecutive blocks without
a tool call ends a night that does no work. ``stop_hook_active`` is logged, not consulted.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path
from typing import TypedDict

#: Continuations one overnight session may take; the last one is the ending block.
MAX_CONTINUATIONS: int = 60

BACKLOG_PATH: tuple[str, str] = ("tasks", "BACKLOG.md")
SCRATCH_DIR: str = "claude-temp"
SESSIONS_DIR: str = "sessions"
COUNT_NAME: str = "overnight.count"
ENDED_NAME: str = "overnight.ended"

_HEADER_LINE: re.Pattern[str] = re.compile(r"^(overnight|session|until):\s*(\S+)")
_OPEN_ITEM: str = "- [ ] "


class Backlog(TypedDict):
    """What the hook reads from ``tasks/BACKLOG.md``."""

    on: bool
    session: str | None
    until: str | None
    open_items: int


def _session_id(value: object) -> str | None:
    """Validate the payload's ``session_id`` as a directory name.

    Copied from ``project_dirs.py`` — a hook runs via ``runpy`` without its siblings on the
    path. Dot-only names and anything with a separator or drive are refused.

    Args:
        value: The raw ``session_id`` from the hook payload.

    Returns:
        The id when it names exactly one directory, else None.
    """
    if not isinstance(value, str) or not value or set(value) == {"."}:
        return None
    return value if value == Path(value).name else None


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


def _read_count(path: Path) -> int:
    """The continuations taken so far; an unreadable counter counts as the cap reached."""
    if not path.is_file():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        print(f"overnight: unreadable counter {path}; ending the night.", file=sys.stderr)
        return MAX_CONTINUATIONS


def _block(reason: str) -> str:
    """The Stop hook's block decision as one JSON line."""
    return json.dumps({"decision": "block", "reason": reason})


def decide(payload: dict[str, object], now: datetime.datetime) -> str | None:
    """Apply the four rules to one Stop event, writing the counter and marker as they require.

    Args:
        payload: The hook payload (``cwd``, ``session_id``, ``stop_hook_active``).
        now: The current local time.

    Returns:
        The JSON block decision to print, or None to let the session stop.

    Raises:
        OSError: When the backlog, counter or marker cannot be read or written.
    """
    session_id = _session_id(payload.get("session_id"))
    if session_id is None:
        return None
    root = Path(str(payload.get("cwd") or Path.cwd()))
    scratch = root / SCRATCH_DIR / SESSIONS_DIR / session_id
    ended = scratch / ENDED_NAME
    if ended.exists():
        return None
    backlog_path = root.joinpath(*BACKLOG_PATH)
    if not backlog_path.is_file():
        return None
    backlog = parse_backlog(backlog_path.read_text(encoding="utf-8"))
    if not backlog["on"] or backlog["session"] != session_id:
        return None

    print(f"overnight: stop_hook_active={payload.get('stop_hook_active')!r}", file=sys.stderr)
    count_path = scratch / COUNT_NAME
    count = _read_count(count_path) + 1
    scratch.mkdir(parents=True, exist_ok=True)
    count_path.write_text(str(count), encoding="utf-8")

    why = None
    if backlog["open_items"] == 0:
        why = "backlog empty"
    elif until_passed(backlog["until"], now):
        why = "until passed"
    elif count >= MAX_CONTINUATIONS:
        why = "cap reached"
    if why is not None:
        ended.write_text(now.isoformat(), encoding="utf-8")
        return _block(
            f"Overnight ended ({why}). Read tasks/BACKLOG.md, then end the night: append "
            f"tasks/morning-{now.date().isoformat()}.md and set `overnight: off`; then stop."
        )
    return _block(
        f"Overnight: {backlog['open_items']} open backlog item(s). Read tasks/BACKLOG.md, take "
        "the first open one, run it as a task; park it if you cannot finish; then stop."
    )


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
