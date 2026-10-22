#!/usr/bin/env python3
"""Run one KASPER debate: several seats on two vendors propose, critique, and one judge answers.

The design is ``docs/design/debate-seats.md`` (ADR-0014). KASPER invokes this script as one
Bash call before an ``architect`` (or, once enabled, ``researcher``) spawn whose question is
contested. Every seat is a headless CLI call — ``claude -p`` or ``codex exec`` on the CLI's
saved sign-in — run read-only, with its prompt on stdin, in its own process group:

1. **propose** — every proposer answers the brief independently;
2. **critique** — every proposer with a valid proposal reads all valid proposals, anonymised
   as ``Seat A``…, and critiques the others (skipped with ``--no-critique``);
3. **judge** — one seat that never proposed writes the answer, with dissent recorded.

Calls are ``2·P + 1`` (``P + 1`` without the critique) and never exceed ``--max-calls``. One
sequential lane per vendor; the lanes run concurrently. Nothing is retried.

The report's header lines (``DEBATE:`` / ``ROLE:`` / ``SEATS:`` / ``CALLS:`` / ``REASON:``)
are written by this script, never by a model — a seat's formatting is not a verdict. Every
seat's own output and transcript sit beside the report in ``<out>.d/``. Once ``--out`` has
been probed, every exit writes a report; stdout carries the report's head, then its path.

Claude seats run only under the verified hook isolation (:data:`CLAUDE_SETTING_SOURCES`),
with the session's ``permissions.deny`` rules passed as ``--disallowedTools``, and a hook
canary is checked around every Claude call: a trip ends the debate.

The project is the **working directory** the script runs from, as with ``codex_seat.py``;
``--brief`` and ``--out`` are relative to it, and a directory with no ``.git`` is refused.

Usage:
    python3 debate.py architect --brief <brief.md> --out <report.md>
    python3 debate.py architect --brief <brief.md> --out <report.md> --dry-run
    python3 debate.py architect --brief <b.md> --out <r.md> --seat claude:sonnet \\
        --seat codex:gpt-6-astra --max-calls 5

Exit codes:
    0  complete — every seat ok, judge ok
    1  degraded — quorum met, judge ok, at least one proposer not ok in some phase
    2  bad arguments: unknown vendor, roster over --max-calls, ``architect`` with a codex
       judge, an unreadable prompt/brief, a cwd that is not a repository, an unwritable
       --out, Claude seat isolation not configured, no deny rules; or a report write failed
    3  a vendor is unusable (not on PATH, not signed in, readiness timed out), or the hook
       canary tripped after a Claude call
    4  the judge call timed out and its process group was killed
    5  quorum not met after propose, or the judge's output was empty, invalid, or failed
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from string import ascii_uppercase
from typing import IO, Literal, NamedTuple

sys.path.insert(0, str(Path(__file__).parent))
from codex_seat import (  # noqa: E402
    NO_SUBAGENTS,
    RUN_LOG_SUFFIX,
    describe_argv,
    kill_and_wait,
    launch,
    resolve_executables,
)

Role = Literal["researcher", "architect"]
Phase = Literal["propose", "critique", "judge"]
CallOutcome = Literal["ok", "timeout", "empty", "invalid", "failed"]
Status = Literal["complete", "degraded", "failed"]


class Seat(NamedTuple):
    """One debate seat: which CLI, which model, and its anonymised label."""

    vendor: str
    model: str
    label: str


class CallResult(NamedTuple):
    """The classified outcome of one headless call and its one-line detail."""

    outcome: CallOutcome
    detail: str


#: The roles a debate can run for, in the order ``--help`` lists them.
ROLES: tuple[Role, ...] = ("researcher", "architect")

#: The phases, in the order they run.
PHASES: tuple[Phase, ...] = ("propose", "critique", "judge")

#: Default proposers (the human's decision, 2026-10-14): one top Codex seat, three Claude
#: seats. The lower GPT-6 tiers are not served on the Codex sign-in this setup uses (HTTP 400,
#: spike 2026-10-14); only the top tier answers. ``--seat`` still accepts any model id.
DEFAULT_CLAUDE_PROPOSER: str = "sonnet"
DEFAULT_CODEX_TOP: str = "gpt-6-astra"

#: The judge never proposes; for an architect debate it must not share the Codex
#: arch-reviewer's vendor.
DEFAULT_JUDGE: tuple[str, str] = ("claude", "fable")

DEFAULT_MAX_CALLS: int = 9
DEFAULT_TIMEOUT_SECONDS: int = 900

#: ``login status`` / ``auth status`` are local credential reads — they should answer at once.
READINESS_TIMEOUT_SECONDS: int = 30

#: Below two valid proposals from two vendors there is no debate to judge.
QUORUM_PROPOSALS: int = 2
QUORUM_VENDORS: int = 2

#: The Claude seat's whole tool list: read-only, no Bash/Write/Edit/Agent.
ROLE_TOOLS: dict[Role, str] = {
    "architect": "Read,Grep,Glob",
    "researcher": "WebSearch,WebFetch,Read",
}

#: Each call's prompt is written beside its output and fed to the CLI as stdin from the file.
PROMPT_SUFFIX: str = ".prompt"

#: Every seat's own output lands in ``<out>.d/``.
SEAT_DIR_SUFFIX: str = ".d"

#: The ``--setting-sources`` value under which ``claude -p`` runs no hook; ``None`` would
#: disable every debate with a Claude seat. Verified by the task 016 spike, 2026-10-14, on
#: claude 2.1.299 / codex-cli 0.154.0: with ``""`` all four checks passed — no new
#: ``claude-temp/sessions/`` entry, ``.git/info/exclude`` unchanged, the home project record
#: unchanged, and the canary read under ``~/.ssh`` refused by the deny rule. ``local`` also
#: passed but was rejected: it would load a future ``settings.local.json``, which can carry
#: hooks. Under ``""`` CLAUDE.md is not loaded; the home auto-memory index is. Re-verify on
#: every CLI upgrade.
CLAUDE_SETTING_SOURCES: str | None = ""

#: Shown for ``--setting-sources`` in a dry run while isolation is unverified; a live run
#: never reaches the Claude argv in that state (pre-flight refuses it).
UNVERIFIED_SOURCES: str = "<unverified>"

#: Headings each phase's output must carry exactly once, non-empty, checked in this order.
REQUIRED_SECTIONS: dict[Phase, tuple[str, ...]] = {
    "propose": ("## Proposal", "## Why this works", "## Risks and unknowns"),
    "critique": ("## Critique", "## Revised position"),
    "judge": (
        "## Answer",
        "## Why this over the alternatives",
        "## Dissent",
        "## Critique effect",
    ),
}

#: The role-specific heading appended to a phase's required list.
ROLE_SECTIONS: dict[tuple[Phase, Role], str] = {
    ("propose", "researcher"): "## Sources",
    ("judge", "researcher"): "## Sources",
    ("judge", "architect"): "## Claims to verify against the code",
}

#: The heading under which a critique names one sub-heading per other proposal.
CRITIQUE_HEADING: str = "## Critique"

#: Phase and role prompts live beside this script's directory, like ``.claude/codex``.
PROMPT_DIR: Path = Path(__file__).resolve().parent.parent / "debate"

#: The judge's label and output file stem.
JUDGE_LABEL: str = "judge"

#: Readiness subcommand per vendor (run on the resolved executable).
READINESS_ARGS: dict[str, tuple[str, ...]] = {
    "codex": ("login", "status"),
    "claude": ("auth", "status"),
}

#: Vendors whose CLI writes the final message to ``<out>`` itself (Codex's ``-o``); the
#: others print it on stdout and the script writes it.
WRITES_OUT: frozenset[str] = frozenset({"codex"})

#: The project-relative directory the SessionStart hook writes into — the canary's witness.
SESSIONS_DIR: Path = Path("claude-temp") / "sessions"

#: The exclude file the SessionStart hook edits — the canary's second witness.
EXCLUDE_FILE: Path = Path(".git") / "info" / "exclude"

#: Filesystems store mtimes coarsely (FAT: 2 s); a file written in the call's first instant
#: must not read as older than the call's start.
MTIME_SLACK_SECONDS: float = 2.0

#: How many report lines stdout echoes before the path.
ECHO_LINES: int = 24

#: Labels are single letters, so a roster holds at most this many proposers.
MAX_PROPOSERS: int = len(ascii_uppercase)

EXIT_DEGRADED: int = 1
EXIT_BAD_ARGUMENTS: int = 2
EXIT_VENDOR_UNUSABLE: int = 3
EXIT_JUDGE_TIMEOUT: int = 4
EXIT_NO_ANSWER: int = 5

#: Phase results keyed by phase, then by seat label.
Results = dict[Phase, dict[str, CallResult]]

#: How a debate ends: status, exit code, ``REASON:`` (failed only), and the report body.
Outcome = tuple[Status, int, str | None, str]

#: One phase's runner, closed over the run's context (see :func:`run_debate`).
PhaseCall = Callable[
    [Phase, Sequence[Seat], Mapping[str, str], Mapping[str, Sequence[str]]],
    dict[str, CallResult],
]

#: ``REASON:`` when the hook canary trips.
CANARY_REASON: str = "claude seat executed hooks — isolation broken"


def parse_seat(value: str) -> tuple[str, str]:
    """Parse one ``vendor:model`` argument (argparse ``type``).

    Args:
        value: The text given to ``--seat`` or ``--judge``.

    Returns:
        ``(vendor, model)``.

    Raises:
        argparse.ArgumentTypeError: The text is not ``vendor:model`` or the vendor is unknown.
    """
    vendor, sep, model = value.partition(":")
    if not sep or not model:
        raise argparse.ArgumentTypeError(f"invalid seat {value!r} — use vendor:model")
    if vendor not in VENDOR_ARGV:
        known = ", ".join(sorted(VENDOR_ARGV))
        raise argparse.ArgumentTypeError(f"unknown vendor {vendor!r} — known: {known}")
    return vendor, model


def default_proposers() -> list[tuple[str, str]]:
    """Return the default proposer roster: one top Codex seat and three Claude seats."""
    return [
        ("codex", DEFAULT_CODEX_TOP),
        ("claude", DEFAULT_CLAUDE_PROPOSER),
        ("claude", DEFAULT_CLAUDE_PROPOSER),
        ("claude", DEFAULT_CLAUDE_PROPOSER),
    ]


def vendor_ready(vendor: str, exe: Path, taskkill: Path | None = None) -> bool:
    """Report whether a vendor's CLI is signed in, via its resolved executable.

    Args:
        vendor: ``codex`` or ``claude``.
        exe: The resolved CLI path.
        taskkill: The resolved ``taskkill`` on Windows, for the group kill.

    Returns:
        True when the status command exits 0 within :data:`READINESS_TIMEOUT_SECONDS`.

    Raises:
        subprocess.TimeoutExpired: The status command passed its deadline (its group is
            killed first).
        OSError: The executable could not be launched.
    """
    process = launch(
        [str(exe), *READINESS_ARGS[vendor]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    try:
        return process.wait(timeout=READINESS_TIMEOUT_SECONDS) == 0
    except subprocess.TimeoutExpired:
        cleanup = kill_and_wait(process, taskkill)
        if cleanup is not None:
            print(f"{vendor} readiness cleanup failed: {cleanup}", file=sys.stderr)
        raise


def load_deny_rules(home: Path, project: Path) -> list[str]:
    """Collect ``permissions.deny`` from the user's and the project's ``settings.json``.

    Narrowing ``--setting-sources`` drops the deny backstop those files carry, so the rules
    travel with each Claude seat as ``--disallowedTools`` — verbatim, deduplicated in order.

    Args:
        home: The user's home directory (reads ``~/.claude/settings.json``).
        project: The project root (reads ``.claude/settings.json``).

    Returns:
        Every deny rule found; empty when neither file exists or neither has any.

    Raises:
        OSError: A settings file exists but cannot be read.
        ValueError: A settings file is not valid JSON.
    """
    rules: list[str] = []
    for settings in (home / ".claude" / "settings.json", project / ".claude" / "settings.json"):
        if not settings.is_file():
            continue
        data = json.loads(settings.read_text(encoding="utf-8"))
        permissions = data.get("permissions", {}) if isinstance(data, dict) else {}
        deny = permissions.get("deny", []) if isinstance(permissions, dict) else []
        for rule in deny:
            if isinstance(rule, str) and rule not in rules:
                rules.append(rule)
    return rules


def read_prompt_file(name: str) -> str:
    """Read one prompt file from :data:`PROMPT_DIR`.

    Args:
        name: The file stem (a phase or a role).

    Returns:
        The file's text, stripped.

    Raises:
        OSError: The file is missing or unreadable.
    """
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def build_prompt(
    phase: Phase,
    role: Role,
    brief: Path,
    proposals: Mapping[str, str],
    critiques: Mapping[str, str],
    own: str | None,
) -> str:
    """Compose one seat's prompt: phase contract, role, brief, then the anonymised inputs.

    Vendor and model names never appear — seats are ``Seat A``… so a critic judges content.

    Args:
        phase: The phase being run.
        role: The debate's role.
        brief: The brief file KASPER wrote, passed verbatim.
        proposals: Valid proposals by label (critique and judge phases).
        critiques: Valid critiques by label (judge phase only).
        own: The critic's own label, marked ``(yours)``; ``None`` otherwise.

    Returns:
        The prompt text, sent on stdin.

    Raises:
        OSError: A prompt file or the brief is unreadable.
    """
    parts = [
        read_prompt_file(phase),
        f"## Role: {role}\n\n{read_prompt_file(role)}",
        f"## Brief\n\n{brief.read_text(encoding='utf-8').strip()}",
    ]
    if phase != "propose":
        sections = [
            f"### Seat {label}{' (yours)' if label == own else ''}\n\n{text.strip()}"
            for label, text in sorted(proposals.items())
        ]
        parts.append("## Proposals\n\n" + "\n\n".join(sections))
    if phase == "judge":
        sections = [
            f"### Seat {label} on the proposals\n\n{text.strip()}"
            for label, text in sorted(critiques.items())
        ]
        parts.append("## Critiques\n\n" + "\n\n".join(sections))
    return "\n\n".join(parts) + "\n"


def claude_argv(
    exe: Path, seat: Seat, role: Role, out: Path, deny: Sequence[str] = ()
) -> list[str]:
    """Build a read-only, hook-isolated ``claude -p`` call (prompt on stdin, never argv).

    Args:
        exe: The resolved ``claude`` executable.
        seat: The seat to run.
        role: Selects the tool list (:data:`ROLE_TOOLS`).
        out: Unused — Claude prints its answer; kept so every vendor shares one signature.
        deny: ``permissions.deny`` rules, passed verbatim as ``--disallowedTools``.

    Returns:
        The argv.
    """
    sources = CLAUDE_SETTING_SOURCES if CLAUDE_SETTING_SOURCES is not None else UNVERIFIED_SOURCES
    argv = [
        str(exe),
        "-p",
        "--model",
        seat.model,
        "--output-format",
        "text",
        "--permission-mode",
        "dontAsk",
        "--tools",
        ROLE_TOOLS[role],
        "--no-session-persistence",
        "--setting-sources",
        sources,
    ]
    # Last, because the flag takes a variadic list: nothing after it can be swallowed.
    if deny:
        argv += ["--disallowedTools", *deny]
    return argv


def codex_argv(exe: Path, seat: Seat, role: Role, out: Path, deny: Sequence[str] = ()) -> list[str]:
    """Build a read-only ``codex exec`` call (prompt on stdin; final message at ``out``).

    Args:
        exe: The resolved ``codex`` executable.
        seat: The seat to run.
        role: Unused — Codex's sandbox is the same for both roles; one shared signature.
        out: Where Codex writes its final message (``-o``).
        deny: Unused — the read-only sandbox is Codex's backstop.

    Returns:
        The argv.
    """
    return [
        str(exe),
        "exec",
        *NO_SUBAGENTS,
        "--ephemeral",
        "--sandbox",
        "read-only",
        "-m",
        seat.model,
        "-o",
        str(out),
    ]


#: The third-vendor seam: vendor name → argv builder. A new vendor is one function, one
#: entry here, and one line in :data:`READINESS_ARGS`.
VENDOR_ARGV: dict[str, Callable[[Path, Seat, Role, Path, Sequence[str]], list[str]]] = {
    "claude": claude_argv,
    "codex": codex_argv,
}


def section_bodies(text: str) -> list[tuple[str, list[str]]]:
    """Split text at lines beginning ``## `` into ``(heading, body lines)`` pairs.

    Args:
        text: A seat's output.

    Returns:
        Every ``## `` section in order; text before the first heading is dropped.
    """
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append((line.rstrip(), []))
        elif sections:
            sections[-1][1].append(line)
    return sections


def validate_output(text: str, phase: Phase, role: Role, others: Sequence[str] = ()) -> str | None:
    """Check a seat's output carries every mandatory section exactly once, non-empty.

    Args:
        text: The seat's output.
        phase: The phase it answered.
        role: The debate's role (adds its role-specific heading).
        others: Critique phase only — the labels of the other valid proposals, each of which
            needs a ``### Seat <L>`` sub-heading under ``## Critique``.

    Returns:
        ``None`` when valid, else the first offending heading, in table order.
    """
    required = list(REQUIRED_SECTIONS[phase])
    extra = ROLE_SECTIONS.get((phase, role))
    if extra is not None:
        required.append(extra)
    sections = section_bodies(text)
    for heading in required:
        matches = [body for found, body in sections if found == heading]
        if len(matches) != 1 or not any(line.strip() for line in matches[0]):
            return heading
    if phase == "critique":
        body = next(body for found, body in sections if found == CRITIQUE_HEADING)
        stripped = {line.strip() for line in body}
        for label in others:
            if f"### Seat {label}" not in stripped:
                return f"### Seat {label}"
    return None


def classify_output(out: Path, started: float, validate: Callable[[str], str | None]) -> CallResult:
    """Classify a call that exited 0 by the freshness and shape of its output file.

    Args:
        out: The call's output file.
        started: Wall-clock time just before the call launched.
        validate: Returns the first offending heading, or ``None``.

    Returns:
        ``empty``, ``invalid``, or ``ok`` with its detail (elapsed seconds for ``ok``).
    """
    try:
        if out.stat().st_mtime < started - MTIME_SLACK_SECONDS:
            return CallResult("empty", "stale output")
        text = out.read_text(encoding="utf-8")
    except OSError:
        return CallResult("empty", "no output")
    if not text.strip():
        return CallResult("empty", "no output")
    missing = validate(text)
    if missing is not None:
        return CallResult("invalid", f"missing `{missing}`")
    return CallResult("ok", f"{round(time.time() - started)}s")


def run_call(
    argv: Sequence[str],
    prompt: str,
    out: Path,
    log: Path,
    timeout: int,
    writes_out: bool,
    validate: Callable[[str], str | None],
    taskkill: Path | None = None,
) -> CallResult:
    """Run one headless call with the prompt on stdin, in its own process group.

    The call's ``out`` and ``log`` are deleted first, so only what this call writes can
    certify it. No pipe is used: stdin is ``<out>.prompt``, stdout and stderr go to files,
    and the call is awaited with ``wait`` — a surviving descendant can never block the
    debate. Outcomes are classified in a fixed order: launch error → ``failed``;
    timeout → ``timeout`` (group killed, bounded wait); non-zero exit → ``failed`` whatever the
    file holds; missing/blank/stale file → ``empty``; a bad section → ``invalid``; else ``ok``.

    Args:
        argv: The resolved command.
        prompt: The prompt, written to ``<out>.prompt`` and fed as stdin.
        out: The call's output file.
        log: The call's transcript file.
        timeout: Seconds before the process group is killed.
        writes_out: True when the CLI writes ``out`` itself; else stdout is written there.
        validate: Returns the first offending heading of a text, or ``None``.
        taskkill: The resolved ``taskkill`` on Windows, for the group kill.

    Returns:
        The classified :class:`CallResult`.

    Raises:
        OSError: ``out``/``log`` could not be prepared or written.
    """
    out.unlink(missing_ok=True)
    log.unlink(missing_ok=True)
    # The prompt goes in through a file, not a pipe: no writer thread, nothing to block on.
    prompt_file = Path(f"{out}{PROMPT_SUFFIX}")
    prompt_file.write_text(prompt, encoding="utf-8")
    started = time.time()
    with ExitStack() as files:
        stdin = files.enter_context(prompt_file.open(encoding="utf-8"))
        transcript = files.enter_context(log.open("w", encoding="utf-8"))
        # Claude prints its answer on stdout, so stdout goes straight to the output file;
        # Codex writes the answer itself (-o), so its stdout is chatter for the transcript.
        if writes_out:
            stdout: IO[str] = transcript
            stderr: int | IO[str] = subprocess.STDOUT
        else:
            stdout = files.enter_context(out.open("w", encoding="utf-8"))
            stderr = transcript
        try:
            process = launch(argv, stdout=stdout, stderr=stderr, stdin=stdin)
        except OSError as exc:
            return CallResult("failed", str(exc))
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            cleanup = kill_and_wait(process, taskkill)
            detail = f"{timeout}s" if cleanup is None else f"{timeout}s; cleanup failed: {cleanup}"
            return CallResult("timeout", detail)
    if returncode != 0:
        return CallResult("failed", f"exit {returncode}")
    return classify_output(out, started, validate)


def hook_canary_snapshot(project: Path) -> tuple[frozenset[str], float | None]:
    """Capture what the SessionStart hook would change: session dirs and the exclude mtime.

    Args:
        project: The project root.

    Returns:
        The names under ``claude-temp/sessions/`` and ``.git/info/exclude``'s mtime
        (``None`` when absent).
    """
    sessions = project / SESSIONS_DIR
    names = frozenset(p.name for p in sessions.iterdir()) if sessions.is_dir() else frozenset()
    exclude = project / EXCLUDE_FILE
    mtime = exclude.stat().st_mtime if exclude.is_file() else None
    return names, mtime


def hook_canary_tripped(
    before: tuple[frozenset[str], float | None], after: tuple[frozenset[str], float | None]
) -> bool:
    """Report whether a Claude call ran a hook: a new session dir or a touched exclude file.

    Args:
        before: The snapshot taken just before the call.
        after: The snapshot taken just after it.

    Returns:
        True when a session name appeared or the exclude file's mtime changed.
    """
    return bool(after[0] - before[0]) or after[1] != before[1]


def run_phase(
    phase: Phase,
    role: Role,
    seats: Sequence[Seat],
    prompts: Mapping[str, str],
    others: Mapping[str, Sequence[str]],
    seat_dir: Path,
    exes: Mapping[str, Path],
    deny: Sequence[str],
    timeout: int,
    taskkill: Path | None,
    project: Path,
    stop: threading.Event,
) -> dict[str, CallResult]:
    """Run one phase: one sequential lane per vendor, the lanes concurrent.

    A Claude call is bracketed by the hook canary; a trip sets ``stop`` and no lane starts
    another call. Seats never reached are absent from the result.

    Args:
        phase: The phase being run.
        role: The debate's role.
        seats: The seats taking part, in roster order.
        prompts: Each seat's prompt by label.
        others: Critique only — each critic's other valid proposal labels.
        seat_dir: ``<out>.d/``.
        exes: The resolved CLI per vendor.
        deny: Deny rules for Claude seats.
        timeout: Per-call seconds.
        taskkill: The resolved ``taskkill`` on Windows.
        project: The project root (canary witness).
        stop: Set when the canary trips; checked before every call.

    Returns:
        The result of every call made, by label.
    """
    lanes: dict[str, list[Seat]] = {}
    for seat in seats:
        lanes.setdefault(seat.vendor, []).append(seat)

    def run_lane(lane: list[Seat]) -> dict[str, CallResult]:
        done: dict[str, CallResult] = {}
        for seat in lane:
            if stop.is_set():
                break
            stem = JUDGE_LABEL if phase == "judge" else f"{phase}-{seat.label}"
            out = seat_dir / f"{stem}.md"
            argv = VENDOR_ARGV[seat.vendor](exes[seat.vendor], seat, role, out, deny)
            seat_others = others.get(seat.label, ())

            def validate(text: str, seat_others: Sequence[str] = seat_others) -> str | None:
                return validate_output(text, phase, role, seat_others)

            before = hook_canary_snapshot(project) if seat.vendor == "claude" else None
            done[seat.label] = run_call(
                argv,
                prompts[seat.label],
                out,
                Path(f"{out}{RUN_LOG_SUFFIX}"),
                timeout,
                seat.vendor in WRITES_OUT,
                validate,
                taskkill,
            )
            if before is not None and hook_canary_tripped(before, hook_canary_snapshot(project)):
                stop.set()
                break
        return done

    results: dict[str, CallResult] = {}
    with ThreadPoolExecutor(max_workers=max(len(lanes), 1)) as pool:
        for done in pool.map(run_lane, lanes.values()):
            results.update(done)
    return results


def seat_detail(result: CallResult | None) -> str:
    """Render one phase outcome for ``## Seats``: ``ok (212s)``, ``timeout (900s)``, ``not run``."""
    if result is None:
        return "not run"
    return f"{result.outcome} ({result.detail})"


def write_report(
    out: Path,
    role: Role,
    status: Status,
    seats: Sequence[Seat],
    results: Results,
    calls_made: int,
    calls_cap: int,
    reason: str | None,
    body: str,
) -> None:
    """Write the report: the script's header lines, the body, then the seat mapping.

    Args:
        out: The report path.
        role: The debate's role.
        status: ``complete``, ``degraded``, or ``failed``.
        seats: Proposers in roster order, then the judge (label :data:`JUDGE_LABEL`).
        results: Every call's result, by phase then label.
        calls_made: Calls launched.
        calls_cap: ``--max-calls``.
        reason: The one-line failure reason; required when ``status`` is ``failed``.
        body: The judge's answer, or the failure body.

    Raises:
        OSError: The report could not be written.
    """
    proposers = [seat for seat in seats if seat.label != JUDGE_LABEL]
    judge = next(seat for seat in seats if seat.label == JUDGE_LABEL)
    proposed = sum(r.outcome == "ok" for r in results.get("propose", {}).values())
    critiqued = sum(r.outcome == "ok" for r in results.get("critique", {}).values())
    judge_result = results.get("judge", {}).get(JUDGE_LABEL)
    judge_state = judge_result.outcome if judge_result else "not run"
    total = len(proposers)
    lines = [
        f"DEBATE: {status}",
        f"ROLE: {role}",
        f"SEATS: proposed {proposed}/{total} · critiqued {critiqued}/{total}"
        f" · judge {judge.vendor}:{judge.model} {judge_state}",
        f"CALLS: {calls_made}/{calls_cap}",
    ]
    if status == "failed":
        lines.append(f"REASON: {reason}")
    lines += ["", body.strip(), "", "## Seats"]
    for seat in proposers:
        propose = seat_detail(results.get("propose", {}).get(seat.label))
        critique = seat_detail(results.get("critique", {}).get(seat.label))
        lines.append(
            f"- {seat.label} {seat.vendor}:{seat.model} — propose {propose} · critique {critique}"
        )
    lines.append(f"- {JUDGE_LABEL} {judge.vendor}:{judge.model} — {seat_detail(judge_result)}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def failure_body(seat_dir: Path) -> str:
    """Body of a failed report: the no-answer line and the seat files that exist."""
    files = sorted(p.name for p in seat_dir.glob("*.md")) if seat_dir.is_dir() else []
    listing = "\n".join(f"- {seat_dir / name}" for name in files) or "- (none)"
    return f"No answer: see REASON.\n\nSeat files:\n{listing}"


def probe_out(out: Path, seat_dir: Path) -> None:
    """Prepare ``--out``: create ``<out>.d/``, truncate the report, clear old per-call files.

    Args:
        out: The report path.
        seat_dir: ``<out>.d/``.

    Raises:
        OSError: Any of it failed.
    """
    seat_dir.mkdir(parents=True, exist_ok=True)
    # A leftover report from an earlier run must never certify this one.
    with out.open("w", encoding="utf-8"):
        pass
    for pattern in ("propose-*.md", "critique-*.md", f"{JUDGE_LABEL}.md"):
        for stale in seat_dir.glob(pattern):
            stale.unlink()
        for suffix in (RUN_LOG_SUFFIX, PROMPT_SUFFIX):
            for stale in seat_dir.glob(pattern + suffix):
                stale.unlink()


def print_plan(
    role: Role,
    proposers: Sequence[Seat],
    judge: Seat,
    brief: Path,
    seat_dir: Path,
    deny: Sequence[str],
    critique: bool,
) -> None:
    """Print every planned call, phase by phase, without running or writing anything.

    Each line is the ``describe_argv`` form: the argv, then an excerpt of the prompt that
    goes on **stdin** (never on argv), its length in brackets. Unresolved CLIs show their
    bare name, with a note on stderr.

    Raises:
        OSError: A prompt file or the brief is unreadable.
    """
    vendors = {seat.vendor for seat in [*proposers, judge]}
    exes = resolve_executables(vendors)
    for vendor in sorted(vendors - exes.keys()):
        print(f"note: {vendor} is not on PATH — a live run exits 3", file=sys.stderr)
    if CLAUDE_SETTING_SOURCES is None and "claude" in vendors:
        print(
            "note: debates disabled — Claude seat isolation not verified; a live run exits 2",
            file=sys.stderr,
        )
    labels = [seat.label for seat in proposers]
    placeholders = {label: f"<Seat {label}'s proposal, at run time>" for label in labels}
    critiques = {label: f"<Seat {label}'s critique, at run time>" for label in labels}
    phases: list[tuple[Phase, Sequence[Seat]]] = [("propose", proposers)]
    if critique:
        phases.append(("critique", proposers))
    phases.append(("judge", [judge]))
    for phase, seats in phases:
        for seat in seats:
            stem = JUDGE_LABEL if phase == "judge" else f"{phase}-{seat.label}"
            out = seat_dir / f"{stem}.md"
            exe = exes.get(seat.vendor, Path(seat.vendor))
            argv = VENDOR_ARGV[seat.vendor](exe, seat, role, out, deny)
            own = seat.label if phase == "critique" else None
            prompt = build_prompt(
                phase,
                role,
                brief,
                placeholders,
                critiques if critique else {},
                own,
            )
            tag = phase if phase == "judge" else f"{phase} {seat.label}"
            print(f"[{tag}] {describe_argv([*argv, prompt])}")


def readiness_failure(
    vendors: set[str], taskkill: Path | None
) -> tuple[dict[str, Path], str | None]:
    """Resolve and check every vendor; return the paths and the first failure reason, if any.

    Args:
        vendors: The vendors in the roster.
        taskkill: The resolved ``taskkill`` on Windows.

    Returns:
        ``(exes, reason)``; ``reason`` is ``None`` when every vendor is ready.
    """
    exes = resolve_executables(vendors)
    for vendor in sorted(vendors):
        if vendor not in exes:
            return exes, f"vendor {vendor} not resolved"
        try:
            if not vendor_ready(vendor, exes[vendor], taskkill):
                return exes, f"vendor {vendor} not signed in"
        except subprocess.TimeoutExpired:
            return exes, f"vendor {vendor} readiness timed out after {READINESS_TIMEOUT_SECONDS}s"
        except OSError as exc:
            return exes, f"vendor {vendor} not launchable: {exc}"
    return exes, None


def failed(code: int, reason: str, seat_dir: Path) -> Outcome:
    """A ``failed`` outcome whose body lists the seat files that exist."""
    return "failed", code, reason, failure_body(seat_dir)


def propose_phase(
    call: PhaseCall,
    role: Role,
    brief: Path,
    seats: Sequence[Seat],
    seat_dir: Path,
    stop: threading.Event,
) -> dict[str, str] | Outcome:
    """Run the propose phase and check the canary and quorum.

    Returns:
        The valid proposals' texts by label, or the failed :data:`Outcome` that ends the debate.

    Raises:
        OSError: A prompt, the brief, or a seat file is unreadable.
    """
    prompt = build_prompt("propose", role, brief, {}, {}, None)
    results = call("propose", seats, {seat.label: prompt for seat in seats}, {})
    if stop.is_set():
        return failed(EXIT_VENDOR_UNUSABLE, CANARY_REASON, seat_dir)
    ok_seats = [s for s in seats if s.label in results and results[s.label].outcome == "ok"]
    ok_vendors = {seat.vendor for seat in ok_seats}
    if len(ok_seats) < QUORUM_PROPOSALS or len(ok_vendors) < QUORUM_VENDORS:
        reason = f"{len(ok_seats)} valid proposals from {len(ok_vendors)} vendors"
        return failed(EXIT_NO_ANSWER, reason, seat_dir)
    return {
        seat.label: (seat_dir / f"propose-{seat.label}.md").read_text(encoding="utf-8")
        for seat in ok_seats
    }


def critique_phase(
    call: PhaseCall,
    role: Role,
    brief: Path,
    seats: Sequence[Seat],
    proposals: Mapping[str, str],
    seat_dir: Path,
    stop: threading.Event,
) -> dict[str, str] | Outcome:
    """Run the critique phase for every proposer whose proposal is valid.

    Returns:
        The valid critiques' texts by label, or the failed :data:`Outcome` of a canary trip.

    Raises:
        OSError: A prompt, the brief, or a seat file is unreadable.
    """
    critics = [seat for seat in seats if seat.label in proposals]
    labels = [seat.label for seat in critics]
    results = call(
        "critique",
        critics,
        {s.label: build_prompt("critique", role, brief, proposals, {}, s.label) for s in critics},
        {label: [other for other in labels if other != label] for label in labels},
    )
    if stop.is_set():
        return failed(EXIT_VENDOR_UNUSABLE, CANARY_REASON, seat_dir)
    return {
        label: (seat_dir / f"critique-{label}.md").read_text(encoding="utf-8")
        for label, result in results.items()
        if result.outcome == "ok"
    }


def judge_phase(
    call: PhaseCall,
    role: Role,
    brief: Path,
    judge: Seat,
    proposals: Mapping[str, str],
    critiques: Mapping[str, str],
    seat_dir: Path,
    stop: threading.Event,
) -> str | Outcome:
    """Run the judge and map its outcome: timeout → exit 4, any other non-ok → exit 5.

    Returns:
        The judge's answer, or the failed :data:`Outcome`.

    Raises:
        OSError: A prompt, the brief, or the judge's file is unreadable.
    """
    prompt = build_prompt("judge", role, brief, proposals, critiques, None)
    verdict = call("judge", [judge], {JUDGE_LABEL: prompt}, {})[JUDGE_LABEL]
    if stop.is_set():
        return failed(EXIT_VENDOR_UNUSABLE, CANARY_REASON, seat_dir)
    if verdict.outcome == "timeout":
        return failed(EXIT_JUDGE_TIMEOUT, f"judge timed out after {verdict.detail}", seat_dir)
    if verdict.outcome == "invalid":
        return failed(EXIT_NO_ANSWER, f"judge output {verdict.detail}", seat_dir)
    if verdict.outcome != "ok":
        return failed(EXIT_NO_ANSWER, f"judge {verdict.outcome}: {verdict.detail}", seat_dir)
    return (seat_dir / f"{JUDGE_LABEL}.md").read_text(encoding="utf-8")


def run_phases(
    call: PhaseCall,
    role: Role,
    brief: Path,
    seats: Sequence[Seat],
    judge: Seat,
    results: Results,
    seat_dir: Path,
    stop: threading.Event,
    critique: bool,
) -> Outcome:
    """Run propose → critique → judge and decide complete, degraded, or failed.

    Returns:
        The debate's :data:`Outcome`.
    """
    try:
        proposals = propose_phase(call, role, brief, seats, seat_dir, stop)
        if isinstance(proposals, tuple):
            return proposals
        critiques: dict[str, str] = {}
        if critique:
            reviewed = critique_phase(call, role, brief, seats, proposals, seat_dir, stop)
            if isinstance(reviewed, tuple):
                return reviewed
            critiques = reviewed
        answer = judge_phase(call, role, brief, judge, proposals, critiques, seat_dir, stop)
    except OSError as exc:
        return failed(EXIT_BAD_ARGUMENTS, f"seat file error: {exc}", seat_dir)
    if isinstance(answer, tuple):
        return answer
    phases: tuple[Phase, ...] = ("propose", "critique") if critique else ("propose",)
    every_ok = all(
        len(results.get(phase, {})) == len(seats)
        and all(r.outcome == "ok" for r in results.get(phase, {}).values())
        for phase in phases
    )
    if every_ok:
        return "complete", 0, None, answer
    return "degraded", EXIT_DEGRADED, None, answer


def finish_report(
    out: Path,
    role: Role,
    seats: Sequence[Seat],
    results: Results,
    max_calls: int,
    seat_dir: Path,
    outcome: Outcome,
) -> int:
    """Write the report for ``outcome`` and echo its head; a write failure is exit 2.

    Returns:
        The outcome's exit code, or :data:`EXIT_BAD_ARGUMENTS` when the report write failed.
    """
    status, code, reason, body = outcome
    calls = sum(len(phase) for phase in results.values())
    try:
        write_report(out, role, status, seats, results, calls, max_calls, reason, body)
    except OSError as exc:
        print(f"cannot write the report {out}: {exc} — seat files: {seat_dir}", file=sys.stderr)
        return EXIT_BAD_ARGUMENTS
    echo_report(out)
    return code


def preflight(
    role: Role,
    brief: Path,
    proposers: Sequence[tuple[str, str]],
    judge: tuple[str, str],
    max_calls: int,
    critique: bool,
    dry_run: bool,
    project: Path,
) -> tuple[list[Seat], Seat, list[str]] | int:
    """Pre-flight steps 1–2: repository, roster, cap, prompts, isolation, deny rules.

    Returns:
        ``(proposer seats, judge seat, deny rules)``, or the exit code to stop with
        (nothing written).
    """
    problem: str | None = None
    planned = (2 * len(proposers) if critique else len(proposers)) + 1
    if not (project / ".git").is_dir():
        problem = f"{project} is not a repository — run from the project root."
    elif role == "architect" and judge[0] == "codex":
        problem = "an architect debate cannot have a codex judge — the arch-reviewer is codex"
    elif not proposers or len(proposers) > MAX_PROPOSERS:
        problem = f"a roster needs 1–{MAX_PROPOSERS} proposers"
    elif planned > max_calls:
        problem = f"roster needs {planned} calls, over --max-calls {max_calls}"
    if problem is None:
        try:
            for name in (*PHASES, role):
                read_prompt_file(name)
            brief.read_text(encoding="utf-8")
        except OSError as exc:
            problem = f"cannot read a prompt or the brief: {exc}"
    seats = [Seat(v, m, label) for (v, m), label in zip(proposers, ascii_uppercase, strict=False)]
    judge_seat = Seat(judge[0], judge[1], JUDGE_LABEL)
    deny: list[str] = []
    if problem is None and "claude" in {seat.vendor for seat in [*seats, judge_seat]}:
        if CLAUDE_SETTING_SOURCES is None and not dry_run:
            problem = "debates disabled: Claude seat isolation not verified"
        else:
            try:
                deny = load_deny_rules(Path.home(), project)
            except (OSError, ValueError) as exc:
                problem = f"cannot read permissions.deny: {exc}"
            if problem is None and not deny:
                problem = "no sensitive-read denies to pass to the Claude seat"
    if problem is not None:
        print(problem, file=sys.stderr)
        return EXIT_BAD_ARGUMENTS
    return seats, judge_seat, deny


def phase_runner(
    role: Role,
    results: Results,
    seat_dir: Path,
    exes: Mapping[str, Path],
    deny: Sequence[str],
    timeout: int,
    taskkill: Path | None,
    project: Path,
    stop: threading.Event,
) -> PhaseCall:
    """Close :func:`run_phase` over one run's context, recording each phase into ``results``.

    Returns:
        A :data:`PhaseCall` taking ``(phase, seats, prompts, others)``.
    """

    def call(
        phase: Phase,
        seats: Sequence[Seat],
        prompts: Mapping[str, str],
        others: Mapping[str, Sequence[str]],
    ) -> dict[str, CallResult]:
        results[phase] = run_phase(
            phase,
            role,
            seats,
            prompts,
            others,
            seat_dir,
            exes,
            deny,
            timeout,
            taskkill,
            project,
            stop,
        )
        return results[phase]

    return call


def run_debate(
    role: Role,
    brief: Path,
    out: Path,
    proposers: Sequence[tuple[str, str]],
    judge: tuple[str, str],
    max_calls: int,
    timeout: int,
    dry_run: bool,
    critique: bool = True,
) -> int:
    """Run pre-flight, then propose → critique → judge, and write the report.

    Args:
        role: ``researcher`` or ``architect``.
        brief: The brief file.
        out: The report path; seat files go to ``<out>.d/``.
        proposers: ``(vendor, model)`` per proposer, in roster order.
        judge: ``(vendor, model)`` of the judge.
        max_calls: The call cap, checked before any call.
        timeout: Per-call seconds.
        dry_run: Print the plan and write nothing.
        critique: False skips the critique phase (``--no-critique``, measurement only).

    Returns:
        A process exit code as documented in the module docstring.
    """
    project = Path.cwd()
    checked = preflight(role, brief, proposers, judge, max_calls, critique, dry_run, project)
    if isinstance(checked, int):
        return checked
    seats, judge_seat, deny = checked
    seat_dir = Path(f"{out}{SEAT_DIR_SUFFIX}")
    if dry_run:
        try:
            print_plan(role, seats, judge_seat, brief, seat_dir, deny, critique)
        except OSError as exc:
            print(f"cannot read a prompt or the brief: {exc}", file=sys.stderr)
            return EXIT_BAD_ARGUMENTS
        return 0
    try:
        probe_out(out, seat_dir)
    except OSError as exc:
        print(f"cannot prepare {out}: {exc}", file=sys.stderr)
        return EXIT_BAD_ARGUMENTS

    # From here on, every exit writes a report (the spec's "one contract").
    all_seats = [*seats, judge_seat]
    results: Results = {}
    taskkill_path = shutil.which("taskkill") if sys.platform == "win32" else None
    taskkill = Path(taskkill_path) if taskkill_path else None
    if sys.platform == "win32" and taskkill is None:
        # Without taskkill a timeout could kill only the shim and leave the seat running.
        outcome = failed(EXIT_VENDOR_UNUSABLE, "taskkill not resolved", seat_dir)
        return finish_report(out, role, all_seats, results, max_calls, seat_dir, outcome)
    exes, reason = readiness_failure({seat.vendor for seat in all_seats}, taskkill)
    if reason is not None:
        outcome = failed(EXIT_VENDOR_UNUSABLE, reason, seat_dir)
        return finish_report(out, role, all_seats, results, max_calls, seat_dir, outcome)

    stop = threading.Event()
    call = phase_runner(role, results, seat_dir, exes, deny, timeout, taskkill, project, stop)
    outcome = run_phases(call, role, brief, seats, judge_seat, results, seat_dir, stop, critique)
    return finish_report(out, role, all_seats, results, max_calls, seat_dir, outcome)


def echo_report(out: Path) -> None:
    """Print the report's head — header lines and the first lines of the answer."""
    try:
        lines = out.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    print("\n".join(lines[:ECHO_LINES]))


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line for :func:`main`."""
    parser = argparse.ArgumentParser(
        description="Run a KASPER debate: propose → critique → judge across two vendors."
    )
    parser.add_argument("role", choices=ROLES, help="The role the debate answers for.")
    parser.add_argument(
        "--brief", type=Path, required=True, help="Brief file, relative to the project root."
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Report path; every seat's output and transcript land in <out>.d/.",
    )
    parser.add_argument(
        "--seat",
        type=parse_seat,
        action="append",
        default=None,
        metavar="VENDOR:MODEL",
        help="A proposer; repeatable, replaces the default roster.",
    )
    parser.add_argument(
        "--judge",
        type=parse_seat,
        default=DEFAULT_JUDGE,
        metavar="VENDOR:MODEL",
        help=f"The judge (default: {':'.join(DEFAULT_JUDGE)}); never codex for architect.",
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        default=DEFAULT_MAX_CALLS,
        help=f"Call cap, checked before any call (default: {DEFAULT_MAX_CALLS}).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Seconds per call before its process group is killed"
        f" (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--no-critique",
        action="store_true",
        help="Skip the critique phase — for the critique measurement only.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print every planned call and write nothing."
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Run the requested debate, always naming the report path last."""
    args = parse_args(argv)
    role: Role = args.role  # argparse `choices` guarantees one of the ROLES literals
    proposers: list[tuple[str, str]] = args.seat if args.seat else default_proposers()
    try:
        return run_debate(
            role,
            args.brief,
            args.out,
            proposers,
            args.judge,
            args.max_calls,
            args.timeout,
            args.dry_run,
            critique=not args.no_critique,
        )
    finally:
        print(args.out)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
