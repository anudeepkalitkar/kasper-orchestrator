#!/usr/bin/env python3
"""PostToolUse Bash permission recorder — a granted prompt becomes a standing ledger grant.

Runs after every Bash call. If the command would NOT have auto-allowed through the ledger (see
``bash_permission_gate.py``), the only way it ran is that the user approved the prompt — so the
missing keys are promoted into the ledger's ``grants`` list and the same command never prompts
twice. Guardrails (ADR-0015):

  * Only an **inert form** lacking a key is promotable: the gate's ``unkeyed_inert_forms`` returns
    the forms whose missing key was the only thing between the command and allow, and nothing
    else — an ask verdict, a project form (``pytest``, ``git commit``), an unknown tool, a writer,
    a non-literal part, an assignment word — ever becomes a grant. One such part blocks the whole
    promotion. The recorder therefore cannot write a key outside the gate's form table.
  * Destructive / outward-facing / arbitrary-execution heads (``rm``, ``curl``, ``bash``, ``ssh``,
    ``open``, ...) are never promoted; nor is any ``git push`` form beyond the feature-branch
    pattern already in the ledger. Promote those deliberately with ``/permit`` if ever wanted.
  * Grants record only in the modes where the human is actually prompted (``default``,
    ``acceptEdits``, ``plan``). Any other mode — ``bypassPermissions``, auto mode (whose
    approvals come from a safety classifier, not the human), or a missing/unknown mode —
    records nothing: fail closed.

Grant keys are the form's words to ``KEY_DEPTH`` (``aws`` -> 3 tokens, default 1) so a grant
covers the operation, not the whole tool. Denials cannot be observed by any hook — record those
by hand (or via /permit --deny) per the boundaries rule.
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
    LEDGER_PATH,
    Form,
    ledger_allow_keys,
    load_ledger,
    unkeyed_inert_forms,
)

# Permission modes in which a non-allowed Bash call reaches the human as a prompt — the only
# modes in which "it ran" means "the human approved it".
PROMPTED_MODES: frozenset[str] = frozenset({"default", "acceptEdits", "plan"})

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

# How many words of the form a grant key captures: enough to scope the operation, not the tool.
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


def _grant_key(form: Form) -> str | None:
    """Derive the ledger grant key for an unkeyed inert form, or None if not promotable.

    The key is the form's words to ``KEY_DEPTH`` — never a wrapper, never a token that is not a
    clean command word, never a head in ``NEVER_PROMOTE`` or any ``git push`` form.
    """
    if form.kind != "inert":
        return None
    head = form.words[0]
    if head in NEVER_PROMOTE or len(head) < 2 or not _KEY_TOKEN.fullmatch(head):
        return None
    key_tokens = form.words[: KEY_DEPTH.get(head, 1)]
    if not all(_KEY_TOKEN.fullmatch(t) for t in key_tokens):
        return None  # an argument-shaped word landed in the key — not a clean op prefix
    key = " ".join(key_tokens)
    if key.startswith("git push"):
        return None  # only the feature-branch pattern auto-allows pushes
    return key


def main() -> None:
    """Promote the just-approved command's unkeyed inert forms into standing ledger grants."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash" or data.get("permission_mode") not in PROMPTED_MODES:
        return  # not Bash, or no human prompt happened in this mode: nothing was granted
    command = (data.get("tool_input") or {}).get("command", "")
    if not command.strip():
        return

    ledger = load_ledger()
    if not ledger:
        return  # unreadable ledger: nothing can be learned safely
    forms = unkeyed_inert_forms(command, ledger)
    if not forms:
        # None: an ask, a project form, an unknown tool or a non-literal part stood in the way —
        # the approval stays one-time. Empty: the command was auto-allowed; nothing to learn.
        return

    existing = set(ledger_allow_keys(ledger))
    grants: list[dict[str, Any]] = ledger.setdefault("grants", [])
    today = datetime.date.today().isoformat()
    added = False
    for form, part in forms:
        key = _grant_key(form)
        if key is None or key in existing or not _plausible_command(key):
            continue
        grants.append(
            {
                "key": key,
                "note": "auto-recorded: user approved this command",
                # The promoted PART, not the command's first line: a grant read from a pipeline
                # tail (``... | od -c | tail -3``) was once misread as parser junk because the
                # truncated first line never showed the command the key came from.
                "example": part[:160],
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
