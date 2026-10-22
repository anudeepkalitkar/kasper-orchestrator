"""Real temporary prompt/report files with subprocess.run replaced by a fake."""

import io
import shlex
import subprocess
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from tests.helpers.git import git


@pytest.mark.parametrize("seat", ["tester", "reviewer", "arch-reviewer"])
@pytest.mark.parametrize("stacks", [(), ("python", "typescript", "terraform", "devops")])
def test_role_and_brief_files_reach_codex_intact(
    codex_seat: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seat: str,
    stacks: tuple[str, ...],
) -> None:
    """The selected UTF-8 role and brief reach exec, and the report is read from disk."""
    role_dir = tmp_path / "roles"
    role_dir.mkdir()
    (role_dir / f"{seat}.md").write_text("Role café\n", encoding="utf-8")
    brief = tmp_path / "brief.md"
    brief.write_text("Verify this change\n", encoding="utf-8")
    out = tmp_path / "report.md"
    log = tmp_path / "report.md.log"
    log.write_text("Previous transcript\n", encoding="utf-8")
    monkeypatch.setattr(codex_seat, "ROLE_PROMPT_DIR", role_dir)
    expected_prompt = "Role café\n\n"
    for name in stacks:
        text = (codex_seat.STACK_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
        assert text.strip(), f"{name} stack must supply instructions"
        expected_prompt += f"## Stack: {name}\n{text.strip()}\n\n"
    expected_prompt += "## Brief\nVerify this change\n"
    report = "REVIEW: approve\n" if seat == "reviewer" else "GATE: green\n"
    calls: list[list[str]] = []
    monkeypatch.setattr(codex_seat, "find_project_root", lambda start: tmp_path)
    monkeypatch.setattr(codex_seat.shutil, "which", lambda name: name)

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[:2] == ["codex", "exec"]:
            assert argv[-1] == expected_prompt
            assert argv[-3:-1] == ["-o", str(out)]
            out.write_text(report, encoding="utf-8")
            transcript = kwargs["stdout"]
            assert isinstance(transcript, io.TextIOBase)
            assert kwargs["stderr"] == subprocess.STDOUT
            transcript.write("Private transcript café\n")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(codex_seat.subprocess, "run", run)

    def launch(argv: list[str], **kwargs: object) -> Mock:
        run(argv, **kwargs)
        return Mock(wait=Mock(return_value=0))

    monkeypatch.setattr(codex_seat, "launch", launch)
    argv = [seat, "--brief", str(brief), "--out", str(out)]
    for name in stacks:
        argv.extend(["--stack", name])
    assert codex_seat.main(argv) == 0
    assert len(calls) == 2
    assert out.read_text(encoding="utf-8") == report
    assert log.read_text(encoding="utf-8") == "Private transcript café\n"
    captured = capsys.readouterr()
    assert captured.out == f"{report}{out}\n"
    assert captured.err == ""


@pytest.mark.parametrize("seat", ["tester", "reviewer", "arch-reviewer"])
@pytest.mark.parametrize("session_status", [0, 1])
def test_a_previous_green_report_cannot_certify_a_run_that_writes_no_report(
    codex_seat: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    session_status: int,
    seat: str,
) -> None:
    """Reusing --out must not convert a missing current verdict into a green gate."""
    brief = tmp_path / "brief.md"
    brief.write_text("Verify current tree\n", encoding="utf-8")
    out = tmp_path / "report.md"
    out.write_text("GATE: green\nPrevious run\n", encoding="utf-8")
    monkeypatch.setattr(codex_seat, "find_project_root", lambda start: tmp_path)
    monkeypatch.setattr(codex_seat.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        codex_seat, "launch", Mock(return_value=Mock(wait=Mock(return_value=session_status)))
    )
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(codex_seat.subprocess, "run", run)
    assert (
        codex_seat.main(
            [
                seat,
                "--brief",
                str(brief),
                "--out",
                str(out),
            ]
        )
        == 5
    )


@pytest.mark.parametrize("worktree", [False, True])
def test_dry_run_discovers_toplevel_from_subdirectory_or_worktree(
    codex_seat: ModuleType,
    checkpoint_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    worktree: bool,
) -> None:
    """Git metadata, including a linked worktree's .git file, determines the project."""
    root = checkpoint_repo
    if worktree:
        root = tmp_path / "linked"
        git(checkpoint_repo, "worktree", "add", "--detach", str(root))
        assert (root / ".git").is_file()
    subdir = root / "nested"
    subdir.mkdir()
    (subdir / "brief.md").write_text("Verify nested project", encoding="utf-8")
    monkeypatch.chdir(subdir)
    monkeypatch.setattr(codex_seat.shutil, "which", lambda name: None)
    assert codex_seat.find_project_root(subdir) == root
    assert (
        codex_seat.main(["reviewer", "--brief", "brief.md", "--out", "report.md", "--dry-run"]) == 0
    )
    argv = shlex.split(capsys.readouterr().out.splitlines()[0])
    assert argv[argv.index("-o") + 1] == str(subdir / "report.md")
    assert not (subdir / "report.md.log").exists()
