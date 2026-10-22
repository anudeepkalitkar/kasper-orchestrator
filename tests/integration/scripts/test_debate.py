"""Real files and controlled Python subprocesses; never the installed model CLIs."""

import csv
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

from tests.helpers.debate import answer

ARGS = ["architect", "--brief", "brief.md", "--out", "report.md"]


@pytest.mark.parametrize("no_critique", [False, True])
def test_dry_run_has_phase_order_and_no_artifacts(
    debate: ModuleType,
    debate_project: Path,
    capsys: pytest.CaptureFixture[str],
    no_critique: bool,
) -> None:
    """Planning lists the entire default roster without preparing output or starting children."""
    assert debate.main(ARGS + ["--dry-run"] + (["--no-critique"] if no_critique else [])) == 0
    lines = capsys.readouterr().out.splitlines()
    tags = [f"[propose {label}]" for label in "ABCD"]
    if not no_critique:
        tags += [f"[critique {label}]" for label in "ABCD"]
    tags += ["[judge]"]
    assert len(lines) == len(tags) + 1
    assert all(line.startswith(tag) for line, tag in zip(lines, tags, strict=False))
    assert lines[-1] == "report.md"
    for name in ("report.md", "report.md.d", "launches.txt"):
        assert not (debate_project / name).exists()


def test_full_run_layout_logs_and_mapping(debate: ModuleType, debate_project: Path) -> None:
    """Nine valid calls leave nine answers, transcripts, and script-owned header lines."""
    assert debate.main(ARGS) == 0
    report = (debate_project / "report.md").read_text(encoding="utf-8")
    assert report.splitlines()[:4] == [
        "DEBATE: complete",
        "ROLE: architect",
        "SEATS: proposed 4/4 · critiqued 4/4 · judge claude:fable ok",
        "CALLS: 9/9",
    ]
    stems = [f"{phase}-{label}" for phase in ("propose", "critique") for label in "ABCD"]
    stems.append("judge")
    directory = debate_project / "report.md.d"
    assert {p.name for p in directory.iterdir()} == {
        f"{stem}{suffix}" for stem in stems for suffix in (".md", ".md.log", ".md.prompt")
    }
    for stem in stems:
        assert (directory / f"{stem}.md").read_text(encoding="utf-8") == answer(stem.split("-")[0])
        assert "controlled transcript" in (directory / f"{stem}.md.log").read_text(encoding="utf-8")
    for label, vendor, model in [
        ("A", "codex", "gpt-6-astra"),
        ("B", "claude", "sonnet"),
        ("C", "claude", "sonnet"),
        ("D", "claude", "sonnet"),
    ]:
        assert f"- {label} {vendor}:{model} — propose ok" in report


@pytest.mark.parametrize("seed_seats", [False, True])
def test_stale_report_and_seat_files_cannot_certify_empty_run(
    debate: ModuleType,
    debate_project: Path,
    seed_seats: bool,
) -> None:
    """An earlier answer cannot supply either quorum or a verdict on a reused output path."""
    out = debate_project / "report.md"
    out.write_text("DEBATE: complete\nOLD ANSWER", encoding="utf-8")
    directory = debate_project / "report.md.d"
    directory.mkdir()
    if seed_seats:
        for phase in ("propose", "critique", "judge"):
            for label in [""] if phase == "judge" else list("ABCD"):
                stem = phase + (f"-{label}" if label else "")
                (directory / f"{stem}.md").write_text(answer(phase), encoding="utf-8")
                (directory / f"{stem}.md.log").write_text("OLD LOG", encoding="utf-8")
    (debate_project / "mode.txt").write_text("empty", encoding="utf-8")
    assert debate.main(ARGS) == 5
    report = out.read_text(encoding="utf-8")
    assert report.startswith("DEBATE: failed\n")
    assert "REASON: 0 valid proposals from 0 vendors" in report
    assert "OLD ANSWER" not in report
    assert report.count("propose empty (no output)") == 4
    assert "CALLS: 4/9" in report
    # Claude stdout is now opened before launch: empty new captures are expected,
    # but no previous answer, unrun critique, or judge may survive the probe.
    assert {path.name for path in directory.glob("*.md")} == {
        f"propose-{label}.md" for label in "BCD"
    }
    assert all(path.read_text(encoding="utf-8") == "" for path in directory.glob("*.md"))
    assert all("OLD LOG" not in p.read_text(encoding="utf-8") for p in directory.iterdir())


def test_readiness_deadline_prevents_seat_launch(
    debate: ModuleType,
    debate_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness has its own bounded deadline and reports the offending vendor."""
    (debate_project / "mode.txt").write_text("readiness", encoding="utf-8")
    monkeypatch.setattr(debate, "READINESS_TIMEOUT_SECONDS", 1)
    started = time.monotonic()
    assert debate.main(ARGS) == 3
    assert time.monotonic() - started < 4
    assert (debate_project / "launches.txt").read_text(encoding="utf-8") == "claude ready\n"
    report = (debate_project / "report.md").read_text(encoding="utf-8")
    assert "REASON: vendor claude readiness timed out after 1s" in report
    assert "CALLS: 0/9" in report


def test_canary_trip_stops_subsequent_children(debate: ModuleType, debate_project: Path) -> None:
    """A new session directory ends the lane before a second seat or judge can launch."""
    (debate_project / "mode.txt").write_text("canary", encoding="utf-8")
    assert debate.main(ARGS + ["--seat", "claude:a", "--seat", "claude:b"]) == 3
    assert (debate_project / "launches.txt").read_text(encoding="utf-8") == (
        "claude ready\nclaude seat\n"
    )
    report = (debate_project / "report.md").read_text(encoding="utf-8")
    assert report.startswith("DEBATE: failed\n")
    assert "REASON: claude seat executed hooks" in report
    assert "CALLS: 1/9" in report


def test_timeout_kills_sleeping_grandchild(
    debate: ModuleType,
    debate_project: Path,
) -> None:
    """A timeout kills the descendant too, verified by polling its actual process state."""
    (debate_project / "mode.txt").write_text("grandchild", encoding="utf-8")
    out = debate_project / "seat.md"
    pid: int | None = None
    taskkill = shutil.which("taskkill") if sys.platform == "win32" else None
    try:
        result = debate.run_call(
            ["/controlled/claude"],
            "PHASE propose",
            out,
            debate_project / "seat.log",
            1,
            False,
            lambda text: None,
            Path(taskkill) if taskkill else None,
        )
        assert result.outcome == "timeout"
        assert result.detail == "1s"
        pid = int((debate_project / "grandchild.pid").read_text(encoding="utf-8"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not _process_exists(pid):
                break
            time.sleep(0.05)
        else:
            pytest.fail(f"grandchild {pid} still exists after group kill")
    finally:
        if pid is not None and _process_exists(pid):
            try:
                os.kill(pid, signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL)
            except ProcessLookupError:
                pass


def _process_exists(pid: int) -> bool:
    if sys.platform == "win32":
        # Windows os.kill(pid, 0) terminates the process instead of probing it.
        tasklist = shutil.which("tasklist")
        assert tasklist is not None
        result = subprocess.run(
            [tasklist, "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            timeout=10,
            check=True,
        )
        rows = csv.reader(result.stdout.splitlines())
        return any(len(row) > 1 and row[1] == str(pid) for row in rows)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_fixture_passes_nonvendor_process_without_encoding_through(
    debate: ModuleType, debate_project: Path,
) -> None:
    """The subprocess.run shape used by taskkill reaches the real child unchanged."""
    assert debate.shutil.which(sys.executable) == sys.executable
    result = subprocess.run(
        [sys.executable, "-c", "raise SystemExit(7)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    assert result.returncode == 7
    assert not (debate_project / "launches.txt").exists()


def test_surviving_grandchild_holding_stdout_does_not_block_report(
    debate: ModuleType,
    debate_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed tree cleanup remains bounded even while descendants retain stdout handles."""
    (debate_project / "mode.txt").write_text("grandchild", encoding="utf-8")
    survivors: list[subprocess.Popen[str]] = []
    real_kill = sys.modules["codex_seat"].kill_group
    taskkill = shutil.which("taskkill") if sys.platform == "win32" else None
    taskkill_path = Path(taskkill) if taskkill else None

    def failed_kill(process: subprocess.Popen[str], resolved: Path | None) -> str:
        survivors.append(process)
        return "controlled tree kill failure"

    monkeypatch.setattr(sys.modules["codex_seat"], "kill_group", failed_kill)
    started = time.monotonic()
    try:
        assert debate.main(ARGS + ["--timeout", "1", "--seat", "claude:a",
                                   "--seat", "codex:b"]) == 5
        elapsed = time.monotonic() - started
        assert elapsed < 1 + sys.modules["codex_seat"].KILL_TIMEOUT_SECONDS + 5
        assert len(survivors) == 2
        report = (debate_project / "report.md").read_text(encoding="utf-8")
        assert report.startswith("DEBATE: failed\n")
        assert "CALLS: 2/9" in report
        assert report.count(
            "propose timeout (1s; cleanup failed: controlled tree kill failure; "
            "child survived cleanup)"
        ) == 2
        pid = int((debate_project / "grandchild.pid").read_text(encoding="utf-8"))
        assert _process_exists(pid)
    finally:
        for process in survivors:
            real_kill(process, taskkill_path)
            process.wait(timeout=10)

