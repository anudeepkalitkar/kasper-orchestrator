#!/usr/bin/env python3
"""PreToolUse Bash permission gate — ledger-driven auto-allow; defer only what the ledger
can't answer.

Reads the PreToolUse hook JSON on stdin and classifies the Bash command against the permission
ledger (``.claude/permissions-ledger.json`` — the single source of truth: seeded allows, guarded
patterns, recorded user grants and denials, each with a note). Decision:

  * push guard hit           -> ``permissionDecision: deny`` — the one hard block here, checked
    first and holding in every permission mode. A ``git push`` that would reach a shared branch
    (named, or implied by the current branch), pushes every branch, or forces is refused
    (:func:`push_denial`); settings.json allows every other push without a prompt.
  * ask-pattern / denial hit -> ``permissionDecision: ask`` with the ledger note as the reason, so
    destructive / outward-facing / previously-denied operations always get a human look. The
    patterns run against the raw command AND against every decomposed part in the normalized
    form the allow check judges, so quoting or restructuring cannot slip past a guard.
  * ask-words hit            -> ``ask`` too. A word rule names a tool and groups of guarded
    words: a part whose resolved head is that tool asks when every group has a match among
    its later non-flag tokens. No option grammar is modelled, so an option's value is just one
    more token — it can never hide the guarded word (fail closed).
  * every part allowed       -> ``permissionDecision: allow``. The command is decomposed first:
    sequencing (``&&  ||  ;``), pipelines (``|  |&``), background ``&``, newlines,
    subshell parens, ``$(...)``/backtick substitutions (recursively), env-var prefixes
    (``PATH=... cmd``), path heads (``.venv/bin/pytest`` -> ``pytest``) and wrapper commands
    (``xargs``/``timeout``/``env``/...) — every real command inside must match the ledger.
    A substitution body this gate cannot delimit simply (quotes, escapes, comments, nesting,
    or no closer at all) is not classified at all: it becomes an unallowed sentinel, so the
    command prompts instead of being read wrongly.
  * anything unknown         -> no decision (the normal one-time prompt) and the command is
    logged to ``.claude/permission-unknowns.log``. If the user then approves it,
    ``permission_recorder.py`` (PostToolUse) appends a grant to the ledger so the same
    command never prompts twice.

Beyond the push guard it never *blocks*: the worst case for an unrecognised command is the
normal one-time prompt. Only
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
import subprocess
import sys
from pathlib import Path
from typing import Any

# One definition of "shared branch", shared with /checkpoint so the two can never disagree.
# Same import precedent as permission_recorder.py.
sys.path.insert(0, str(Path(__file__).parent))
from git_checkpoint import PROTECTED_BRANCHES  # noqa: E402


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
# caught — this only lets a loop/conditional with an allowed body auto-allow. ``if``/``while``/
# ``until`` are skipped too: what follows them is a real command (the condition), so it is judged
# by the allow check and the ask-words guard like any other — never waved through as scaffolding.
_STRUCTURAL: frozenset[str] = frozenset(
    {"do", "done", "then", "else", "elif", "fi", "esac", "{", "}", "!", "in"}
    | {"if", "while", "until"}
)
# Openers whose segment is a declaration, not a command to classify (the body arrives as its own
# split segments; any ``$(...)`` inside is classified separately). ``[``/``[[`` are NOT here:
# ``[[ 'a[$(cmd)]' -eq 0 ]]`` evaluates the operand as arithmetic and runs ``cmd`` (and bash 5's
# ``[ -v 'a[$(cmd)]' ]`` evaluates the subscript), so a test segment is judged like any command.
_OPENERS: frozenset[str] = frozenset({"for", "case"})
# Harmless shell builtins that run no external program — allowed as scaffolding wherever they
# appear (``exit 1`` after ``||``, ``set -e``). Only builtins that never evaluate an argument as
# code, an arithmetic expression or an array subscript belong here: ``unset``, ``export``,
# ``local``, ``readonly``, ``declare``, ``wait`` (``-p var``), ``let``, ``eval``, ``source``,
# ``trap`` and ``read`` all can (``unset 'a[$(git clean -fd)]'`` runs git clean), so they are
# judged by the ledger like any command and prompt unless granted. The numeric builtins below
# parse their argument as a plain number (verified: ``exit 'a[$(cmd)]'`` does not run ``cmd``).
_BUILTINS: frozenset[str] = frozenset({"exit", "break", "continue", "return", "shift", "set"})
# Wrappers whose real command is their argument — unwrap and classify that instead.
_WRAPPERS: frozenset[str] = frozenset(
    {"xargs", "timeout", "nohup", "env", "command", "time", "nice"}
)
# Per wrapper: the options that take a separate value (consumed with it), and the bare flags.
# Anything else in a wrapper's option run — an unknown option, combined short flags, ``env -S``
# (which re-splits a string into a command) — cannot be read with confidence, so unwrapping
# fails closed (None) rather than guessing which token is the executable.
_WRAPPER_VALUE_OPTS: dict[str, frozenset[str]] = {
    "xargs": frozenset(
        {"-I", "-J", "-R", "-S", "-n", "-P", "-L", "-s", "-E", "-d", "-a"}
        | {"--arg-file", "--delimiter", "--eof", "--max-args", "--max-procs", "--max-lines"}
        | {"--max-chars", "--process-slot-var"}
    ),
    "timeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
    "env": frozenset({"-u", "-C", "-P", "--unset", "--chdir"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "time": frozenset({"-f", "-o", "--format", "--output"}),
}
_WRAPPER_FLAGS: dict[str, frozenset[str]] = {
    "xargs": frozenset(
        {"-0", "-r", "-t", "-p", "-x", "-o", "-i", "-l", "-e"}
        | {"--null", "--no-run-if-empty", "--verbose", "--interactive", "--exit", "--open-tty"}
    ),
    "timeout": frozenset({"-v", "-f", "-p", "--verbose", "--preserve-status", "--foreground"}),
    "env": frozenset({"-", "-i", "-0", "-v", "--ignore-environment", "--null", "--debug"}),
    "command": frozenset({"-p", "-v", "-V"}),
    "time": frozenset({"-p", "-v", "-a", "-l", "--portability", "--verbose", "--append"}),
}
# Builtins that evaluate an argument as code, a variable name (array subscript) or arithmetic.
# A quoted ``$(...)`` the substitution extractor rightly leaves alone (``'a[$(cmd)]'``) is still
# run when one of these evaluates it — so such a part asks explicitly (:func:`evaluated_payload`).
_EVALUATING_HEADS: frozenset[str] = frozenset(
    {"unset", "local", "export", "declare", "typeset", "readonly", "read", "eval", "source", "."}
    | {"trap", "let", "printf", "test", "[", "[["}
)
_PAYLOAD_MARKERS: tuple[str, ...] = ("$(", "`", "<(")  # what bash runs when it evaluates text
# An arithmetic body — ``(( ... ))``, ``$(( ... ))``, ``for (( ...; ...; ... ))``. Matched on the
# raw command because segment splitting treats ``(``/``)`` as subshell separators and drops them.
# Non-greedy, so a nested ``)`` ends the body early — fine: the marker sits before it.
_ARITHMETIC: re.Pattern[str] = re.compile(r"\(\((.*?)\)\)", re.DOTALL)
_EVALUATED_NOTE: str = (
    "a quoted command substitution bash may evaluate (array subscript, arithmetic, or an "
    "evaluating builtin) — confirm"
)
_ENV_ASSIGN: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_MAX_UNWRAP_DEPTH: int = 5  # nested wrappers (``env nice timeout 5 cmd``) peeled before giving up
_SUB_TOKEN: str = "__SUB__"  # placeholder left where a $(...)/backtick substitution was extracted
# Stand-in part for a substitution body the gate refuses to read (see _is_opaque): it matches no
# allow key, and the leading underscores make it unpromotable by the recorder, so a command
# carrying one always prompts instead of being classified on a guessed body.
_OPAQUE_SUBSTITUTION: str = "__opaque_substitution__"
# Characters whose Bash meaning the naive delimiting in _substitution_end cannot honour: they move
# or hide the real closing delimiter. A body containing any of them is opaque.
_OPAQUE_BODY_CHARS: frozenset[str] = frozenset("'\"\\#`")
_HEREDOC: re.Pattern[str] = re.compile(r"<<-?\s*(?P<q>['\"]?)(?P<tag>\w+)(?P=q)")
# Push guard (:func:`push_denial`): a push runs only when every check below proves it safe, so
# each list is closed — whatever is not named here is refused, never guessed at.
# Push options that take a value, as the next token or as ``--opt=value``.
_PUSH_VALUE_OPTS: frozenset[str] = frozenset(
    {"-o", "--push-option", "--repo", "--receive-pack", "--exec", "--recurse-submodules"}
)
# Every other push option let through. An abbreviation git would expand (``--al``) or an option
# from a newer git might take a value or widen the push, so it is not here.
_PUSH_FLAGS: frozenset[str] = frozenset(
    {"--set-upstream", "--tags", "--follow-tags", "--no-follow-tags", "--delete", "--dry-run"}
    | {"--porcelain", "--atomic", "--no-atomic", "--quiet", "--verbose", "--progress"}
    | {"--no-progress", "--thin", "--no-thin", "--verify", "--no-verify", "--signed"}
    | {"--no-signed", "--ipv4", "--ipv6", "--no-recurse-submodules"}
)
_PUSH_SHORT_FLAGS: frozenset[str] = frozenset("uqvnd46")  # letters a short cluster may hold
_PUSH_EVERY_BRANCH: frozenset[str] = frozenset({"--all", "--branches", "--mirror"})
_HEAD_NAMES: frozenset[str] = frozenset({"HEAD", "@"})  # refspec names for the current branch
# The only characters a push token may hold: none of them lets Bash expand, glob or split it,
# so the token the guard reads is the token git receives.
_LITERAL: re.Pattern[str] = re.compile(r"[\w./:@=+,%-]+", re.ASCII)
# An output redirection (``2>&1``, ``>/dev/null``, ``>`` then a file) — not a push argument.
# Group 1 is the target when it is attached; empty means the next token is the target.
_REDIRECTION: re.Pattern[str] = re.compile(r"(?:\d*|&)>>?(.*)", re.DOTALL)
# Heads that can change the directory later parts run in (``builtin``/``eval``/``source`` may).
_CWD_CHANGERS: frozenset[str] = frozenset({"cd", "pushd", "popd", "builtin", "eval", "source", "."})
# The one directory change that is followed: the command opens with ``cd <literal> &&``.
_CD_THEN: re.Pattern[str] = re.compile(
    r"cd\s+(?P<dir>[\w./@+,%-]+)\s*&&(?P<rest>.*)", re.ASCII | re.DOTALL
)
_REF_PREFIXES: tuple[str, ...] = ("refs/", "heads/")  # stripped in order: git resolves each form
_GIT_TIMEOUT_SECONDS: int = 5
_ORIGIN_HEAD_REF: str = "refs/remotes/origin/HEAD"
_ORIGIN_PREFIX: str = "refs/remotes/origin/"
_HEADS_PREFIX: str = "refs/heads/"
_REF_FIELDS: int = 3  # %(HEAD), %(refname), %(symref) — the lookup's tab-separated format
_PUSH_RULE: str = "shared-branch and force pushes are human-only (git-workflow)"


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


def _word_hit(word: str, token: str) -> bool:
    """Whether a token is a guarded word: equal to it, or — for a word ending in ``-`` (an
    ``aws`` verb prefix such as ``delete-``) — starting with it.
    """
    return token.startswith(word) if word.endswith("-") else token == word


def ask_words_match(command: str, ledger: dict[str, Any]) -> str | None:
    """Return the note of the first ``ask_words`` rule a command part trips, else None.

    A rule is ``{"tool": str, "all": [[word, ...], ...], "note": str}``. A part trips it when
    its head — env prefixes, wrappers and path stripped, as the allow check resolves it — is
    ``tool`` and every group in ``all`` has a word among the part's later tokens that do not
    start with ``-`` (an empty ``all`` guards the tool itself). Tokens come from the same
    quote-aware split as the allow check, so ``-m "merge notes"`` stays one token and never
    matches, while any option value is just another token and never hides a guarded word.
    """
    rules = [r for r in ledger.get("ask_words") or [] if isinstance(r, dict) and r.get("tool")]
    for part in command_parts(command):
        tokens = _unwrap_tokens(_segment_tokens(part))
        if tokens is None:
            # A wrapper this gate cannot read could be hiding any guarded command.
            return "wrapper options could not be resolved — confirm the wrapped command"
        if not tokens:
            continue
        words = [t for t in tokens[1:] if not t.startswith("-")]
        for rule in rules:
            groups: list[list[str]] = rule.get("all") or []
            if tokens[0] == rule["tool"] and all(
                any(_word_hit(w, t) for w in group for t in words) for group in groups
            ):
                return str(rule.get("note") or "matched a guarded word rule")
    return None


def evaluated_payload(command: str) -> str | None:
    """Return a note when a part carries a command substitution bash may evaluate, else None.

    Trips on an arithmetic body (``((``, ``$((``, ``for ((``) in the raw command that holds
    ``$(``, a backtick or ``<(``; or a part with such a token — quoted or not — inside an array
    subscript (``[``), or whose resolved head is an evaluating builtin (``_EVALUATING_HEADS``).
    Fails closed: a builtin is flagged whatever its options, and a part whose wrappers cannot be
    read is flagged whatever it holds.
    """
    joined = _strip_heredocs(command).replace("\\\n", " ")
    for body in _ARITHMETIC.findall(joined):
        if any(marker in body for marker in _PAYLOAD_MARKERS):
            return _EVALUATED_NOTE  # arithmetic evaluates its operands — subscripts included
    for part in command_parts(command):
        raw = _segment_tokens(part)
        tokens = _unwrap_tokens(raw)
        if tokens is None:
            return _EVALUATED_NOTE  # an unreadable wrapper could be running anything
        if not any(marker in token for token in raw for marker in _PAYLOAD_MARKERS):
            continue
        if tokens and tokens[0] in _EVALUATING_HEADS:
            return _EVALUATED_NOTE
        for token in tokens:
            if any(m in token for m in _PAYLOAD_MARKERS) and ("[" in token or "((" in token):
                return _EVALUATED_NOTE
    return None


def ask_match(command: str, ledger: dict[str, Any]) -> str | None:
    """Return the note of the first ask-words rule, ask-pattern or denial the command trips,
    else None. Word rules (:func:`ask_words_match`) and evaluated payloads
    (:func:`evaluated_payload`) are checked first.

    Each ``ask_patterns``/``denials`` regex is applied with ``re.search`` (so a ``^`` anchors a
    part's head) to two views of the command: the raw string, so a guarded token anywhere — a
    heredoc body, a comment — still prompts; and every decomposed part in the normalized form
    the allow check judges (``_normalized_parts``), so what the ledger allows and what it guards
    are read from the same text. A match on either view is an ask; a malformed regex is skipped.
    """
    word_note = ask_words_match(command, ledger) or evaluated_payload(command)
    if word_note is not None:
        return word_note
    views = [command, *_normalized_parts(command)]
    for entry in list(ledger.get("ask_patterns") or []) + list(ledger.get("denials") or []):
        pattern = entry.get("pattern") if isinstance(entry, dict) else None
        if not pattern:
            continue
        try:
            if any(re.search(pattern, view) for view in views):
                return str(entry.get("note") or "matched a guarded pattern")
        except re.error:
            continue
    return None


def _substitution_end(command: str, body: int, backtick: bool) -> int:
    """Index just past a substitution's closing delimiter, or -1 when it is never closed.

    ``body`` is the index of the first character inside the substitution. The scan is deliberately
    naive — a backtick body ends at the next backtick, a ``$(`` body at the ``)`` that brings a raw
    paren count back to zero — because delimiting one exactly takes Bash's own lexer: quoting,
    escapes, comments and nesting all move the real end. Correctness comes from ``_is_opaque``
    instead, which refuses to classify any body those rules could apply to.
    """
    if backtick:
        end = command.find("`", body)
        return -1 if end < 0 else end + 1
    depth, i, n = 1, body, len(command)
    while i < n:
        if command[i] == "(":
            depth += 1
        elif command[i] == ")":
            depth -= 1
            if not depth:
                return i + 1
        i += 1
    return -1


def _is_opaque(body: str) -> bool:
    """True when Bash might end this substitution body somewhere other than the naive scan did.

    A quote, a backslash, a ``#`` comment, a backtick or a nested ``$(`` each move or hide the
    real closing delimiter, so the extracted text may be neither the whole body nor a command.
    The caller replaces such a body with ``_OPAQUE_SUBSTITUTION`` instead of classifying it: the
    gate then prompts, which is the only safe direction — it can ask more often than an exact
    reading would, never allow more.
    """
    return any(char in _OPAQUE_BODY_CHARS for char in body) or "$(" in body


def _extract_substitutions(command: str) -> tuple[str, list[str]]:
    """Pull ``$(...)`` and backtick bodies out of the command.

    Returns the outer command with each substitution replaced by ``__SUB__``, plus the list of
    inner commands to classify on their own. Single-quoted text is literal (no substitution);
    inside double quotes substitutions ARE live, so only single quotes suppress extraction.
    Quote tracking is two-state like ``_split_segments``: an apostrophe inside double quotes
    (``echo "it's $(curl ...)"``) is literal and must not hide the substitution that follows it —
    treating it as an opening quote made the gate allow an unclassified command.

    A body that is opaque (``_is_opaque``) or never closed yields ``_OPAQUE_SUBSTITUTION`` in
    place of an inner command, so it prompts; the rest of the outer command is classified as
    usual.
    """
    inners: list[str] = []
    out: list[str] = []
    i, n = 0, len(command)
    quote: str | None = None
    while i < n:
        char = command[i]
        if quote == "'":  # single quotes are fully literal — not even a backslash escapes
            out.append(char)
            if char == "'":
                quote = None
            i += 1
            continue
        if char == "\\" and i + 1 < n:
            out.append(command[i : i + 2])
            i += 2
        elif char == '"':
            quote = None if quote == '"' else '"'
            out.append(char)
            i += 1
        elif char == "'" and quote is None:
            quote = "'"
            out.append(char)
            i += 1
        elif command.startswith("$(", i) or char == "`":
            backtick = char == "`"
            body = i + (1 if backtick else 2)
            end = _substitution_end(command, body, backtick)
            out.append(f" {_SUB_TOKEN} ")
            if end < 0:  # never closed: the rest of the command is unreadable too
                inners.append(_OPAQUE_SUBSTITUTION)
                break
            inner = command[body : end - 1]
            inners.append(_OPAQUE_SUBSTITUTION if _is_opaque(inner) else inner)
            i = end
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

    Split points: ``&&``, ``||``, ``|&``, ``|``, ``;``, background ``&`` (not the ``&`` in
    ``2>&1``), and ``\\n`` (heredoc bodies are already stripped by ``_strip_heredocs``).
    Unquoted parens (subshell/grouping) become whitespace so ``(cd a && ls)`` classifies
    cleanly. Anything inside single or double quotes never splits.
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


def _unwrap_tokens(tokens: list[str], depth: int = 0) -> list[str] | None:
    """Reduce a segment's tokens to the command the allow check judges.

    Drops leading scaffolding and env-var assignments, reduces a path head to its basename
    (``_strip_tokens``), and peels wrapper commands (``xargs``/``timeout``/``env``/...) down to
    their argument, re-stripping after each. Returns None when wrappers nest deeper than
    ``_MAX_UNWRAP_DEPTH`` or a wrapper carries an option it cannot read
    (:func:`_skip_wrapper_options`) — unreadable, so the caller must not allow it.
    """
    if depth > _MAX_UNWRAP_DEPTH:
        return None
    tokens = _strip_tokens(tokens)
    if not tokens or tokens[0] not in _WRAPPERS:
        return tokens
    rest = _skip_wrapper_options(tokens[0], tokens[1:])
    if rest is None:
        return None
    if tokens[0] == "timeout" and rest:
        rest = rest[1:]  # skip the duration argument
    return _unwrap_tokens(rest, depth + 1)


def _skip_wrapper_options(wrapper: str, rest: list[str]) -> list[str] | None:
    """Drop a wrapper's leading options, each value-taking one together with its value.

    Args:
        wrapper: The wrapper command (a member of ``_WRAPPERS``).
        rest: The tokens after it.

    Returns:
        The tokens from the wrapped command on, or None when an option is not one this gate
        knows for that wrapper — unreadable, so the caller must fail closed.
    """
    values = _WRAPPER_VALUE_OPTS.get(wrapper, frozenset())
    flags = _WRAPPER_FLAGS.get(wrapper, frozenset())
    while rest and rest[0].startswith("-"):
        option = rest[0]
        if option == "--":
            return rest[1:]
        if option in values:
            rest = rest[2:]  # the option and its separate value
        elif option in flags or option.split("=", 1)[0] in values | flags:
            rest = rest[1:]  # a bare flag, or ``--long=value`` carrying its own value
        elif option[:2] in values and not option.startswith("--"):
            rest = rest[1:]  # a short option with its value attached (``-n1``, ``-I{}``)
        else:
            return None
    return rest


def _tokens_allowed(tokens: list[str], keys: list[str]) -> bool:
    """True when the normalized token list (``_unwrap_tokens``) is pure scaffolding, a
    declaration/substitution placeholder, a harmless builtin, or starts with an allowed key.
    """
    unwrapped = _unwrap_tokens(tokens)
    if unwrapped is None:
        return False  # wrapped past reading — never allow what cannot be classified
    if not unwrapped:
        return True  # pure scaffolding / bare env assignment
    head = unwrapped[0]
    if head in _OPENERS or head == _SUB_TOKEN:
        return True  # declaration or extracted substitution (classified separately)
    if head in _BUILTINS:
        return True  # harmless shell builtin — runs no external program
    effective: list[str] = []  # key match ignores flag tokens (and -C's path argument)
    skip_next = False
    for token in unwrapped:
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
    return _tokens_allowed(_segment_tokens(text), keys)


def _segment_tokens(text: str) -> list[str]:
    """Quote-aware tokenization of one segment, shared by the allow and ask checks.

    ``'X="a b"'`` is ONE env-assign token, never a stray ``b"`` head; ``comments=True`` makes a
    ``# ...`` segment pure scaffolding. Unbalanced quotes fall back to a naive whitespace split,
    which at worst prompts — never silently allows more.
    """
    try:
        return shlex.split(text, comments=True, posix=True)
    except ValueError:
        return text.split()


def command_parts(command: str) -> list[str]:
    """Flatten a command into every classifiable part: outer segments + recursive
    substitution bodies.
    """
    joined = _strip_heredocs(command).replace("\\\n", " ")  # backslash-newline continuations
    outer, inners = _extract_substitutions(joined)
    parts = [seg for seg in _split_segments(outer) if seg.strip()]
    for inner in inners:
        parts.extend(command_parts(inner))
    return parts


def _normalized_parts(command: str) -> list[str]:
    """Every classifiable part as the allow check judges it: quotes resolved, scaffolding, env
    prefixes and wrappers stripped, the path head reduced, tokens joined by single spaces.

    Parts wrapped past reading (``_unwrap_tokens`` -> None) are omitted — the allow check
    refuses them anyway, so the command prompts regardless.
    """
    # why: a raw-only ask check missed ``git commit --no-""verify`` — Bash drops the empty
    # quotes, so the flag the ledger guards only exists in the unquoted tokens the allow check
    # was already auto-allowing.
    normalized: list[str] = []
    for part in command_parts(command):
        tokens = _unwrap_tokens(_segment_tokens(part))
        if tokens:
            normalized.append(" ".join(tokens))
    return normalized


def unallowed_parts(command: str, ledger: dict[str, Any]) -> list[str]:
    """The parts of the command the ledger does NOT allow (empty list == fully auto-allowable)."""
    keys = ledger_allow_keys(ledger)
    patterns = list(ledger.get("allow_patterns") or [])
    return [part for part in command_parts(command) if not _segment_allowed(part, keys, patterns)]


def _read_push(tokens: list[str]) -> tuple[list[str], list[str]] | str | None:
    """Read a normalized part as a ``git push``.

    Args:
        tokens: The part's tokens as :func:`_unwrap_tokens` returns them.

    Returns:
        None when the part is not a ``git push``; ``(git -C directories in order, the tokens
        after push)`` when it is; or a refusal reason when a git option other than ``-C`` and
        ``--no-pager`` stands ahead of a ``push`` token — it may take a value or redirect the
        push (``-c``, ``--git-dir``), so the part cannot be read.
    """
    if tokens[:1] != ["git"] or "push" not in tokens:
        return None
    directories: list[str] = []
    rest = iter(tokens[1:])
    for token in rest:
        if token == "-C":
            directories.append(next(rest, ""))
        elif token.startswith("-C"):
            directories.append(token[2:])
        elif not token.startswith("-"):
            return (directories, list(rest)) if token == "push" else None
        elif token != "--no-pager":
            return f"git option {token} ahead of push is not one the push guard can read"
    return None


def _branch_state(cwd: str) -> tuple[str | None, str | None] | None:
    """The checked-out branch and the branch origin's HEAD names, or None when git cannot say.

    One git call answers both — ``%(HEAD)`` stars the current branch, ``%(symref)`` resolves
    origin's HEAD — because this runs inside a hook on a ``git push``. Origin's HEAD is the
    only mainline candidate ``new_task.mainline`` knows that :data:`PROTECTED_BRANCHES` does
    not already name.

    Args:
        cwd: The directory the push runs in.

    Returns:
        ``(branch, origin_head)`` — the branch None on a detached HEAD, origin's HEAD None when
        the repository records none; or None when the lookup failed (no repository, no git, a
        timeout) or its output is anything but the expected lines (undecodable bytes, a
        malformed line, an unexpected ref, an origin HEAD naming no origin branch, an empty or
        whitespace-holding branch name, two current branches) — which the caller must treat
        as "nothing is proven".
    """
    try:
        result = subprocess.run(
            ["git", "for-each-ref", "--format=%(HEAD)%09%(refname)%09%(symref)"]
            + ["refs/heads", _ORIGIN_HEAD_REF],
            cwd=cwd,
            capture_output=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        lines = result.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None  # a name read approximately could fail to match a shared branch
    branch: str | None = None
    origin_head: str | None = None
    for line in lines:
        fields = line.split("\t")
        if len(fields) != _REF_FIELDS:
            return None
        marker, ref, symref = fields
        # Origin's HEAD must name an origin branch, every other line a local one.
        full, prefix = (symref, _ORIGIN_PREFIX) if ref == _ORIGIN_HEAD_REF else (ref, _HEADS_PREFIX)
        name = full.removeprefix(prefix)
        if not full.startswith(prefix) or not name or any(char.isspace() for char in name):
            return None  # an empty or blank name would match no shared branch
        if ref == _ORIGIN_HEAD_REF:
            origin_head = name
        elif marker == "*":
            if branch is not None:
                return None
            branch = name
    return branch, origin_head


def _without_redirections(tokens: list[str]) -> list[str]:
    """The tokens minus output redirections (:data:`_REDIRECTION`) and their targets."""
    kept: list[str] = []
    rest = iter(tokens)
    for token in rest:
        redirection = _REDIRECTION.fullmatch(token)
        if redirection is None:
            kept.append(token)
        elif not redirection[1]:
            next(rest, None)  # the target is the next token
    return kept


def _push_refusal(raw: list[str], directories: list[str], args: list[str], cwd: str) -> str | None:
    """Why one ``git push`` must not run, or None when every check proves it safe.

    In order: nothing but shell scaffolding may precede ``git`` (an env assignment or a wrapper
    can redirect the push); every token must be literal (:data:`_LITERAL`); every option must
    be on a closed list, none of them forcing or pushing every branch; the repository's branch
    state must be readable; and every refspec must be ``[+]`` -free ``[<src>:]<dst>`` whose
    destination, with :data:`_REF_PREFIXES` stripped, is not a shared branch —
    :data:`PROTECTED_BRANCHES` or origin's HEAD, compared case-insensitively. No refspec, or a
    bare ``HEAD``/``@``, means the current branch, which must then be known.

    Args:
        raw: The part's tokens before env prefixes and wrappers were stripped.
        directories: The ``git -C`` directories, in order.
        args: The tokens after ``push``.
        cwd: The directory the part runs in.
    """
    head = next(i for i, token in enumerate(raw) if token.rsplit("/", 1)[-1] == "git")
    if not all(token in _STRUCTURAL for token in raw[:head]):
        return "an env assignment or wrapper ahead of git may redirect the push"
    args = _without_redirections(args)
    for token in [*directories, *args]:
        if _SUB_TOKEN in token or not _LITERAL.fullmatch(token):
            return f"{token!r} is not a literal the push guard can read (expansion, glob, quoting)"
    positionals: list[str] = []
    remote_by_option = False
    rest = iter(args)
    for arg in rest:
        name, equals, _ = arg.partition("=")
        short = arg.startswith("-") and not arg.startswith("--")
        if arg == "--":
            positionals.extend(rest)
        elif not arg.startswith("-"):
            positionals.append(arg)
        elif name in _PUSH_EVERY_BRANCH:
            return f"{arg} pushes every branch, shared ones included"
        elif name in _PUSH_VALUE_OPTS:
            if not equals:
                next(rest, None)  # the value is the next token
            remote_by_option = remote_by_option or name == "--repo"
        elif short and arg.startswith("-o"):
            continue  # ``-o<value>``: the push option's value is attached
        elif name in _PUSH_FLAGS or (short and set(arg[1:]) <= _PUSH_SHORT_FLAGS):
            continue
        elif arg.startswith("--force") or (short and "f" in arg):
            return f"{arg} is a force push"
        else:
            return f"{arg} is not an option the push guard can read"
    state = _branch_state(os.path.join(cwd, *directories))
    if state is None:
        return "the repository's branches could not be read, so nothing proves the push safe"
    branch, origin_head = state
    shared = PROTECTED_BRANCHES | ({origin_head.lower()} if origin_head else set())
    # The first positional is the remote. With --repo git still reads it that way, but a reader
    # takes ``--repo origin master`` for a push to master, so every positional is then judged
    # as a refspec — the stricter reading.
    refspecs = positionals if remote_by_option else positionals[1:]
    for refspec in refspecs or ["HEAD"]:  # no refspec: git pushes the current branch
        if refspec.startswith("+"):
            return f"{refspec} is a force push by refspec"
        *sources, destination = refspec.split(":")
        if destination in _HEAD_NAMES and not sources:
            if branch is None:
                return "HEAD is detached, so the branch it pushes to is unknown"
            destination = branch
        elif len(sources) > 1 or not destination or "//" in destination:
            return f"{refspec} is not a refspec form the push guard recognises"
        for prefix in _REF_PREFIXES:
            destination = destination.removeprefix(prefix)
        if destination in _HEAD_NAMES:
            return f"{refspec} names HEAD as its destination, which is no branch"
        if destination.lower() in shared:
            return f"it would push to the shared branch '{destination}'"
    return None


def _push_denial(command: str, cwd: str) -> str | None:
    """:func:`push_denial` without its crash guard."""
    pushes: list[tuple[list[str], tuple[list[str], list[str]] | str]] = []
    cwd_changes = 0
    for part in command_parts(command):
        raw = _segment_tokens(part)
        tokens = _unwrap_tokens(raw)
        if tokens is None:
            if "push" in part:
                return "a wrapper the gate cannot read may hide a push"
            continue
        read = _read_push(tokens)
        if tokens[:1] and tokens[0] in _CWD_CHANGERS:
            cwd_changes += 1
        elif read is not None:
            pushes.append((raw, read))
    if not pushes:
        return None
    if cwd_changes:
        opening = _CD_THEN.fullmatch(_strip_heredocs(command).replace("\\\n", " ").strip())
        first = command_parts(opening["rest"])[:1] if opening else []
        followed = cwd_changes == 1 and len(pushes) == 1 and first
        if not opening or not followed or _segment_tokens(first[0]) != pushes[0][0]:
            return (
                "the directory it runs in cannot be followed — use `git -C <dir> push …`, "
                "or exactly `cd <dir> && git push …`"
            )
        cwd = os.path.join(cwd, opening["dir"])
    for raw, read in pushes:
        reason = read if isinstance(read, str) else _push_refusal(raw, read[0], read[1], cwd)
        if reason is not None:
            return reason
    return None


def push_denial(command: str, cwd: str) -> str | None:
    """Why the command's ``git push`` must be denied outright, or None.

    A push is let through only when the guard proves it safe (:func:`_push_refusal`); whatever
    it cannot read is refused. The directory is the hook's, moved only by ``git -C <dir>`` or
    by the command opening with ``cd <dir> &&`` immediately followed by its single push — any
    other directory change beside a push (a subshell, ``;``, ``||``, a pipe, ``pushd``) is
    refused, because the part it applies to cannot be told from the text. Not seen at all: a
    push inside ``bash -c``, a script or an alias.

    Args:
        command: The raw Bash command.
        cwd: The directory the command starts in.

    Returns:
        The reason for the first refused push, else None. An unexpected error while judging a
        command that mentions ``push`` is itself a refusal — the hook must never crash open.
    """
    try:
        return _push_denial(command, cwd)
    except Exception as error:  # fail closed: a crash here would let the push through
        return f"the push guard failed ({type(error).__name__})" if "push" in command else None


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
    """Classify the incoming Bash command against the ledger and emit a permission decision
    (or none).
    """
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command", "")
    if not command.strip():
        return

    denial = push_denial(command, str(data.get("cwd") or Path.cwd()))
    if denial is not None:
        print(_decision("deny", f"blocked by the push guard: {denial} — {_PUSH_RULE}"))
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

    # Unknown: log it so /permit --review (or a user grant via the recorder) can promote it,
    # then defer.
    try:
        UNKNOWN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with UNKNOWN_LOG.open("a", encoding="utf-8") as handle:
            handle.write(command.splitlines()[0][:200] + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
