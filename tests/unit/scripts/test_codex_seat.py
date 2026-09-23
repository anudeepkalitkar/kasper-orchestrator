"""Seat contracts with in-memory files and a fake subprocess; no runtime I/O."""

import io
import os
import shlex
import subprocess
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize(
    ("seat", "prefix"),
    [
        (
            "tester",
            ["codex", "exec", "-c", "features.multi_agent=false", "--sandbox", "workspace-write"],
        ),
        ("reviewer", ["codex", "exec", "review", "-c", "features.multi_agent=false"]),
        (
            "arch-reviewer",
            ["codex", "exec", "-c", "features.multi_agent=false", "--sandbox", "read-only"],
        ),
    ],
)
def test_argv_preserves_prompt_and_output_as_single_arguments(
    codex_seat: ModuleType, seat: str, prefix: list[str]
) -> None:
    """All seats use their required CLI form without interpreting shell text."""
    prompt = "Role\nBrief 'quoted' $(literal)"
    assert codex_seat.codex_argv(seat, Path("reports/my report.md"), prompt) == [
        *prefix,
        "-o",
        "reports/my report.md",
        prompt,
    ]


@pytest.mark.parametrize("seat", ["tester", "reviewer", "arch-reviewer"])
def test_prompt_places_the_selected_role_before_the_brief(
    codex_seat: ModuleType, seat_files: dict[Path, str], seat: str
) -> None:
    """Role instructions precede a labelled brief, preserving interior Unicode and newlines."""
    seat_files[Path("brief.md")] = "\n  Verify café\nsecond line  \n"
    role = seat.capitalize()
    assert codex_seat.build_prompt(seat, Path("brief.md")) == (
        f"{role} role\n\n## Brief\nVerify café\nsecond line\n"
    )


@pytest.mark.parametrize("seat", ["tester", "reviewer", "arch-reviewer"])
def test_dry_run_prints_command_and_output_without_starting_codex(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    capsys: pytest.CaptureFixture[str],
    seat: str,
) -> None:
    """Dry runs need no login and always name the output file last."""
    assert (
        codex_seat.main(
            [
                seat,
                "--brief",
                "brief.md",
                "--out",
                "report.md",
                "--dry-run",
            ]
        )
        == 0
    )
    lines = capsys.readouterr().out.splitlines()
    prefix = {
        "tester": [
            "codex",
            "exec",
            "-c",
            "features.multi_agent=false",
            "--sandbox",
            "workspace-write",
        ],
        "reviewer": ["codex", "exec", "review", "-c", "features.multi_agent=false"],
        "arch-reviewer": [
            "codex",
            "exec",
            "-c",
            "features.multi_agent=false",
            "--sandbox",
            "read-only",
        ],
    }[seat]
    role = seat.capitalize()
    prompt = f"{role} role\n\n## Brief\nTask brief\n"
    assert shlex.split(lines[0]) == [
        *prefix,
        "-o",
        "report.md",
        f"{prompt.replace(chr(10), ' ')}… [{len(prompt)} chars]",
    ]
    assert lines[1:] == ["report.md"]
    seat_process.assert_not_called()


def test_description_abbreviates_long_prompts(codex_seat: ModuleType) -> None:
    """A dry-run preview stops at 80 characters and records the full prompt length."""
    prompt = "a\n" * 50
    assert shlex.split(codex_seat.describe_argv(["codex", prompt])) == [
        "codex",
        "a " * 40 + "… [100 chars]",
    ]


@pytest.mark.parametrize("seat", ["tester", "reviewer", "arch-reviewer"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_non_repository_cwd_is_refused_before_any_work(
    codex_seat: ModuleType,
    seat_project: Path,
    seat_process: Mock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seat: str,
    dry_run: bool,
) -> None:
    """Missing .git stops even a dry run before prompt reads, cleanup, or subprocesses."""
    checked_paths: list[Path] = []

    def is_dir(path: Path) -> bool:
        checked_paths.append(path)
        return False

    file_access = Mock(side_effect=AssertionError("refused cwd must not access files"))
    monkeypatch.setattr(Path, "is_dir", is_dir)
    for method in ("read_text", "open", "unlink"):
        monkeypatch.setattr(Path, method, file_access)
    argv = [seat, "--brief", "brief.md", "--out", "report.md"]
    if dry_run:
        argv.append("--dry-run")

    assert codex_seat.main(argv) == 2

    assert checked_paths == [seat_project / ".git"]
    seat_process.assert_not_called()
    file_access.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == "report.md\n"
    assert str(seat_project) in captured.err
    assert "not a repository" in captured.err


@pytest.mark.parametrize("seat", ["tester", "reviewer", "arch-reviewer"])
@pytest.mark.parametrize("project", [Path("/projects/first"), Path("/projects/second")])
def test_role_prompt_is_read_beside_the_script_regardless_of_cwd(
    codex_seat: ModuleType,
    repo_root: Path,
    seat_files: dict[Path, str],
    seat_process: Mock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seat: str,
    project: Path,
) -> None:
    """A different caller project cannot redirect any seat's role prompt."""
    expected_role = repo_root / ".claude" / "codex" / f"{seat}.md"
    seat_files.clear()
    seat_files[expected_role] = "Installation role\n"
    seat_files[Path("brief.md")] = "Caller brief\n"
    seat_files[project / ".claude" / "codex" / f"{seat}.md"] = "Wrong caller role\n"
    monkeypatch.setattr(Path, "cwd", Mock(return_value=project))
    monkeypatch.setattr(Path, "is_dir", lambda path: path == project / ".git")

    assert codex_seat.main([seat, "--brief", "brief.md", "--out", "report.md", "--dry-run"]) == 0

    assert codex_seat.ROLE_PROMPT_DIR == expected_role.parent
    assert "Installation role  ## Brief Caller brief" in capsys.readouterr().out
    seat_process.assert_not_called()


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        ("GATE: green\nDetails", 0),
        ("GATE: red\nDetails", 1),
        (None, 5),
        ("", 5),
        (" \n", 5),
        ("No verdict", 5),
        ("Details\nGATE: green", 5),
        ("GATE: greenish", 5),
        ("green", 5),
        ("red", 5),
        ("\nGATE: green", 5),
        (" GATE: green", 5),
        ("GATE: GREEN", 5),
        ("GATE:green", 5),
        ("GATE: green ", 5),
    ],
)
def test_only_an_exact_first_line_gate_is_a_verdict(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    capsys: pytest.CaptureFixture[str],
    report: str | None,
    expected: int,
) -> None:
    """Malformed, missing, or later verdicts must never certify a gate."""
    if report is not None:
        seat_files[Path("report.md")] = report
    assert (
        codex_seat.main(
            [
                "tester",
                "--brief",
                "brief.md",
                "--out",
                "report.md",
            ]
        )
        == expected
    )
    assert capsys.readouterr().out.splitlines()[-1] == "report.md"
    assert seat_process.call_count == 2


@pytest.mark.parametrize("failure", [1, "timeout", "missing"])
def test_login_failure_exits_three_without_running_a_seat(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    capsys: pytest.CaptureFixture[str],
    failure: int | str,
) -> None:
    """Failed, hung, or unavailable login preflights stop before exec."""
    if failure == "timeout":
        seat_process.side_effect = subprocess.TimeoutExpired("codex", 30)
    elif failure == "missing":
        seat_process.side_effect = FileNotFoundError("codex")
    else:
        seat_process.return_value.returncode = failure
    assert (
        codex_seat.main(
            [
                "tester",
                "--brief",
                "brief.md",
                "--out",
                "report.md",
            ]
        )
        == 3
    )
    seat_process.assert_called_once_with(
        ["codex", "login", "status"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    captured = capsys.readouterr()
    assert captured.out == "report.md\n"
    assert ("install @openai/codex" if failure == "missing" else "codex login") in captured.err


@pytest.mark.parametrize("seat", ["tester", "reviewer", "arch-reviewer"])
def test_exec_timeout_exits_four_and_uses_requested_deadline(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    seat_log: Mock,
    seat_project: Path,
    capsys: pytest.CaptureFixture[str],
    seat: str,
) -> None:
    """All seats use a bounded run in the repository with stdin disconnected."""
    seat_process.side_effect = [Mock(returncode=0), subprocess.TimeoutExpired("codex", 7)]
    assert (
        codex_seat.main(
            [
                seat,
                "--brief",
                "brief.md",
                "--out",
                "report.md",
                "--timeout",
                "7",
            ]
        )
        == 4
    )
    assert seat_process.call_args.kwargs == {
        "cwd": seat_project,
        "stdin": subprocess.DEVNULL,
        "stdout": seat_log.return_value,
        "stderr": subprocess.STDOUT,
        "timeout": 7,
        "check": False,
    }
    captured = capsys.readouterr()
    assert captured.out == "report.md\n"
    assert "exceeded 7s" in captured.err
    seat_log.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("returncode", [0, 1, 7])
def test_reviewer_returns_codex_status_without_requiring_a_gate(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    returncode: int,
) -> None:
    """Reviewer findings are data; its process status passes through unchanged."""
    seat_process.side_effect = [Mock(returncode=0), Mock(returncode=returncode)]
    assert (
        codex_seat.main(
            [
                "reviewer",
                "--brief",
                "brief.md",
                "--out",
                "report.md",
            ]
        )
        == returncode
    )


def test_failed_tester_session_without_report_exits_five(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
) -> None:
    """A failed session is no verdict, distinct from a reported red gate."""
    seat_process.side_effect = [Mock(returncode=0), Mock(returncode=1)]
    assert (
        codex_seat.main(
            [
                "tester",
                "--brief",
                "brief.md",
                "--out",
                "report.md",
            ]
        )
        == 5
    )


@pytest.mark.parametrize("seat", ["tester", "reviewer", "arch-reviewer"])
@pytest.mark.parametrize("missing", ["brief", "role"])
def test_unreadable_prompt_exits_two_before_login(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    capsys: pytest.CaptureFixture[str],
    missing: str,
    seat: str,
) -> None:
    """Missing brief or role is an input error, with the output path still printed."""
    path = Path("brief.md") if missing == "brief" else codex_seat.ROLE_PROMPT_DIR / f"{seat}.md"
    del seat_files[path]
    assert (
        codex_seat.main(
            [
                seat,
                "--brief",
                "brief.md",
                "--out",
                "report.md",
            ]
        )
        == 2
    )
    seat_process.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == "report.md\n"
    assert "cannot read the seat's prompt" in captured.err


@pytest.mark.parametrize(
    ("seat", "limit"), [("tester", 11), ("reviewer", 60), ("arch-reviewer", 60)]
)
@pytest.mark.parametrize("offset", [-1, 0, 1, 20])
def test_echo_limits_report_lines_and_prints_output_path_last(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    capsys: pytest.CaptureFixture[str],
    seat: str,
    limit: int,
    offset: int,
) -> None:
    """Tester stdout is capped at eleven report lines; reviewers get an overflow pointer."""
    out = Path("reports/my report.md")
    lines = ["GATE: green", *(f"Finding {index}" for index in range(limit + offset - 1))]
    seat_files[out] = "\n".join(lines) + "\n"

    assert codex_seat.main([seat, "--brief", "brief.md", "--out", str(out)]) == 0

    expected = lines[:limit]
    if seat != "tester" and offset > 0:
        expected.append(f"… full report in {out}")
    assert capsys.readouterr().out.splitlines() == [*expected, str(out)]


@pytest.mark.parametrize("seat", ["tester", "reviewer", "arch-reviewer"])
def test_transcript_is_opened_beside_report_without_creating_a_disk_file(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    seat_log: Mock,
    seat_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seat: str,
) -> None:
    """The complete run routes transcript writes to a fake and never opens or unlinks disk."""
    out = Path("reports/my report.md")
    seat_files[out] = "GATE: green\n"
    disk_open = Mock(side_effect=AssertionError("unit test attempted disk open"))
    disk_unlink = Mock(side_effect=AssertionError("unit test attempted disk unlink"))
    disk_stat = Mock(side_effect=AssertionError("unit test attempted disk stat"))
    real_cwd = Mock(side_effect=AssertionError("unit test attempted real cwd lookup"))
    with monkeypatch.context() as guard:
        guard.setattr(io, "open", disk_open)
        guard.setattr(os, "open", disk_open)
        guard.setattr(os, "unlink", disk_unlink)
        guard.setattr(os, "stat", disk_stat)
        guard.setattr(os, "getcwd", real_cwd)
        guard.setattr("builtins.open", disk_open)
        assert codex_seat.main([seat, "--brief", "brief.md", "--out", str(out)]) == 0

    disk_open.assert_not_called()
    disk_unlink.assert_not_called()
    disk_stat.assert_not_called()
    real_cwd.assert_not_called()
    seat_log.assert_called_once_with(Path("reports/my report.md.log"), "w", encoding="utf-8")
    assert seat_process.call_count == 2
    assert seat_process.call_args_list[0].args == (["codex", "login", "status"],)
    assert seat_process.call_args_list[0].kwargs == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "timeout": 30,
        "check": False,
    }
    assert seat_process.call_args.kwargs == {
        "cwd": seat_project,
        "stdin": subprocess.DEVNULL,
        "stdout": seat_log.return_value,
        "stderr": subprocess.STDOUT,
        "timeout": 1800,
        "check": False,
    }
    seat_log.return_value.__exit__.assert_called_once_with(None, None, None)
    assert capsys.readouterr().out.splitlines() == ["GATE: green", str(out)]


def test_unwritable_transcript_stops_before_exec(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    seat_log: Mock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Failure to open the transcript is an input error with the report path last."""
    seat_log.side_effect = PermissionError("read-only destination")
    assert codex_seat.main(["tester", "--brief", "brief.md", "--out", "report.md"]) == 2
    assert seat_process.call_count == 1
    assert seat_process.call_args.args == (["codex", "login", "status"],)
    captured = capsys.readouterr()
    assert captured.out == "report.md\n"
    assert "cannot prepare report.md" in captured.err
    assert "read-only destination" in captured.err


@pytest.mark.parametrize("seat", ["tester", "reviewer", "arch-reviewer"])
@pytest.mark.parametrize("stacks", [("a", "b"), ("b", "a"), ("a", "a")])
def test_repeatable_stacks_reach_exec_in_requested_order(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    seat: str,
    stacks: tuple[str, str],
) -> None:
    """CLI stack blocks preserve order and repeats between the role and brief."""
    seat_files[codex_seat.STACK_PROMPT_DIR / "a.md"] = "\nRules café\nsecond line\n"
    seat_files[codex_seat.STACK_PROMPT_DIR / "b.md"] = "Rules B\n"
    seat_files[Path("report.md")] = "GATE: green\n"
    argv = [seat, "--brief", "brief.md", "--out", "report.md"]
    for name in stacks:
        argv.extend(["--stack", name])

    assert codex_seat.main(argv) == 0

    blocks = {"a": "## Stack: a\nRules café\nsecond line", "b": "## Stack: b\nRules B"}
    expected = (
        f"{seat.capitalize()} role\n\n{blocks[stacks[0]]}\n\n"
        f"{blocks[stacks[1]]}\n\n## Brief\nTask brief\n"
    )
    assert seat_process.call_args.args[0][-1] == expected


@pytest.mark.parametrize(
    "name", ["", "Python", "../python", "/python", "a/b", "a\\b", "a_b", "1a", "a b", "a\n"]
)
def test_invalid_stack_names_exit_two_before_login(
    codex_seat: ModuleType,
    seat_project: Path,
    seat_process: Mock,
    capsys: pytest.CaptureFixture[str],
    name: str,
) -> None:
    """Non-slugs, including path traversal and trailing newlines, are argument errors."""
    with pytest.raises(SystemExit) as exc:
        codex_seat.main(["tester", "--brief", "brief.md", "--out", "report.md", "--stack", name])
    assert exc.value.code == 2
    assert "invalid stack name" in capsys.readouterr().err
    seat_process.assert_not_called()


@pytest.mark.parametrize("name", ["a", "python", "python3", "my-stack", "a0-b1"])
def test_valid_stack_slugs_are_preserved(
    codex_seat: ModuleType, seat_project: Path, name: str
) -> None:
    """Lowercase slug names retain their spelling through argument parsing."""
    args = codex_seat.parse_args(
        ["tester", "--brief", "brief.md", "--out", "report.md", "--stack", name]
    )
    assert args.stack == [name]


@pytest.mark.parametrize("seat", ["tester", "reviewer", "arch-reviewer"])
def test_missing_stack_names_its_installation_path_before_login(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    seat_project: Path,
    repo_root: Path,
    capsys: pytest.CaptureFixture[str],
    seat: str,
) -> None:
    """A caller-local stack cannot replace a missing installed stack prompt."""
    seat_files[seat_project / ".claude/codex/stacks/missing.md"] = "Wrong caller stack"
    assert (
        codex_seat.main([seat, "--brief", "brief.md", "--out", "report.md", "--stack", "missing"])
        == 2
    )
    captured = capsys.readouterr()
    assert "cannot read the seat's prompt" in captured.err
    assert str(repo_root / ".claude/codex/stacks/missing.md") in captured.err
    assert captured.out == "report.md\n"
    seat_process.assert_not_called()


@pytest.mark.parametrize("returncode", [0, 1, 7])
@pytest.mark.parametrize(
    ("report", "expected"),
    [(None, 5), ("", 5), (" \n\t", 5), ("Design findings", 0), ("GATE: red\nFindings", 0)],
)
def test_arch_reviewer_requires_a_current_nonempty_report(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    capsys: pytest.CaptureFixture[str],
    returncode: int,
    report: str | None,
    expected: int,
) -> None:
    """Report presence decides architecture review completion regardless of process status."""
    if report is not None:
        seat_files[Path("report.md")] = report
    seat_process.side_effect = [Mock(returncode=0), Mock(returncode=returncode)]
    assert (
        codex_seat.main(["arch-reviewer", "--brief", "brief.md", "--out", "report.md"]) == expected
    )
    captured = capsys.readouterr()
    assert captured.out.splitlines()[-1] == "report.md"
    if expected == 5:
        assert f"codex exited {returncode} but wrote no report" in captured.err
    else:
        assert captured.err == ""


@pytest.mark.parametrize("seat", ["tester", "reviewer", "arch-reviewer"])
def test_gate_verdict_is_consulted_only_for_tester(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    monkeypatch: pytest.MonkeyPatch,
    seat: str,
) -> None:
    """Review seats treat a GATE line as report text, never as their exit verdict."""
    seat_files[Path("report.md")] = "GATE: red\nFindings"
    verdict = Mock(wraps=codex_seat.read_gate_verdict)
    monkeypatch.setattr(codex_seat, "read_gate_verdict", verdict)
    assert codex_seat.main([seat, "--brief", "brief.md", "--out", "report.md"]) == (
        1 if seat == "tester" else 0
    )
    if seat == "tester":
        verdict.assert_called_once_with(Path("report.md"))
    else:
        verdict.assert_not_called()
