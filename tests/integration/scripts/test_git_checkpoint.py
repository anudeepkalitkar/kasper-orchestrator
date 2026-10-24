"""Checkpoint subprocesses against offline temporary repositories."""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers.git import git
from tests.helpers.hooks import load_script, script_path


def checkpoint(repo_root: Path, repo: Path, message: str) -> subprocess.CompletedProcess[str]:
    """Run the real stdin CLI with pushing explicitly disabled."""
    return subprocess.run(
        [sys.executable, str(script_path(repo_root, "git_checkpoint")), "--no-push"],
        cwd=repo,
        input=message,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize("filename", ["ordinary.txt", " leading space.txt"])
def test_stdin_message_and_staged_filename_preserved(
    repo_root: Path,
    checkpoint_repo: Path,
    filename: str,
) -> None:
    """Quotes, substitutions, and unusual filenames reach Git without shell interpretation."""
    (checkpoint_repo / filename).write_text("content\n", encoding="utf-8")
    message = "fix: \"quotes\" 'single' $(touch dollar-marker) `touch tick-marker`"
    result = checkpoint(repo_root, checkpoint_repo, message + "\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(checkpoint_repo, "log", "-1", "--format=%B").rstrip("\n") == message
    assert filename in result.stdout
    assert git(checkpoint_repo, "ls-files", "-z") == filename + "\0"
    assert not (checkpoint_repo / "dollar-marker").exists()
    assert not (checkpoint_repo / "tick-marker").exists()


def test_multiple_filenames_preserve_leading_and_trailing_spaces(
    repo_root: Path,
    checkpoint_repo: Path,
) -> None:
    """Checkpoint commits and reports every exact filename in a mixed path listing."""
    filenames = [" leading space.txt", "ordinary.txt", "trailing space.txt "]
    for filename in filenames:
        (checkpoint_repo / filename).write_text("content\n", encoding="utf-8")

    result = checkpoint(repo_root, checkpoint_repo, "preserve filename whitespace")

    assert result.returncode == 0, result.stdout + result.stderr
    assert git(checkpoint_repo, "ls-tree", "--name-only", "-z", "HEAD") == "".join(
        filename + "\0" for filename in filenames
    )
    assert "Staged:\n" + "".join(f"    {filename}\n" for filename in filenames) in result.stdout
    assert git(checkpoint_repo, "status", "--porcelain=v1", "-z") == ""


@pytest.mark.parametrize(
    "path",
    [
        ".env.local",
        "nested/.env",
        "tasks/a.md",
        "claude-temp/a",
        "claude-memory/a",
        "docs/adr/a",
    ],
)
@pytest.mark.parametrize("state", ["staged", "changed", "untracked"])
def test_refuses_every_pending_local_path_without_mutation(
    repo_root: Path,
    checkpoint_repo: Path,
    path: str,
    state: str,
) -> None:
    """Staged, unstaged tracked, and untracked local files all block the whole checkpoint."""
    file = checkpoint_repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("placeholder\n", encoding="utf-8")
    if state != "untracked":
        git(checkpoint_repo, "add", "--", path)
    if state == "changed":
        git(checkpoint_repo, "commit", "-m", "fixture baseline")
        file.write_text("changed placeholder\n", encoding="utf-8")
    if path.startswith("docs/adr/"):
        (checkpoint_repo / ".git/info/exclude").write_text("/docs/adr/\n", encoding="utf-8")
        if state == "untracked":
            git(checkpoint_repo, "add", "--force", "--", path)
    (checkpoint_repo / "safe.txt").write_text("safe\n", encoding="utf-8")
    before_head = git(checkpoint_repo, "rev-parse", "HEAD")
    before_status = git(checkpoint_repo, "status", "--porcelain=v1", "-z")
    result = checkpoint(repo_root, checkpoint_repo, "must refuse")
    assert result.returncode == 1, result.stdout + result.stderr
    assert path in result.stderr
    assert git(checkpoint_repo, "rev-parse", "HEAD") == before_head
    assert git(checkpoint_repo, "status", "--porcelain=v1", "-z") == before_status


def test_detached_head_refused(repo_root: Path, checkpoint_repo: Path) -> None:
    """A detached HEAD must not acquire a new checkpoint commit."""
    git(checkpoint_repo, "checkout", "--detach")
    (checkpoint_repo / "safe.txt").write_text("safe", encoding="utf-8")
    result = checkpoint(repo_root, checkpoint_repo, "must refuse")
    assert result.returncode == 1
    assert "detached HEAD" in result.stderr
    assert git(checkpoint_repo, "rev-list", "--count", "HEAD").strip() == "1"


def test_empty_stdin_refused(repo_root: Path, checkpoint_repo: Path) -> None:
    """An empty message fails before staging pending files."""
    (checkpoint_repo / "safe.txt").write_text("safe", encoding="utf-8")
    result = checkpoint(repo_root, checkpoint_repo, " \n")
    assert result.returncode == 2
    assert "message is required" in result.stderr
    assert git(checkpoint_repo, "diff", "--cached", "--name-only") == ""


def test_only_commits_declared_adr_and_leaves_other_changes_unstaged(
    repo_root: Path,
    checkpoint_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a local-only exclude entry an explicitly selected ADR is committable."""
    adr = checkpoint_repo / "docs/adr/decision.md"
    adr.parent.mkdir(parents=True)
    adr.write_text("Decision", encoding="utf-8")
    (checkpoint_repo / "other.py").write_text("pending", encoding="utf-8")
    monkeypatch.chdir(checkpoint_repo)
    module = load_script(repo_root, "git_checkpoint")
    assert module.main(["record decision", "--no-push", "--only", "docs/adr/decision.md"]) == 0
    assert git(checkpoint_repo, "show", "--pretty=", "--name-only", "HEAD").strip() == (
        "docs/adr/decision.md"
    )
    assert git(checkpoint_repo, "diff", "--cached", "--name-only") == ""
    assert git(checkpoint_repo, "status", "--porcelain").strip() == "?? other.py"


def test_only_refuses_prestaged_rename_from_undeclared_path(
    repo_root: Path, checkpoint_repo: Path
) -> None:
    """Both sides of a rename must be authorized before any index or HEAD mutation."""
    (checkpoint_repo / "other.py").write_text("value = 1\n", encoding="utf-8")
    git(checkpoint_repo, "add", "other.py")
    git(checkpoint_repo, "commit", "-m", "seed original")
    git(checkpoint_repo, "mv", "other.py", "allowed.py")
    before_head = git(checkpoint_repo, "rev-parse", "HEAD")
    before_index = git(checkpoint_repo, "diff", "--cached", "--binary")
    result = subprocess.run(
        [
            sys.executable,
            str(script_path(repo_root, "git_checkpoint")),
            "must refuse",
            "--no-push",
            "--only",
            "allowed.py",
        ],
        cwd=checkpoint_repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "other.py" in result.stderr
    assert git(checkpoint_repo, "rev-parse", "HEAD") == before_head
    assert git(checkpoint_repo, "diff", "--cached", "--binary") == before_index


def test_only_commits_tracked_worktree_deletion(
    repo_root: Path, checkpoint_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deleted file still in the index is a valid scoped checkpoint operand."""
    path = checkpoint_repo / "deleted.py"
    path.write_text("value = 1\n", encoding="utf-8")
    git(checkpoint_repo, "add", "--", path.name)
    git(checkpoint_repo, "commit", "-m", "seed tracked file")
    path.unlink()
    monkeypatch.chdir(checkpoint_repo)
    module = load_script(repo_root, "git_checkpoint")
    assert module.main(["record deletion", "--no-push", "--only", path.name]) == 0
    assert git(checkpoint_repo, "ls-tree", "--name-only", "HEAD") == ""
    assert git(checkpoint_repo, "log", "-1", "--format=%s").strip() == "record deletion"
