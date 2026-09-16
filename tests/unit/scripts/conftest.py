"""In-memory collaborators for the seat runner's unit tests."""

from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.fixture
def seat_files(codex_seat: ModuleType, monkeypatch: pytest.MonkeyPatch) -> dict[Path, str]:
    """Fake UTF-8 file reads; absent paths raise as missing files would."""
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
    return files


@pytest.fixture
def seat_process(codex_seat: ModuleType, monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Replace subprocess.run with a recording fake; never launch Codex."""
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(codex_seat.subprocess, "run", run)
    return run
