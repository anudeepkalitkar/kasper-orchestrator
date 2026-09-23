#!/usr/bin/env python3
"""Run one KASPER verification seat on the OpenAI Codex CLI (ADR-0009).

The tester, code-reviewer, and architecture-reviewer seats are not Claude subagents:
KASPER invokes this script as a single Bash call, and Codex does the work headless.

- ``tester`` authors tests and runs the gate (workspace-write sandbox; ``GATE:`` verdict).
- ``reviewer`` judges the diff with ``codex exec review``; its findings are data.
- ``arch-reviewer`` judges a design document read-only with plain ``codex exec``.

The seat's discipline lives in ``.claude/codex/<seat>.md``; the task-specific brief is a
file KASPER writes. Each ``--stack <name>`` appends ``.claude/codex/stacks/<name>.md``
between the two, so a seat carries the conventions of the stack under verification.

Codex's own exit code is not a verdict — a red gate also exits 0 — so the tester
seat is judged from the first line of its captured report, which the role prompt
requires to be ``GATE: green`` or ``GATE: red``.

Codex's own chatter (banner, echoed prompt, progress) is tens of kilobytes per run
and KASPER pays for every byte of it, so it goes to ``<out>.log`` — overwritten each
run, kept as the evidence trail — and stdout carries only the seat's final message
followed by the report path.

The project under verification is the **working directory the seat is run from** —
never the script's own location, which for an installed copy is ``~/.claude``. KASPER
runs the seat from the project root; ``--brief`` and ``--out`` are read and written
relative to it, and a working directory with no ``.git`` is refused rather than
verified by accident.

Usage:
    python3 codex_seat.py tester   --brief <brief.md> --out <report.md>
    python3 codex_seat.py reviewer --brief <brief.md> --out <review.md> --dry-run
    python3 codex_seat.py arch-reviewer --stack python --brief <brief.md> --out <review.md>

Exit codes:
    0  tester: the gate is green · reviewer: Codex completed its turn ·
       arch-reviewer: Codex wrote a non-empty report
    1  tester: the gate is red
    2  bad arguments, an unreadable brief/role/stack prompt, an unusable --out path, or
       a working directory that is not a repository
    3  Codex is unusable — not signed in, or not on PATH
    4  the run exceeded --timeout and was killed
    5  tester: Codex finished but wrote no parseable ``GATE:`` line ·
       arch-reviewer: Codex finished but its report is empty or absent
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

Seat = Literal["tester", "reviewer", "arch-reviewer"]

#: The seats in the order ``--help`` lists them; must match :data:`Seat`.
SEATS: tuple[Seat, ...] = ("tester", "reviewer", "arch-reviewer")

#: Beside this script's own directory, so the installed copy in ``~/.claude/scripts``
#: reads ``~/.claude/codex`` while the repository copy reads the repository's.
ROLE_PROMPT_DIR: Path = Path(__file__).resolve().parent.parent / "codex"

#: Stack prompts live one level below the role prompts.
STACK_PROMPT_DIR: Path = ROLE_PROMPT_DIR / "stacks"

#: A stack name is a bare slug, so ``--stack`` can never smuggle in a path.
STACK_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9-]*$")

#: A verification pass can legitimately take a long time; a stuck one must not hang KASPER.
DEFAULT_TIMEOUT_SECONDS: int = 1800

#: ``codex login status`` is a local credential read — it should answer instantly.
LOGIN_TIMEOUT_SECONDS: int = 30

#: How much of the prompt ``--dry-run`` echoes before abbreviating it.
DRY_RUN_PROMPT_CHARS: int = 80

#: The exact opener the role prompt demands of a tester report's first line.
GATE_PREFIX: str = "GATE: "

#: Codex's transcript lands beside the report under this suffix.
RUN_LOG_SUFFIX: str = ".log"

#: A tester report is ``GATE:`` plus at most ten lines by contract.
TESTER_ECHO_LINES: int = 11

#: Ranked findings are longer; echo enough to act on and leave the rest in the file.
REVIEWER_ECHO_LINES: int = 60

EXIT_GATE_RED: int = 1
EXIT_BAD_ARGUMENTS: int = 2
EXIT_CODEX_UNUSABLE: int = 3
EXIT_TIMEOUT: int = 4
EXIT_NO_VERDICT: int = 5


def stack_name(value: str) -> str:
    """Validate one ``--stack`` argument as a bare slug.

    Args:
        value: The name given on the command line.

    Returns:
        The name unchanged.

    Raises:
        argparse.ArgumentTypeError: The name does not match :data:`STACK_NAME_PATTERN`.
    """
    if not STACK_NAME_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"invalid stack name {value!r} — use lowercase letters, digits and hyphens"
        )
    return value


def build_prompt(seat: Seat, brief: Path, stacks: Sequence[str] = ()) -> str:
    """Compose the prompt for a seat: its role prompt, any stack prompts, then the brief.

    Args:
        seat: Which seat's role prompt to load from :data:`ROLE_PROMPT_DIR`.
        brief: The task-specific brief KASPER wrote.
        stacks: Stack names whose prompts from :data:`STACK_PROMPT_DIR` are appended in
            order, each under a ``## Stack: <name>`` heading.

    Returns:
        The full prompt text to hand to Codex.

    Raises:
        OSError: The role prompt, a stack prompt, or the brief could not be read.
    """
    parts = [(ROLE_PROMPT_DIR / f"{seat}.md").read_text(encoding="utf-8").rstrip()]
    for name in stacks:
        text = (STACK_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
        parts.append(f"## Stack: {name}\n{text}")
    parts.append(f"## Brief\n{brief.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts) + "\n"


def codex_argv(seat: Seat, out: Path, prompt: str) -> list[str]:
    """Return the Codex command line for a seat.

    The tester needs ``--sandbox workspace-write`` (it writes tests and the gate
    writes caches); the reviewer runs ``codex exec review`` in its free-text form,
    which is the only shape that accepts our rules as a prompt; the arch-reviewer
    judges a design document, not a diff, so it runs plain ``codex exec`` read-only.

    Args:
        seat: The seat being run.
        out: File Codex writes its final message to.
        prompt: The composed prompt from :func:`build_prompt`.

    Returns:
        The argv list, prompt last.
    """
    if seat == "tester":
        return ["codex", "exec", "--sandbox", "workspace-write", "-o", str(out), prompt]
    if seat == "arch-reviewer":
        return ["codex", "exec", "--sandbox", "read-only", "-o", str(out), prompt]
    return ["codex", "exec", "review", "-o", str(out), prompt]


def is_logged_in() -> bool:
    """Report whether the Codex CLI has a usable ChatGPT sign-in.

    Without this pre-flight an unauthenticated run burns ~60s on 401 retries
    before failing.

    Returns:
        True when ``codex login status`` exits 0.

    Raises:
        OSError: The ``codex`` executable is not on PATH.
    """
    try:
        return (
            subprocess.run(
                ["codex", "login", "status"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=LOGIN_TIMEOUT_SECONDS,
                check=False,
            ).returncode
            == 0
        )
    except subprocess.TimeoutExpired:
        return False


def read_gate_verdict(out: Path) -> Literal["green", "red"] | None:
    """Read the tester's verdict from the first line of its report.

    The match is exact — no leading blank line, no surrounding whitespace, no other
    casing. A seat that cannot follow the one formatting rule it was given has not
    shown it ran the gate, so anything else is "no verdict" rather than a guess.

    Args:
        out: The file Codex wrote its final message to.

    Returns:
        ``"green"`` or ``"red"``, or None when the file is missing or its first
        line is not exactly the ``GATE:`` line the role prompt demands.
    """
    try:
        lines = out.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    first_line = lines[0] if lines else ""
    if first_line == f"{GATE_PREFIX}green":
        return "green"
    if first_line == f"{GATE_PREFIX}red":
        return "red"
    return None


def echo_report(out: Path, max_lines: int, *, note_overflow: bool) -> None:
    """Print the seat's final message, truncated so one run cannot flood KASPER.

    Args:
        out: The file Codex wrote its final message to.
        max_lines: How many lines to echo before cutting the rest.
        note_overflow: Whether a truncated echo ends with a pointer at the file.
            Ranked findings are legitimately longer than the echo, so the reviewer
            says where the rest is; a tester report over its contracted length is a
            defect its transcript already records, and the caller prints the path on
            the next line regardless, so the pointer would only be noise.
    """
    try:
        lines = out.read_text(encoding="utf-8").splitlines()
    except OSError:
        return  # The caller reports the missing report; there is nothing to echo.
    print("\n".join(lines[:max_lines]))
    if note_overflow and len(lines) > max_lines:
        print(f"… full report in {out}")


def describe_argv(argv: list[str]) -> str:
    """Render a Codex argv for humans, with the prompt abbreviated to one line."""
    head, prompt = argv[:-1], argv[-1]
    excerpt = prompt[:DRY_RUN_PROMPT_CHARS].replace("\n", " ")
    shown = f"{excerpt}… [{len(prompt)} chars]"
    return shlex.join([*head, shown])


def has_report(out: Path) -> bool:
    """Report whether Codex left a non-empty final message at ``out``.

    Args:
        out: The file Codex wrote its final message to.

    Returns:
        True when the file exists and holds at least one non-whitespace character.
    """
    try:
        return bool(out.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def run_seat(
    seat: Seat,
    brief: Path,
    out: Path,
    timeout: int,
    dry_run: bool,
    stacks: Sequence[str] = (),
) -> int:
    """Run one seat end to end and return this script's exit code.

    Codex runs in the current working directory — the project KASPER invoked the seat
    for — so the same script serves the repository copy and the installed home copy.

    Args:
        seat: The seat to run.
        brief: The task brief file.
        out: Where Codex's final message is captured.
        timeout: Seconds before the Codex run is killed.
        dry_run: Print the command instead of running it.
        stacks: Stack prompts to include, in order (see :func:`build_prompt`).

    Returns:
        A process exit code as documented in the module docstring.
    """
    project = Path.cwd()
    if not (project / ".git").is_dir():
        print(
            f"{project} is not a repository — run the seat from the project root.", file=sys.stderr
        )
        return EXIT_BAD_ARGUMENTS

    try:
        argv = codex_argv(seat, out, build_prompt(seat, brief, stacks))
    except OSError as exc:
        print(f"cannot read the seat's prompt: {exc}", file=sys.stderr)
        return EXIT_BAD_ARGUMENTS

    if dry_run:
        print(describe_argv(argv))
        return 0

    log = Path(f"{out}{RUN_LOG_SUFFIX}")
    try:
        if not is_logged_in():
            print("codex is not signed in — run `codex login`, then retry.", file=sys.stderr)
            return EXIT_CODEX_UNUSABLE
        # A leftover report from an earlier run would otherwise certify this one.
        out.unlink(missing_ok=True)
        # Streaming straight to the log keeps the transcript off KASPER's stdout, and
        # leaves the partial output on disk when a run has to be killed.
        with log.open("w", encoding="utf-8") as transcript:
            completed = subprocess.run(
                argv,
                cwd=project,
                stdin=subprocess.DEVNULL,
                stdout=transcript,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
    except FileNotFoundError as exc:
        print(f"codex is not runnable ({exc}) — install @openai/codex.", file=sys.stderr)
        return EXIT_CODEX_UNUSABLE
    except OSError as exc:
        print(f"cannot prepare {out} for this run: {exc}", file=sys.stderr)
        return EXIT_BAD_ARGUMENTS
    except subprocess.TimeoutExpired:
        print(
            f"codex {seat} exceeded {timeout}s and was killed — transcript: {log}", file=sys.stderr
        )
        return EXIT_TIMEOUT

    if seat == "reviewer":
        # Findings are data, not a verdict — KASPER reads them from the output file.
        echo_report(out, REVIEWER_ECHO_LINES, note_overflow=True)
        return completed.returncode

    if seat == "arch-reviewer":
        echo_report(out, REVIEWER_ECHO_LINES, note_overflow=True)
        if not has_report(out):
            print(
                f"codex exited {completed.returncode} but wrote no report — transcript: {log}",
                file=sys.stderr,
            )
            return EXIT_NO_VERDICT
        return 0

    echo_report(out, TESTER_ECHO_LINES, note_overflow=False)
    verdict = read_gate_verdict(out)
    if verdict is None:
        print(
            f"codex exited {completed.returncode} but wrote no `GATE:` first line"
            f" — transcript: {log}",
            file=sys.stderr,
        )
        return EXIT_NO_VERDICT
    return EXIT_GATE_RED if verdict == "red" else 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line for :func:`main`."""
    parser = argparse.ArgumentParser(description="Run a KASPER verification seat on Codex.")
    parser.add_argument(
        "seat",
        choices=SEATS,
        help="tester: write tests and run the gate · reviewer: judge the diff ·"
        " arch-reviewer: judge a design document (read-only).",
    )
    parser.add_argument(
        "--brief",
        type=Path,
        required=True,
        help="Task brief file, relative to the project root you run this from.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Where Codex writes its report; its transcript lands at <out>.log.",
    )
    parser.add_argument(
        "--stack",
        type=stack_name,
        action="append",
        default=[],
        metavar="NAME",
        help="Append .claude/codex/stacks/NAME.md to the prompt; repeatable, kept in order.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Seconds before the run is killed (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the codex command and exit without running it.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Run the requested seat, always naming the output path last."""
    args = parse_args(argv)
    seat: Seat = args.seat  # argparse `choices` guarantees one of the SEATS literals
    try:
        return run_seat(seat, args.brief, args.out, args.timeout, args.dry_run, args.stack)
    finally:
        print(args.out)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
