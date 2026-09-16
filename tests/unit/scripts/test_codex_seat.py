"""Seat contracts with in-memory files and a fake subprocess; no runtime I/O."""

import shlex
import subprocess
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize(
    ("seat", "prefix"),
    [
        ("tester", ["codex", "exec", "--sandbox", "workspace-write"]),
        ("reviewer", ["codex", "exec", "review"]),
    ],
)
def test_argv_preserves_prompt_and_output_as_single_arguments(
    codex_seat: ModuleType, seat: str, prefix: list[str]
) -> None:
    """Both seats use their required CLI form without interpreting shell text."""
    prompt = "Role\nBrief 'quoted' $(literal)"
    assert codex_seat.codex_argv(seat, Path("reports/my report.md"), prompt) == [
        *prefix,
        "-o",
        "reports/my report.md",
        prompt,
    ]


@pytest.mark.parametrize("seat", ["tester", "reviewer"])
def test_prompt_places_the_selected_role_before_the_brief(
    codex_seat: ModuleType, seat_files: dict[Path, str], seat: str
) -> None:
    """Role instructions precede a labelled brief, preserving interior Unicode and newlines."""
    seat_files[Path("brief.md")] = "\n  Verify café\nsecond line  \n"
    role = "Tester" if seat == "tester" else "Reviewer"
    assert codex_seat.build_prompt(seat, Path("brief.md")) == (
        f"{role} role\n\n## Brief\nVerify café\nsecond line\n"
    )


@pytest.mark.parametrize("seat", ["tester", "reviewer"])
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
    prefix = (
        ["codex", "exec", "--sandbox", "workspace-write"]
        if seat == "tester"
        else [
            "codex",
            "exec",
            "review",
        ]
    )
    role = "Tester" if seat == "tester" else "Reviewer"
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
        timeout=30,
        check=False,
    )
    captured = capsys.readouterr()
    assert captured.out == "report.md\n"
    assert ("install @openai/codex" if failure == "missing" else "codex login") in captured.err


@pytest.mark.parametrize("seat", ["tester", "reviewer"])
def test_exec_timeout_exits_four_and_uses_requested_deadline(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    capsys: pytest.CaptureFixture[str],
    seat: str,
) -> None:
    """Both seats use a bounded run in the repository with stdin disconnected."""
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
        "cwd": codex_seat.REPO_ROOT,
        "stdin": subprocess.DEVNULL,
        "timeout": 7,
        "check": False,
    }
    captured = capsys.readouterr()
    assert captured.out == "report.md\n"
    assert "exceeded 7s" in captured.err


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


@pytest.mark.parametrize("missing", ["brief", "role"])
def test_unreadable_prompt_exits_two_before_login(
    codex_seat: ModuleType,
    seat_files: dict[Path, str],
    seat_process: Mock,
    capsys: pytest.CaptureFixture[str],
    missing: str,
) -> None:
    """Missing brief or role is an input error, with the output path still printed."""
    path = Path("brief.md") if missing == "brief" else codex_seat.ROLE_PROMPT_DIR / "tester.md"
    del seat_files[path]
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
        == 2
    )
    seat_process.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == "report.md\n"
    assert "cannot read the seat's prompt" in captured.err
