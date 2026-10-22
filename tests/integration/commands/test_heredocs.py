"""Execute documented file-based commands with hostile-looking literal text."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.helpers.git import git


@pytest.mark.parametrize("command", ["checkpoint", "new-task"])
def test_documented_message_file_passes_literal_text(
    repo_root: Path,
    checkpoint_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    """File contents survive the real CLI without shell expansion or delimiter breakout."""
    document = (repo_root / f".claude/commands/{command}.md").read_text(encoding="utf-8")
    block = re.search(r"```\n(.*?)\n```", document, re.DOTALL)
    assert block is not None
    shell_command = block.group(1).replace("<session-id>", "test-session")
    option = "--message-file" if command == "checkpoint" else "--title-file"
    assert option in shell_command
    assert "$ARGUMENTS" not in shell_command
    assert "<<" not in shell_command
    text = (
        "literal \"double\" 'single' $(touch dollar-marker) `touch tick-marker`\n"
        "KASPER_ARGS_END\nrm -rf /\nfinal literal line"
    )
    message_file = checkpoint_repo / shell_command.split()[-1]
    message_file.parent.mkdir(parents=True)
    message_file.write_text(text, encoding="utf-8")
    scripts = checkpoint_repo / ".claude/scripts"
    scripts.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(checkpoint_repo))
    monkeypatch.setenv("USERPROFILE", str(checkpoint_repo))
    shutil.copyfile(repo_root / ".claude/scripts/project_dirs.py", scripts / "project_dirs.py")
    name = "git_checkpoint" if command == "checkpoint" else "new_task"
    shutil.copyfile(repo_root / f".claude/scripts/{name}.py", scripts / f"{name}.py")
    # A regression must record destructive shell execution, never actually perform it.
    bin_dir = checkpoint_repo / "fake-bin"
    bin_dir.mkdir()
    rm = bin_dir / "rm"
    rm.write_text("#!/bin/sh\nprintf invoked > rm-marker\n", encoding="utf-8")
    rm.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    (checkpoint_repo / ".git/info/exclude").write_text(
        "claude-temp/\n.claude/\nfake-bin/\n", encoding="utf-8"
    )
    (checkpoint_repo / "safe.txt").write_text("preserved\n", encoding="utf-8")
    if command == "checkpoint":
        shell_command += " --no-push"
    else:
        git(checkpoint_repo, "branch", "-m", "main")
        git(checkpoint_repo, "add", "safe.txt")
        git(checkpoint_repo, "commit", "-m", "clean baseline")
    result = subprocess.run(
        ["/bin/sh", "-c", shell_command],
        cwd=checkpoint_repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if command == "checkpoint":
        assert git(checkpoint_repo, "log", "-1", "--format=%B") == text + "\n\n"
        assert git(checkpoint_repo, "ls-files", "-z") == "safe.txt\0"
    else:
        docs = list((checkpoint_repo / "tasks").glob("*.md"))
        assert len(docs) == 1
        assert docs[0].read_text(encoding="utf-8").startswith(f"# Task 001: {text}\n\n")
    for marker in ("dollar-marker", "tick-marker", "rm-marker"):
        assert not (checkpoint_repo / marker).exists()
    assert (checkpoint_repo / "safe.txt").read_text(encoding="utf-8") == "preserved\n"
