#!/usr/bin/env python3
"""PostToolUse Bash permission recorder — a granted prompt becomes a standing ledger grant.

Runs after every Bash call. If the command would NOT have auto-allowed through the ledger (see
``bash_permission_gate.py``), the only way it ran is that the user approved the prompt — so the
unallowed parts are promoted into the ledger's ``grants`` list and the same command never prompts
twice. Guardrails:

  * Commands matching an ask-pattern/denial are NEVER promoted — that approval was one-time.
  * Destructive / outward-facing / arbitrary-execution heads (``rm``, ``curl``, ``bash``, ``ssh``,
    ``open``, ...) are never promoted; nor is any ``git push`` form beyond the feature-branch
    pattern already in the ledger. Promote those deliberately with ``/permit`` if ever wanted.
  * Nothing records in ``bypassPermissions`` mode (no prompt happened, so nothing was granted).

Grant keys are token prefixes sized per tool (``aws`` -> 3 tokens, ``git``/``gh``/``docker`` -> 2,
default 1) so a grant covers the operation, not the whole tool. Denials cannot be observed by any
hook — record those by hand (or via /permit --deny) per the boundaries rule.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from bash_permission_gate import (  # noqa: E402
    _BUILTINS,
    _ENV_ASSIGN,
    _WRAPPERS,
    LEDGER_PATH,
    _strip_tokens,
    ask_match,
    ledger_allow_keys,
    load_ledger,
    unallowed_parts,
)

# Heads that must never become standing grants — each use stays a one-time human approval.
NEVER_PROMOTE: frozenset[str] = frozenset(
    {
        "rm",
        "sudo",
        "curl",
        "wget",
        "dd",
        "mkfs",
        "shred",
        "fdisk",
        "kill",
        "pkill",
        "killall",
        "launchctl",
        "osascript",
        # Windows equivalents of the shells and system tools above: on Windows these are
        # the ways an approved command becomes an arbitrary one, so the ledger must never
        # widen to them either (ADR-0023).
        "powershell",
        "pwsh",
        "cmd",
        "reg",
        "taskkill",
        "wmic",
        "certutil",
        "ssh",
        "scp",
        "rsync",
        "nc",
        "open",
        "defaults",
        "security",
        "diskutil",
        "systemsetup",
        "networksetup",
        "bash",
        "sh",
        "zsh",
        "eval",
        "exec",
        "source",
        ".",
    }
)

# How many tokens of the command a grant key captures: enough to scope the operation, not the tool.
KEY_DEPTH: dict[str, int] = {
    "aws": 3,
    "gh": 3,
    "git": 2,
    "docker": 2,
    "terraform": 2,
    "kubectl": 2,
    "brew": 2,
    "npm": 2,
    "npx": 2,
    "pnpm": 2,
    "yarn": 2,
    "pip": 2,
    "pip3": 2,
    "cargo": 2,
    "go": 2,
    "poetry": 2,
    "uv": 2,
}


# A promotable key token must look like a real command word (head or subcommand) — never a path,
# URL, code fragment, or data token that leaked out of a mis-parsed command.
_KEY_TOKEN: re.Pattern[str] = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


def _plausible_command(key: str) -> bool:
    """True when a candidate grant key names a real command — junk never becomes policy.

    The head token must look like a command name (no '=', no leading '-', no path fragment
    with spaces) AND resolve on PATH or as an executable file. A mis-split token ("Shared",
    "unit", 'b"') fails here and stays a one-time prompt instead of a standing grant.
    """
    head = key.split()[0] if key.split() else ""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9._+-]*", head):
        return False
    return shutil.which(head) is not None or os.access(head, os.X_OK)


def _grant_key(part: str) -> str | None:
    """Derive the ledger grant key for an unallowed command part, or None if not promotable."""
    tokens = _strip_tokens(part.strip().split())
    effective = [t for t in tokens if not t.startswith("-")]
    while effective and effective[0] in _WRAPPERS:
        wrapper = effective.pop(0)  # promote the wrapped command, not the wrapper
        if wrapper == "timeout" and effective:
            effective.pop(0)  # skip the duration argument
        while effective and _ENV_ASSIGN.match(effective[0]):
            effective.pop(0)
    if not effective:
        return None
    head = effective[0]
    if head in NEVER_PROMOTE or head in _BUILTINS:
        return None
    if len(head) < 2 or not _KEY_TOKEN.fullmatch(head):
        return None
    key_tokens = effective[: KEY_DEPTH.get(head, 1)]
    if not all(_KEY_TOKEN.fullmatch(t) for t in key_tokens):
        return None  # an argument (path/URL/data) landed in the key — not a clean op prefix
    key = " ".join(key_tokens)
    if key.startswith("git push"):
        return None  # only the feature-branch pattern auto-allows pushes
    return key


def main() -> None:
    """Promote the just-approved command's unallowed parts into standing ledger grants."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash" or data.get("permission_mode") == "bypassPermissions":
        return
    command = (data.get("tool_input") or {}).get("command", "")
    if not command.strip():
        return

    ledger = load_ledger()
    if not ledger or ask_match(command, ledger) is not None:
        return  # unreadable ledger, or a guarded op: the approval stays one-time
    pending = unallowed_parts(command, ledger)
    if not pending:
        return  # was auto-allowed — nothing was granted, nothing to learn

    existing = set(ledger_allow_keys(ledger))
    grants: list[dict[str, Any]] = ledger.setdefault("grants", [])
    today = datetime.date.today().isoformat()
    added = False
    for part in pending:
        key = _grant_key(part)
        if key is None or key in existing or not _plausible_command(key):
            continue
        grants.append(
            {
                "key": key,
                "note": "auto-recorded: user approved this command",
                "example": command.splitlines()[0][:160],
                "added": today,
            }
        )
        existing.add(key)
        added = True

    if added:
        tmp = LEDGER_PATH.with_suffix(".json.tmp")
        # ensure_ascii=False keeps the ledger's prose as authored: the default would
        # re-escape every em dash in _doc/note on each recorded grant, rewriting the whole
        # file. install.py's writer agrees.
        text = json.dumps(ledger, indent=2, ensure_ascii=False) + "\n"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, LEDGER_PATH)


if __name__ == "__main__":
    main()
