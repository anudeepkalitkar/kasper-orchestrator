"""Offline Git subprocess helper for isolated integration repositories."""

import os
import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    """Run Git only in the supplied temp repository, ignoring user configuration."""
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    return result.stdout
