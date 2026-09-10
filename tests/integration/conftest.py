"""Fixtures for the integration tier: real installs into temporary Claude homes."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pytest

import install


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A Claude home path that does not exist yet — the installer must create it."""
    return tmp_path / "claude-home"


@pytest.fixture
def installed_home(repo_root: Path, home: Path, fixed_now: datetime) -> Path:
    """A home carrying one completed install of this repository, at the frozen instant."""
    install.install(repo_root, home, "python3", now=fixed_now, out=io.StringIO())
    return home
