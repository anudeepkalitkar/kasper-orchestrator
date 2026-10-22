"""Fixtures for the integration tier: real installs into temporary Claude homes."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import IO

import pytest

import install
from tests.helpers.git import git


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A Claude home path that does not exist yet — the installer must create it."""
    return tmp_path / "claude-home"


@pytest.fixture
def installed_home(repo_root: Path, home: Path, fixed_now: datetime) -> Path:
    """A home carrying one completed install of this repository, at the frozen instant."""
    install.install(repo_root, home, "python3", now=fixed_now, out=io.StringIO())
    return home


@pytest.fixture
def debate_project(
    debate: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repo_root: Path,
) -> Path:
    """Build an isolated project and replace CLI launches with controlled Python children."""
    (tmp_path / ".git/info").mkdir(parents=True)
    (tmp_path / ".git/info/exclude").write_text("unchanged\n", encoding="utf-8")
    (tmp_path / "claude-temp/sessions").mkdir(parents=True)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text(
        '{"permissions":{"deny":["Read(secret)"]}}', encoding="utf-8"
    )
    (tmp_path / "brief.md").write_text("Choose a design café", encoding="utf-8")
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name in ("propose", "critique", "judge", "architect", "researcher"):
        (prompts / f"{name}.md").write_text(f"PHASE {name}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty-home")
    monkeypatch.setattr(debate, "PROMPT_DIR", prompts)
    real_which = shutil.which
    monkeypatch.setattr(
        debate.shutil,
        "which",
        lambda vendor: f"/controlled/{vendor}"
        if vendor in ("claude", "codex")
        else real_which(vendor),
    )
    monkeypatch.setenv("PYTHONPATH", str(repo_root))
    real_popen = subprocess.Popen

    def popen(
        argv: list[str],
        *,
        stdin: int | IO[str],
        stdout: int | IO[str],
        stderr: int | IO[str],
        encoding: str | None = None,
        start_new_session: bool = False,
        creationflags: int = 0,
        cwd: Path | None = None,
    ) -> subprocess.Popen[str]:
        if argv[0] not in ("/controlled/claude", "/controlled/codex"):
            return real_popen(
                argv,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                encoding=encoding,
                start_new_session=start_new_session,
                creationflags=creationflags,
                cwd=cwd,
            )
        mode_path = tmp_path / "mode.txt"
        mode = mode_path.read_text(encoding="utf-8") if mode_path.exists() else "valid"
        return real_popen(
            [
                sys.executable,
                "-m",
                "tests.helpers.debate_child",
                Path(argv[0]).name,
                mode,
                *argv[1:],
            ],
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            encoding=encoding,
            start_new_session=start_new_session,
            creationflags=creationflags,
            cwd=cwd,
        )

    monkeypatch.setattr(debate.subprocess, "Popen", popen)
    return tmp_path


@pytest.fixture
def checkpoint_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an offline feature branch with a baseline and no user Git hooks/config."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.invalid")
    git(tmp_path, "init", "-b", "feat/test")
    git(tmp_path, "config", "core.hooksPath", str(tmp_path / "no-hooks"))
    git(tmp_path, "commit", "--allow-empty", "-m", "baseline")
    return tmp_path
