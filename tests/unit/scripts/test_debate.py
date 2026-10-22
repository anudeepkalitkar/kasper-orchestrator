"""Debate requirements with fake processes, filesystem, and time; no runtime I/O."""

import argparse
import io
import signal
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call

import pytest

from tests.helpers.debate import answer, headings

ARGS = ["architect", "--brief", "brief.md", "--out", "report.md"]


@pytest.mark.parametrize("value", ["claude:sonnet", "codex:gpt-6-astra"])
def test_parse_seat(debate: ModuleType, value: str) -> None:
    """Known vendors preserve the requested model."""
    assert debate.parse_seat(value) == tuple(value.split(":"))


@pytest.mark.parametrize("value", ["other:model", "claude", "claude:", ":model", ""])
def test_invalid_seat(debate: ModuleType, value: str) -> None:
    """Malformed seats fail before execution."""
    with pytest.raises(argparse.ArgumentTypeError):
        debate.parse_seat(value)


@pytest.mark.parametrize("phase", ["propose", "critique", "judge"])
@pytest.mark.parametrize("role", ["architect", "researcher"])
def test_validation_contract(debate: ModuleType, phase: str, role: str) -> None:
    """Every required section is unique and nonempty; first defect wins."""
    valid = answer(phase, role)
    assert debate.validate_output(valid, phase, role, ["B", "C"]) is None
    for heading in headings(phase, role):
        for broken in (
            valid.replace(heading, "## Unexpected"),
            valid + heading + "\nDuplicate\n",
            valid.replace(heading + "\nEvidence café", heading + "\n "),
        ):
            if broken != valid:
                assert debate.validate_output(broken, phase, role) == heading
    assert debate.validate_output("", phase, role) == headings(phase, role)[0]


def test_critique_requires_other_seats(debate: ModuleType) -> None:
    """A general critique cannot omit one competing proposal."""
    assert (
        debate.validate_output(answer("critique"), "critique", "architect", ["Z"]) == "### Seat Z"
    )
    assert (
        debate.validate_output(
            "## Critique\n \n## Revised position\nValid", "critique", "architect"
        )
        == "## Critique"
    )


@pytest.mark.parametrize(
    "role,tools", [("architect", "Read,Grep,Glob"), ("researcher", "WebSearch,WebFetch,Read")]
)
def test_read_only_argv(debate: ModuleType, role: str, tools: str) -> None:
    """Resolved heads, isolation, denies, and stdin-only prompts protect each seat."""
    deny = ["Read(secret)", "Bash(sudo *)"]
    assert debate.claude_argv(
        Path("/resolved/claude"), debate.Seat("claude", "m", "A"), role, Path("out"), deny
    ) == [
        "/resolved/claude",
        "-p",
        "--model",
        "m",
        "--output-format",
        "text",
        "--permission-mode",
        "dontAsk",
        "--tools",
        tools,
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--disallowedTools",
        *deny,
    ]
    assert debate.codex_argv(
        Path("/resolved/codex"), debate.Seat("codex", "m", "A"), role, Path("out")
    ) == [
        "/resolved/codex",
        "exec",
        "-c",
        "features.multi_agent=false",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "-m",
        "m",
        "-o",
        "out",
    ]


@pytest.mark.parametrize("phase", ["propose", "critique", "judge"])
def test_prompt_order_and_anonymity(
    debate: ModuleType, debate_files: dict[Path, str], phase: str
) -> None:
    """Phase, role, brief, sorted anonymous proposals, then critiques are ordered."""
    text = debate.build_prompt(
        phase, "architect", Path("brief.md"), {"B": "Second", "A": "First"}, {"B": "Review"}, "A"
    )
    expected = [f"Contract {phase}", "Contract architect", "Task brief"]
    if phase != "propose":
        expected += ["### Seat A (yours)", "First", "### Seat B", "Second"]
    if phase == "judge":
        expected += ["## Critiques", "Review"]
    positions = [text.index(part) for part in expected]
    assert positions == sorted(positions)
    assert "claude" not in text and "codex" not in text


@pytest.mark.parametrize(
    "names,mtime,tripped",
    [
        (frozenset({"old"}), 1.0, False),
        (frozenset({"old", "new"}), 1.0, True),
        (frozenset({"old"}), 2.0, True),
    ],
)
def test_canary(debate: ModuleType, names: frozenset[str], mtime: float, tripped: bool) -> None:
    """New session names or a changed exclude timestamp trip isolation."""
    assert debate.hook_canary_tripped((frozenset({"old"}), 1.0), (names, mtime)) is tripped


@pytest.mark.parametrize("missing", [None, "home", "project", "both"])
def test_deny_merge(debate: ModuleType, debate_files: dict[Path, str], missing: str | None) -> None:
    """Denies merge verbatim and deduplicate, tolerating absent settings."""
    home = Path("/home/.claude/settings.json")
    project = Path("/project/.claude/settings.json")
    debate_files[home] = '{"permissions":{"deny":["Read(home)","Read(secret)"]}}'
    if missing in ("home", "both"):
        del debate_files[home]
    if missing in ("project", "both"):
        del debate_files[project]
    expected = [] if missing == "both" else ["Read(secret)"]
    if missing not in ("home", "both"):
        expected = ["Read(home)", "Read(secret)"]
    assert debate.load_deny_rules(Path("/home"), Path("/project")) == expected


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("start", "failed"),
        ("timeout", "timeout"),
        ("nonzero", "failed"),
        ("absent", "empty"),
        ("stale", "empty"),
        ("blank", "empty"),
        ("invalid", "invalid"),
        ("valid", "ok"),
    ],
)
@pytest.mark.parametrize("writes_out", [True, False])
def test_run_call_precedence(
    debate: ModuleType,
    debate_files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected: str,
    writes_out: bool,
) -> None:
    """Only fresh valid output from a successful process can pass."""
    out, log = Path("seat.md"), Path("seat.md.log")
    debate_files[out] = answer("propose")
    process = Mock(returncode=7 if mode == "nonzero" else 0)
    payload = {"absent": "", "blank": " \n", "invalid": "wrong"}.get(mode, answer("propose"))

    def wait(timeout: int | None = None) -> int:
        if mode == "timeout" and timeout == 3:
            assert timeout == 3
            raise subprocess.TimeoutExpired("child", timeout)
        if mode != "absent":
            debate_files[out] = payload
        return int(process.returncode)

    def popen(*args: object, **kwargs: object) -> Mock:
        assert out not in debate_files
        assert kwargs["encoding"] == "utf-8"
        assert kwargs.get("shell", False) is False
        assert kwargs.get("start_new_session") is True
        if mode == "start":
            raise OSError("cannot launch")
        return process

    process.wait.side_effect = wait
    monkeypatch.setattr(debate.subprocess, "Popen", popen)
    kill = Mock(return_value=None)
    monkeypatch.setattr(sys.modules["codex_seat"], "kill_group", kill)
    if mode == "stale":
        monkeypatch.setattr(Path, "stat", lambda p: SimpleNamespace(st_mtime=90.0))
    result = debate.run_call(
        ["/resolved/cli"],
        "private brief",
        out,
        log,
        3,
        writes_out,
        lambda text: debate.validate_output(text, "propose", "architect"),
    )
    assert result.outcome == expected
    if mode == "timeout":
        kill.assert_called_once_with(process, None)
        assert result.detail == "3s"
        assert process.wait.call_args_list[-1] == call(timeout=10)
    else:
        kill.assert_not_called()
    if mode not in ("start", "timeout"):
        process.wait.assert_called_once_with(timeout=3)


@pytest.mark.parametrize(
    "fault,extra,code",
    [
        ("none", ["--judge", "codex:m"], 2),
        ("none", ["--max-calls", "8"], 2),
        ("isolation", [], 2),
        ("denies", [], 2),
        ("unwritable", [], 2),
        ("unresolved", [], 3),
        ("unready", [], 3),
        ("deadline", [], 3),
    ],
)
def test_preflight_stops_before_seats(
    debate: ModuleType,
    debate_files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault: str,
    extra: list[str],
    code: int,
) -> None:
    """Argument failures and vendor failures never start a paid call."""
    ready = Mock(return_value=True)
    monkeypatch.setattr(debate, "vendor_ready", ready)
    if fault == "isolation":
        monkeypatch.setattr(debate, "CLAUDE_SETTING_SOURCES", None)
    elif fault == "denies":
        del debate_files[Path("/project/.claude/settings.json")]
    elif fault == "unwritable":
        monkeypatch.setattr(Path, "open", Mock(side_effect=PermissionError("denied")))
    elif fault == "unresolved":
        monkeypatch.setattr(debate.shutil, "which", lambda name: None)
    elif fault == "unready":
        ready.return_value = False
    elif fault == "deadline":
        ready.side_effect = subprocess.TimeoutExpired("child", 30)
    assert debate.main(ARGS + extra) == code
    debate.subprocess.Popen.assert_not_called()
    assert capsys.readouterr().out.splitlines()[-1] == "report.md"
    if code == 3:
        report = debate_files[Path("report.md")]
        assert report.splitlines()[:4] == [
            "DEBATE: failed",
            "ROLE: architect",
            "SEATS: proposed 0/4 · critiqued 0/4 · judge claude:fable not run",
            "CALLS: 0/9",
        ]
        assert report.splitlines()[4].startswith("REASON: vendor claude")
    else:
        ready.assert_not_called()


@pytest.mark.parametrize(
    "fault,code,status,calls",
    [
        ("none", 0, "complete", 9),
        ("proposal", 1, "degraded", 8),
        ("critique", 1, "degraded", 9),
        ("quorum", 5, "failed", 4),
        ("one_vendor", 5, "failed", 4),
        ("judge_timeout", 4, "failed", 9),
        ("judge_invalid", 5, "failed", 9),
        ("judge_empty", 5, "failed", 9),
        ("judge_failed", 5, "failed", 9),
        ("no_critique", 0, "complete", 5),
    ],
)
def test_orchestration_filters_inputs_and_writes_exact_headers(
    debate: ModuleType,
    debate_files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault: str,
    code: int,
    status: str,
    calls: int,
) -> None:
    """Bad seats never reach later prompts; quorum and judge outcomes control the report."""
    monkeypatch.setattr(debate, "vendor_ready", Mock(return_value=True))
    seen: list[tuple[str, str]] = []

    def run(
        argv: list[str],
        prompt: str,
        out: Path,
        log: Path,
        timeout: int,
        writes: bool,
        validate: object,
        taskkill: Path | None,
    ) -> object:
        stem = out.stem
        phase = stem.split("-")[0]
        seen.append((stem, prompt))
        outcome = "ok"
        if fault == "proposal" and stem == "propose-D":
            outcome = "failed"
        if fault == "critique" and stem == "critique-D":
            outcome = "invalid"
        if fault == "quorum" and phase == "propose" and stem != "propose-A":
            outcome = "empty"
        if fault == "one_vendor" and stem == "propose-A":
            outcome = "failed"
        if fault.startswith("judge_") and phase == "judge":
            outcome = fault.removeprefix("judge_")
        debate_files[out] = answer(phase) + f"\nUNIQUE-{stem}\n"
        return debate.CallResult(outcome, "missing `## Answer`" if outcome == "invalid" else "3s")

    monkeypatch.setattr(debate, "run_call", run)
    extra = ["--no-critique"] if fault == "no_critique" else []
    assert debate.main(ARGS + extra) == code
    assert len(seen) == calls
    report = debate_files[Path("report.md")]
    lines = report.splitlines()
    assert lines[0:2] == [f"DEBATE: {status}", "ROLE: architect"]
    proposed = 1 if fault == "quorum" else 3 if fault in ("proposal", "one_vendor") else 4
    critiqued = (
        0
        if fault in ("quorum", "one_vendor", "no_critique")
        else (3 if fault in ("proposal", "critique") else 4)
    )
    judge_state = (
        "not run"
        if fault in ("quorum", "one_vendor")
        else (fault.removeprefix("judge_") if fault.startswith("judge_") else "ok")
    )
    assert lines[2:4] == [
        f"SEATS: proposed {proposed}/4 · critiqued {critiqued}/4"
        f" · judge claude:fable {judge_state}",
        f"CALLS: {calls}/9",
    ]
    if status == "failed":
        assert lines[4].startswith("REASON: ")
    if fault == "judge_invalid":
        assert "## Answer" in lines[4]
    if fault == "proposal":
        assert "- D claude:sonnet — propose failed (3s) · critique not run" in report
        assert all("UNIQUE-propose-D" not in prompt for _, prompt in seen)
    if fault == "critique":
        assert "UNIQUE-critique-D" not in seen[-1][1]
    assert capsys.readouterr().out.splitlines()[-1] == "report.md"


def test_canary_stops_next_popen(
    debate: ModuleType,
    debate_files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A hook trip is terminal even when the triggering process returned valid output."""
    monkeypatch.setattr(debate, "vendor_ready", Mock(return_value=True))
    snapshots = Mock(side_effect=[(frozenset(), None), (frozenset({"new"}), None)])
    monkeypatch.setattr(debate, "hook_canary_snapshot", snapshots)
    child = Mock(returncode=0)

    def finish(timeout: int) -> int:
        debate_files[Path("report.md.d/propose-A.md")] = answer("propose")
        return 0

    child.wait.side_effect = finish
    popen = Mock(return_value=child)
    monkeypatch.setattr(debate.subprocess, "Popen", popen)
    assert debate.main(ARGS + ["--seat", "claude:a", "--seat", "claude:b"]) == 3
    popen.assert_called_once()
    assert "REASON: claude seat executed hooks" in debate_files[Path("report.md")]
    assert capsys.readouterr().out.splitlines()[-1] == "report.md"


@pytest.mark.parametrize("mode", ["ready", "unready", "timeout", "start_error"])
def test_readiness_uses_resolved_executable_and_deadline(
    debate: ModuleType,
    debate_files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """Credential probes use resolved heads, bounded waits, and group cleanup on timeout."""
    process = Mock(stdout=None)
    process.wait.side_effect = (
        [subprocess.TimeoutExpired("child", 30), 0]
        if mode == "timeout"
        else [0 if mode == "ready" else 1]
    )
    popen = Mock(return_value=process)
    if mode == "start_error":
        popen.side_effect = OSError("cannot launch")
    kill = Mock(return_value=None)
    monkeypatch.setattr(debate.subprocess, "Popen", popen)
    monkeypatch.setattr(sys.modules["codex_seat"], "kill_group", kill)
    if mode in ("timeout", "start_error"):
        with pytest.raises(subprocess.TimeoutExpired if mode == "timeout" else OSError):
            debate.vendor_ready("codex", Path("/resolved/codex"))
    else:
        assert debate.vendor_ready("codex", Path("/resolved/codex")) is (mode == "ready")
    assert popen.call_args.args == (["/resolved/codex", "login", "status"],)
    if mode != "start_error":
        assert process.wait.call_args_list[0].kwargs == {"timeout": 30}
    if mode == "timeout":
        kill.assert_called_once_with(process, None)
        assert process.wait.call_args_list == [call(timeout=30), call(timeout=10)]
    else:
        kill.assert_not_called()


@pytest.mark.parametrize(
    "size,cap,critique,expected",
    [
        (0, 100, True, 2),
        (26, 53, True, 0),
        (27, 100, True, 2),
        (4, 9, True, 0),
        (4, 8, True, 2),
        (4, 5, False, 0),
        (4, 4, False, 2),
    ],
)
def test_roster_and_call_cap_boundaries(
    debate: ModuleType,
    debate_files: dict[Path, str],
    size: int,
    cap: int,
    critique: bool,
    expected: int,
) -> None:
    """The label limit and exact phase cost are enforced before readiness."""
    result = debate.preflight(
        "architect",
        Path("brief.md"),
        [("claude", "m")] * size,
        ("claude", "judge"),
        cap,
        critique,
        False,
        Path("/project"),
    )
    if expected:
        assert result == expected
    else:
        assert not isinstance(result, int)
        assert len(result[0]) == size


def test_main_failure_and_dry_run_paths_print_out_last(
    debate: ModuleType,
    debate_files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing inputs, dry runs, and report-write failures retain the output-path contract."""
    assert debate.main(ARGS + ["--dry-run"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "report.md"
    brief = debate_files.pop(Path("brief.md"))
    assert debate.main(ARGS) == 2
    assert capsys.readouterr().out.splitlines()[-1] == "report.md"
    debate_files[Path("brief.md")] = brief
    monkeypatch.setattr(debate, "vendor_ready", Mock(return_value=False))
    monkeypatch.setattr(Path, "write_text", Mock(side_effect=PermissionError("report denied")))
    assert debate.main(ARGS) == 2
    assert capsys.readouterr().out.splitlines()[-1] == "report.md"


def test_windows_without_taskkill_stops_before_readiness(
    debate: ModuleType,
    debate_files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows must have tree cleanup available before even probing a vendor."""
    monkeypatch.setattr(sys.modules["codex_seat"], "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(debate, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        debate.shutil, "which", lambda name: None if name == "taskkill" else f"/resolved/{name}"
    )
    ready = Mock()
    monkeypatch.setattr(debate, "vendor_ready", ready)
    assert debate.main(ARGS) == 3
    ready.assert_not_called()
    debate.subprocess.Popen.assert_not_called()
    report = debate_files[Path("report.md")]
    assert report.startswith("DEBATE: failed\n")
    assert "CALLS: 0/9\nREASON: taskkill not resolved\n" in report


@pytest.mark.parametrize(
    "fault,detail",
    [
        ("success", None),
        ("nonzero", "taskkill exit 1"),
        ("timeout", "taskkill timed out after 10s"),
        ("launch", "taskkill not launchable: denied"),
    ],
)
def test_windows_taskkill_is_bounded_and_failure_reaches_call_result(
    debate: ModuleType,
    debate_files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    detail: str | None,
) -> None:
    """Tree-kill failures retain the seat timeout and appear in its diagnostic detail."""
    monkeypatch.setattr(sys.modules["codex_seat"], "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(debate, "sys", SimpleNamespace(platform="win32"))
    process = Mock(pid=123, stdout=Mock())
    process.wait.side_effect = [subprocess.TimeoutExpired("seat", 3), 0]
    monkeypatch.setattr(debate, "launch", Mock(return_value=process))
    run = Mock(return_value=Mock(returncode=1 if fault == "nonzero" else 0))
    if fault == "timeout":
        run.side_effect = subprocess.TimeoutExpired("taskkill", 10)
    elif fault == "launch":
        run.side_effect = OSError("denied")
    monkeypatch.setattr(debate.subprocess, "run", run)
    result = debate.run_call(
        ["/resolved/cli"],
        "brief",
        Path("seat.md"),
        Path("seat.log"),
        3,
        False,
        lambda text: None,
        Path("/resolved/taskkill"),
    )
    assert sys.modules["codex_seat"].KILL_TIMEOUT_SECONDS == 10
    assert result.outcome == "timeout"
    assert result.detail == ("3s" if detail is None else f"3s; cleanup failed: {detail}")
    run.assert_called_once_with(
        ["/resolved/taskkill", "/T", "/F", "/PID", "123"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    assert process.wait.call_args_list == [call(timeout=3), call(timeout=10)]
    process.communicate.assert_not_called()


@pytest.mark.parametrize("wait_timeout", [False, True])
@pytest.mark.parametrize("kill_fault", ["success", "gone", "denied"])
def test_posix_group_kill_and_wait_are_bounded(
    debate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    wait_timeout: bool,
    kill_fault: str,
) -> None:
    """Cleanup waits at most ten seconds even when killpg fails."""
    monkeypatch.setattr(sys.modules["codex_seat"], "sys", SimpleNamespace(platform="linux"))
    process = Mock(pid=123, stdin=None, stdout=None, stderr=None)
    killpg = Mock()
    if kill_fault == "gone":
        killpg.side_effect = ProcessLookupError()
    elif kill_fault == "denied":
        killpg.side_effect = PermissionError("denied")
    monkeypatch.setattr(sys.modules["codex_seat"].os, "killpg", killpg, raising=False)
    process.wait.return_value = 0
    if wait_timeout:
        process.wait.side_effect = subprocess.TimeoutExpired("child", 10)
    detail = debate.kill_and_wait(process, None)
    killpg.assert_called_once_with(123, signal.SIGKILL)
    process.wait.assert_called_once_with(timeout=10)
    process.communicate.assert_not_called()
    problems = ["killpg failed: denied"] if kill_fault == "denied" else []
    if wait_timeout:
        problems.append("child survived cleanup")
    assert detail == ("; ".join(problems) if problems else None)


def test_call_records_surviving_child_without_another_wait(
    debate: ModuleType,
    debate_files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A surviving child cannot hide a cleanup failure or block forever."""
    process = Mock(stdin=None, stdout=None, stderr=None)
    process.wait.side_effect = [
        subprocess.TimeoutExpired("seat", 3),
        subprocess.TimeoutExpired("cleanup", 10),
    ]
    monkeypatch.setattr(debate, "launch", Mock(return_value=process))
    monkeypatch.setattr(
        sys.modules["codex_seat"], "kill_group", Mock(return_value="taskkill exit 1")
    )
    result = debate.run_call(
        ["cli"],
        "brief",
        Path("seat.md"),
        Path("seat.log"),
        3,
        False,
        lambda text: None,
    )
    assert result.outcome == "timeout"
    assert result.detail == "3s; cleanup failed: taskkill exit 1; child survived cleanup"
    assert process.wait.call_args_list == [call(timeout=3), call(timeout=10)]
    process.communicate.assert_not_called()


def test_readiness_surviving_child_is_reported_without_another_wait(
    debate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failed readiness cleanup reports its bounded wait and preserves the original deadline."""
    process = Mock(stdin=None, stdout=None, stderr=None)
    deadline = subprocess.TimeoutExpired("ready", 30)
    process.wait.side_effect = [deadline, subprocess.TimeoutExpired("drain", 10)]
    monkeypatch.setattr(debate, "launch", Mock(return_value=process))
    monkeypatch.setattr(sys.modules["codex_seat"], "kill_group", Mock(return_value=None))
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        debate.vendor_ready("codex", Path("/resolved/codex"))
    assert caught.value is deadline
    assert process.wait.call_args_list == [call(timeout=30), call(timeout=10)]
    process.communicate.assert_not_called()
    assert capsys.readouterr().err == ("codex readiness cleanup failed: child survived cleanup\n")


@pytest.mark.parametrize("writes_out", [False, True])
def test_call_passes_file_handles_to_popen_without_pipes(
    debate: ModuleType,
    debate_files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    writes_out: bool,
) -> None:
    """Neither vendor uses pipes, including stdin; descendants cannot block pipe closure."""
    handles: dict[Path, io.StringIO] = {}

    def open_file(path: Path, mode: str = "r", encoding: str = "utf-8") -> io.StringIO:
        handle = io.StringIO(debate_files.get(path, "") if mode == "r" else "")
        handles[path] = handle
        return handle

    monkeypatch.setattr(Path, "open", open_file)
    process = Mock()
    process.wait.return_value = 0
    popen = Mock(return_value=process)
    monkeypatch.setattr(debate.subprocess, "Popen", popen)
    debate.run_call(
        ["cli"], "brief", Path("seat.md"), Path("seat.log"), 3, writes_out, lambda text: None
    )
    kwargs = popen.call_args.kwargs
    assert kwargs["stdin"] is handles[Path("seat.md.prompt")]
    assert kwargs["stdout"] is handles[Path("seat.log" if writes_out else "seat.md")]
    assert kwargs["stderr"] == (subprocess.STDOUT if writes_out else handles[Path("seat.log")])
    assert all(kwargs[name] != subprocess.PIPE for name in ("stdin", "stdout", "stderr"))
    process.wait.assert_called_once_with(timeout=3)
    process.communicate.assert_not_called()
    assert all(handle.closed for handle in handles.values())
