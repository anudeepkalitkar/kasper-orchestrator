"""In-memory collaborators for the seat runner's unit tests."""

from pathlib import Path
from types import ModuleType
from typing import cast
from unittest.mock import Mock, mock_open

import pytest


@pytest.fixture
def seat_project(codex_seat: ModuleType, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Model a caller's repository independently of the script's installation path."""
    project = Path("/projects/caller repository")
    # argparse's gettext lookup otherwise probes locale files on every parse.
    monkeypatch.setattr(codex_seat.argparse, "_", lambda message: message)
    monkeypatch.setattr(Path, "cwd", Mock(return_value=project))
    monkeypatch.setattr(Path, "is_dir", lambda path: path == project / ".git")
    return project


@pytest.fixture
def seat_files(
    codex_seat: ModuleType, monkeypatch: pytest.MonkeyPatch, seat_log: Mock, seat_project: Path
) -> dict[Path, str]:
    """Fake reads and cleanup; seeded reports represent the fake exec's output."""
    files = {
        codex_seat.ROLE_PROMPT_DIR / "tester.md": "Tester role\n",
        codex_seat.ROLE_PROMPT_DIR / "reviewer.md": "Reviewer role\n",
        Path("brief.md"): "Task brief\n",
    }

    def read_text(path: Path, encoding: str | None = None) -> str:
        assert encoding == "utf-8"
        if path not in files:
            raise FileNotFoundError(str(path))
        return files[path]

    monkeypatch.setattr(Path, "read_text", read_text)
    # Report freshness is exercised with real files in the integration tier.
    monkeypatch.setattr(Path, "unlink", Mock())
    return files


@pytest.fixture
def seat_log(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Record transcript opens and supply an in-memory context-managed handle."""
    opened = mock_open()

    def open_text(path: Path, mode: str, *, encoding: str) -> Mock:
        return cast(Mock, opened(path, mode, encoding=encoding))

    monkeypatch.setattr(Path, "open", open_text)
    return cast(Mock, opened)


@pytest.fixture
def seat_process(codex_seat: ModuleType, monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Replace subprocess.run with a recording fake; never launch Codex."""
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(codex_seat.subprocess, "run", run)
    return run
