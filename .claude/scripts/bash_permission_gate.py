#!/usr/bin/env python3
"""PreToolUse Bash permission gate — ledger-driven auto-allow; defer only what the ledger can't answer.

Reads the PreToolUse hook JSON on stdin and classifies the Bash command against the permission
ledger (``.claude/permissions-ledger.json`` — the single source of truth: seeded allows, guarded
patterns, recorded user grants and denials, each with a note). Decision:

  * ask-pattern / denial hit -> ``permissionDecision: ask`` with the ledger note as the reason, so
    destructive / outward-facing / previously-denied operations always get a human look.
  * every part allowed       -> ``permissionDecision: allow``. The command is decomposed first:
    sequencing (``&&  ||  ;``), pipelines (``|  |&``), background ``&``, newlines,
    subshell parens, ``$(...)``/backtick substitutions (recursively), env-var prefixes
    (``PATH=... cmd``), path heads (``.venv/bin/pytest`` -> ``pytest``) and wrapper commands
    (``xargs``/``timeout``/``env``/...) — every real command inside must match the ledger.
  * anything unknown         -> no decision (the normal one-time prompt) and the command is logged to
    ``.claude/permission-unknowns.log``. If the user then approves it, ``permission_recorder.py``
    (PostToolUse) appends a grant to the ledger so the same command never prompts twice.

It never *blocks*: the worst case for an unrecognised command is the normal one-time prompt. Only
Bash is gated here — other tools are governed by settings.json. Splitting is quote-aware: ``;`` /
``&&`` / ``|`` inside quotes (``python3 -c "a; b"``, commit messages) never split, and heredoc
bodies are stripped before classification — they are data for the command, never commands
themselves (ask-patterns still see the raw command, so a dangerous body still forces a prompt).

Every verdict surfaces natively: an ask or an unknown becomes the harness's own permission prompt,
in front of the human, in the session they are already in (ADR-0024). Nothing is parked, relayed,
or answered on the human's behalf.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    """The .claude config dir holding the ledger — repo-local if present, else the global ~/.claude.

    Prefers ``$CLAUDE_PROJECT_DIR/.claude`` (the ledger travels with this portable setup); falls
    back to ``~/.claude`` for when the setup is installed as the global config.
    """
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if project:
        local = Path(project) / ".claude"
        if (local / "permissions-ledger.json").is_file():
            return local
    return Path.home() / ".claude"


LEDGER_PATH: Path = _config_dir() / "permissions-ledger.json"
UNKNOWN_LOG: Path = _config_dir() / "permission-unknowns.log"

# Shell scaffolding tokens skipped when finding a segment's real command. Ask-patterns are checked
# against the WHOLE command first, so a dangerous body (``for x; do rm -rf ...; done``) is still
# caught — this only lets a loop/conditional with an allowed body auto-allow.
_STRUCTURAL: frozenset[str] = frozenset(
    {"do", "done", "then", "else", "elif", "fi", "esac", "{", "}", "!", "in"}
)
# Control-flow openers whose segment is a declaration, not a command to classify (the body arrives
# as its own split segments; any ``$(...)`` inside is classified separately).
_OPENERS: frozenset[str] = frozenset({"for", "while", "until", "if", "case", "[", "[["})
# Harmless shell builtins that run no external program — allowed as scaffolding wherever they
# appear (``exit 1`` after ``||``, ``set -e``, ``export FOO=$(...)`` — the substitution is still
# classified on its own).
_BUILTINS: frozenset[str] = frozenset(
    {
        "exit",
        "break",
        "continue",
        "return",
        "set",
        "export",
        "unset",
        "shift",
        "wait",
        "local",
        "readonly",
    }
)
# Wrappers whose real command is their argument — unwrap and classify that instead.
_WRAPPERS: frozenset[str] = frozenset(
    {"xargs", "timeout", "nohup", "env", "command", "time", "nice"}
)
_ENV_ASSIGN: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SUB_TOKEN: str = "__SUB__"  # placeholder left where a $(...)/backtick substitution was extracted
_HEREDOC: re.Pattern[str] = re.compile(r"<<-?\s*(?P<q>['\"]?)(?P<tag>\w+)(?P=q)")


def load_ledger() -> dict[str, Any]:
    """Read the permission ledger; on any failure return an empty ledger (=> everything prompts)."""
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def ledger_allow_keys(ledger: dict[str, Any]) -> list[str]:
    """All token-prefix allow keys: the seeded ``allow_keys`` plus recorded ``grants``."""
    keys = [k for k in (ledger.get("allow_keys") or {}) if isinstance(k, str) and k.strip()]
    keys += [g["key"] for g in (ledger.get("grants") or []) if isinstance(g, dict) and g.get("key")]
    return sorted({k.strip() for k in keys})


def ask_match(command: str, ledger: dict[str, Any]) -> str | None:
    """Return the note of the first ask-pattern/denial matching the raw command, else None."""
    for entry in list(ledger.get("ask_patterns") or []) + list(ledger.get("denials") or []):
        pattern = entry.get("pattern") if isinstance(entry, dict) else None
        if not pattern:
            continue
        try:
            if re.search(pattern, command):
                return str(entry.get("note") or "matched a guarded pattern")
        except re.error:
            continue
    return None


def _extract_substitutions(command: str) -> tuple[str, list[str]]:
    """Pull ``$(...)`` (nested) and backtick bodies out of the command.

    Returns the outer command with each substitution replaced by ``__SUB__``, plus the list of
    inner commands to classify on their own. Single-quoted text is literal (no substitution);
    inside double quotes substitutions ARE live, so only single quotes suppress extraction.
    """
    inners: list[str] = []
    out: list[str] = []
    i, n = 0, len(command)
    in_single = False
    while i < n:
        char = command[i]
        if in_single:
            out.append(char)
            if char == "'":
                in_single = False
            i += 1
            continue
        if char == "'":
            in_single = True
            out.append(char)
            i += 1
        elif char == "\\" and i + 1 < n:
            out.append(command[i : i + 2])
            i += 2
        elif command.startswith("$(", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if command[j] == "(":
                    depth += 1
                elif command[j] == ")":
                    depth -= 1
                j += 1
            inners.append(command[i + 2 : j - 1])
            out.append(f" {_SUB_TOKEN} ")
            i = j
        elif char == "`":
            j = command.find("`", i + 1)
            if j == -1:
                out.append(char)
                i += 1
            else:
                inners.append(command[i + 1 : j])
                out.append(f" {_SUB_TOKEN} ")
                i = j + 1
        else:
            out.append(char)
            i += 1
    return "".join(out), inners


def _strip_heredocs(command: str) -> str:
    """Remove heredoc bodies — they are data for a command, never commands themselves.

    Marker lines stay (so ``python3 - <<'EOF'`` still classifies as ``python3``); body lines up to
    and including the terminator are dropped. Multiple heredocs on one line are consumed in order.
    ``<<<`` herestrings are left untouched. Ask-patterns run against the RAW command before this,
    so guarded content inside a body still forces a prompt.
    """
    if "<<" not in command:
        return command
    lines = command.split("\n")
    kept: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        kept.append(line)
        tags = [
            m.group("tag")
            for m in _HEREDOC.finditer(line)
            if not (m.start() > 0 and line[m.start() - 1] == "<")  # skip <<< herestrings
        ]
        i += 1
        for tag in tags:
            while i < len(lines) and lines[i].strip("\t ") != tag:
                i += 1
            i += 1  # drop the terminator line too
    return "\n".join(kept)


def _split_segments(command: str) -> list[str]:
    """Split on unquoted sequencing/pipe/background operators and newlines.

    Split points: ``&&``, ``||``, ``|&``, ``|``, ``;``, background ``&`` (not the ``&`` in ``2>&1``),
    and ``\\n`` (heredoc bodies are already stripped by ``_strip_heredocs``). Unquoted parens
    (subshell/grouping) become whitespace so ``(cd a && ls)`` classifies cleanly. Anything inside
    single or double quotes never splits.
    """
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i, n = 0, len(command)
    while i < n:
        char = command[i]
        if quote is not None:
            buf.append(char)
            if char == "\\" and quote == '"' and i + 1 < n:
                buf.append(command[i + 1])
                i += 2
                continue
            if char == quote:
                quote = None
            i += 1
            continue
        if char in ("'", '"'):
            quote = char
            buf.append(char)
        elif char == "\\" and i + 1 < n:
            buf.append(char)
            buf.append(command[i + 1])
            i += 2
            continue
        elif (
            command.startswith("&&", i)
            or command.startswith("||", i)
            or command.startswith("|&", i)
        ):
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        elif char in (";", "|"):
            segments.append("".join(buf))
            buf = []
        elif char == "&":
            if (i > 0 and command[i - 1] == ">") or (
                i + 1 < n and command[i + 1] == ">"
            ):  # 2>&1 / &>file style redirect, not background
                buf.append(char)
            else:
                segments.append("".join(buf))
                buf = []
        elif char == "\n":
            segments.append("".join(buf))
            buf = []
        elif char in ("(", ")"):
            buf.append(" ")
        else:
            buf.append(char)
        i += 1
    segments.append("".join(buf))
    return segments


def _strip_tokens(tokens: list[str]) -> list[str]:
    """Drop leading scaffolding + env-var assignments; reduce a path head to its basename."""
    tokens = list(tokens)
    while tokens and (tokens[0] in _STRUCTURAL or _ENV_ASSIGN.match(tokens[0])):
        tokens.pop(0)
    if tokens and "/" in tokens[0]:
        tokens[0] = tokens[0].rsplit("/", 1)[-1]
    return tokens


def _tokens_allowed(tokens: list[str], keys: list[str], depth: int = 0) -> bool:
    """True when the (stripped) token list starts with an allowed key, is scaffolding, or unwraps to one."""
    if depth > 5:
        return False
    tokens = _strip_tokens(tokens)
    if not tokens:
        return True  # pure scaffolding / bare env assignment
    head = tokens[0]
    if head in _OPENERS or head == _SUB_TOKEN:
        return True  # declaration or extracted substitution (classified separately)
    if head in _BUILTINS:
        return True  # harmless shell builtin — runs no external program
    if head in _WRAPPERS:
        rest = tokens[1:]
        while rest and rest[0].startswith("-"):
            rest = rest[1:]
        if head == "timeout" and rest:
            rest = rest[1:]  # skip the duration argument
        return _tokens_allowed(rest, keys, depth + 1)
    effective: list[str] = []  # key match ignores flag tokens (and -C's path argument)
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token == "-C":
            skip_next = True  # -C takes a directory argument (git -C, make -C)
            continue
        if not token.startswith("-"):
            effective.append(token)
    for key in keys:
        key_tokens = key.split()
        if effective[: len(key_tokens)] == key_tokens:
            return True
    return False


def _segment_allowed(segment: str, keys: list[str], patterns: list[dict[str, Any]]) -> bool:
    """True when one sequencing/pipeline segment is allowed by pattern or token-prefix key."""
    text = segment.strip()
    if not text:
        return True
    for entry in patterns:
        pattern = entry.get("pattern") if isinstance(entry, dict) else None
        if not pattern:
            continue
        try:
            if re.search(pattern, text):
                return True
        except re.error:
            continue
    # Quote-aware tokenization: 'X="a b"' is ONE env-assign token, never a stray 'b"' head;
    # comments=True makes a '# ...' segment pure scaffolding. Unbalanced quotes fall back to the
    # old naive split, which at worst prompts — never silently allows more.
    try:
        tokens = shlex.split(text, comments=True, posix=True)
    except ValueError:
        tokens = text.split()
    return _tokens_allowed(tokens, keys)


def command_parts(command: str) -> list[str]:
    """Flatten a command into every classifiable part: outer segments + recursive substitution bodies."""
    joined = _strip_heredocs(command).replace("\\\n", " ")  # backslash-newline continuations
    outer, inners = _extract_substitutions(joined)
    parts = [seg for seg in _split_segments(outer) if seg.strip()]
    for inner in inners:
        parts.extend(command_parts(inner))
    return parts


def unallowed_parts(command: str, ledger: dict[str, Any]) -> list[str]:
    """The parts of the command the ledger does NOT allow (empty list == fully auto-allowable)."""
    keys = ledger_allow_keys(ledger)
    patterns = list(ledger.get("allow_patterns") or [])
    return [part for part in command_parts(command) if not _segment_allowed(part, keys, patterns)]


def _decision(kind: str, reason: str) -> str:
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": kind,
                "permissionDecisionReason": reason,
            }
        }
    )


def main() -> None:
    """Classify the incoming Bash command against the ledger and emit a permission decision (or none)."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command", "")
    if not command.strip():
        return

    ledger = load_ledger()
    note = ask_match(command, ledger)
    if note is not None:
        print(_decision("ask", f"guarded by permission ledger: {note}"))
        return
    parts = unallowed_parts(command, ledger)
    if not parts:
        print(_decision("allow", "all parts covered by the permission ledger"))
        return

    # Unknown: log it so /permit --review (or a user grant via the recorder) can promote it, then defer.
    try:
        UNKNOWN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with UNKNOWN_LOG.open("a", encoding="utf-8") as handle:
            handle.write(command.splitlines()[0][:200] + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
