"""Decisions on the repository ledger, loaded once as collection-time test data."""

import json
from io import StringIO
from types import ModuleType
from unittest.mock import Mock

import pytest

from tests.conftest import REPO_ROOT
from tests.helpers.files import read_json

# Like importing the scripts, loading this input happens at collection, never in a unit test.
LEDGER = read_json(REPO_ROOT / ".claude/permissions-ledger.json")


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("aws s3 rm s3://b/k", "ask"),
        ("aws route53 change-resource-record-sets --x", "ask"),
        ("aws secretsmanager rotate-secret --x", "ask"),
        ("docker push img", "ask"),
        ("docker login", "ask"),
        ("docker compose push", "ask"),
        ('git -c "user.name=A B" merge feat/x', "ask"),
        ('git -c "user.name=A B" clean -fd', "ask"),
        ("gh --repo owner/repo variable set X --body=v", "ask"),
        ("aws route53 change-resource-record-sets --x y", "ask"),
        ("aws secretsmanager rotate-secret --secret-id s", "ask"),
        ("dropdb mydb", "ask"),
        ("cd x && FOO=1 timeout 5 /usr/bin/git merge y", "ask"),
        ("echo $(git merge y)", "ask"),
        ("aws s3 ls", "allow"),
        ("gh pr list", "allow"),
        ("docker ps", "allow"),
        ("gh variable set X", "ask"),
        ("gh secret delete X", "ask"),
        ("git merge feat/x", "ask"),
        ("git clean -fd", "ask"),
        ("dropdb db", "ask"),
        ("terraform apply", "ask"),
        ("terraform state rm x", "ask"),
        ("terraform import a b", "ask"),
        ('git -C "dir with spaces" merge feat/x', "ask"),
        ('git -C "dir with spaces" clean -fd', "ask"),
        ('terraform -chdir="dir with spaces" apply', "ask"),
        ("gh variable --repo=owner/repo set X --body=v", "ask"),
        ("gh secret --repo owner/repo delete X", "ask"),
        ("git log --merges", "allow"),
        ("git -C x diff", "allow"),
        ('git commit -m "merge notes"', "allow"),
        ("git status", "allow"),
        ("ls", "allow"),
        ("pytest tests/unit", "allow"),
        ("git merge-base a b", "allow"),
        ("terraform plan", "allow"),
    ],
)
@pytest.mark.parametrize("allow_source", ["grants", "allow_keys", "allow_patterns"])
def test_required_ledger_decisions(
    permission_gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    expected: str,
    allow_source: str,
) -> None:
    """Exercise the decision entrypoint so an absent decision cannot pass as ask."""
    tools = ["git", "gh", "aws", "docker", "terraform", "dropdb", "cd", "echo", "ls", "pytest"]
    ledger = {**LEDGER, "grants": [], "allow_keys": {}, "allow_patterns": []}
    if allow_source == "grants":
        ledger["grants"] = [{"key": tool} for tool in tools]
    elif allow_source == "allow_keys":
        ledger["allow_keys"] = dict.fromkeys(tools, "broad test permission")
    else:
        ledger["allow_patterns"] = [{"pattern": ".*"}]
    # Prove a grant covers this command so an unknown-command fallback cannot pass.
    assert permission_gate.unallowed_parts(command, ledger) == []
    monkeypatch.setattr(permission_gate, "load_ledger", lambda: ledger)
    monkeypatch.setattr(
        permission_gate.sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": command},
                }
            )
        ),
    )
    permission_gate.main()
    result = json.loads(capsys.readouterr().out)
    assert result["hookSpecificOutput"]["permissionDecision"] == expected


def test_repository_word_rules_are_well_formed_and_cover_guarded_tools() -> None:
    """Validate collection-loaded policy without filesystem access during the test."""
    rules = LEDGER["ask_words"]
    assert isinstance(rules, list)
    guarded_tools: set[str] = set()
    for rule in rules:
        assert isinstance(rule, dict)
        assert isinstance(rule["tool"], str) and rule["tool"]
        guarded_tools.add(rule["tool"])
        groups = rule["all"]
        assert isinstance(groups, list)
        # Empty outer groups intentionally guard every invocation (dropdb).
        for group in groups:
            assert isinstance(group, list) and group
            assert all(isinstance(word, str) and word for word in group)
    assert {"git", "gh", "aws", "docker", "terraform", "dropdb"} <= guarded_tools


@pytest.mark.parametrize(
    "pattern",
    [
        r"\bgh\s+pr\s+merge\b",
        r"\baws\s+[\w-]+\s+(delete|terminate|create|update|put|deploy|run|start|stop|reboot|"
        r"modify|register|deregister|invoke|cp|sync|mb|rb)[\w-]*\b",
    ],
)
def test_repository_retains_master_payload_guard_patterns(pattern: str) -> None:
    """Keep the exact master regex guards alongside token-based policy."""
    assert pattern in {entry["pattern"] for entry in LEDGER["ask_patterns"]}


def test_repository_retains_merge_and_aws_word_rules() -> None:
    """Regex restoration must preserve the complementary word-rule layer."""
    rules = LEDGER["ask_words"]
    assert any(rule["tool"] == "gh" and rule["all"] == [["pr"], ["merge"]] for rule in rules)
    assert any(
        rule["tool"] == "aws"
        and len(rule["all"]) == 1
        and {"cp", "mv", "sync", "run-", "invoke-", "delete-"} <= set(rule["all"][0])
        for rule in rules
    )


@pytest.mark.parametrize(
    ("command", "guarded"),
    [
        ("aws service delete-item", True),
        ("aws service --delete-item", False),
        ("aws service undelete-item", False),
        ("aws service delete", True),
        ("gh variable list", False),
        ("gh issue delete X", False),
        ("gh variable list && gh issue delete X", False),
        ("gh secret --repo owner/repo set X", True),
        ("echo git merge feature", False),
        ("git -C merge status", True),
        ("dropdb", True),
        ("gh --repo owner/repo pr merge 1", True),
        ("gh pr list", False),
        ("gh issue merge 1", False),
        ("aws --region us-east-1 s3 cp a s3://b/k", True),
        ("aws s3 ls", False),
    ],
)
def test_word_rules_preserve_token_and_command_boundaries(
    permission_gate: ModuleType, command: str, guarded: bool
) -> None:
    """Require whole words, all groups in one command, and explicit prefix matching."""
    ledger = {"ask_words": LEDGER["ask_words"]}
    assert (permission_gate.ask_match(command, ledger) is not None) is guarded


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("if git clean -fd; then echo done; fi", "ask"),
        ("while git merge x; do :; done", "ask"),
        ("until terraform apply; do :; done", "ask"),
        ("! git merge x", "ask"),
        ("{ git clean -fd; }", "ask"),
        ("(git merge x)", "ask"),
        ("printf x | xargs -I echo git clean -fd", "ask"),
        ("xargs -n1 git merge", "ask"),
        ("xargs --max-args=1 git merge", "ask"),
        ("timeout -s KILL 5 git merge x", "ask"),
        ("env -u FOO git merge x", "ask"),
        ("env -S 'git merge x'", "ask"),
        ("nice -n 5 terraform apply", "ask"),
        ("xargs -Z git merge", "ask"),
        ("python3 -c 'import os; os.system(\"terraform apply\")'", "ask"),
        ("case x in x) gh pr merge 1;; esac", "ask"),
        ("case x in x) aws s3 cp a s3://b/k;; esac", "ask"),
        ("python3 -c 'import os; os.system(\"gh pr merge 1\")'", "ask"),
        ("python3 - <<'PY'\nimport os\nos.system('terraform apply')\nPY", "ask"),
        ("git log --merges", "allow"),
        ("git -C x diff", "allow"),
        ('git commit -m "merge notes"', "allow"),
        ("terraform plan", "allow"),
        ("aws s3 ls", "allow"),
        ("gh pr list", "allow"),
        ("docker ps", "allow"),
        ('while read -r line; do echo "$line"; done', "prompt"),
        ("if git status; then echo ok; fi", "allow"),
        ("unset 'a[$(git cl\"\"ean -fd)]'", "ask"),
        ("[[ 'a[$(git cl\"\"ean -fd)]' -eq 0 ]]", "ask"),
        ("local 'a[$(git merge x)]'", "ask"),
        ("export 'a[$(git merge x)]'=1", "ask"),
        ("printf -v 'a[$(git merge x)]' x", "ask"),
        ("test -v 'a[$(git merge x)]'", "ask"),
        ("gh --repo x pr merge 1", "ask"),
        ("gh pr --repo x merge 1", "ask"),
        ("aws --region us-east-1 s3 cp a s3://b/k", "ask"),
        ("aws s3 sync . s3://b", "ask"),
        ("aws ec2 run-instances", "ask"),
        ("aws lambda invoke --function-name f out", "ask"),
        ("gh pr view 1", "allow"),
        ("true || exit 1", "allow"),
        ("declare 'a[$(x)]'", "ask"),
        ("let 'a[$(x)]'", "ask"),
        ("(( a[$(x)] ))", "ask"),
        ("((a[$(x)]))", "ask"),
        ("for (( i=$(x); ; ))", "ask"),
        ("eval '$(x)'", "ask"),
        ("printf '%s' '$(x)'", "ask"),
        ("echo '$(x)'", "allow"),
    ],
)
def test_pass_five_decisions_with_broad_grants(
    permission_gate: ModuleType,
    permission_unknown_log: Mock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    expected: str,
) -> None:
    """Guard conditions, wrappers and script payloads despite broad user grants."""
    tools = [
        "git",
        "gh",
        "aws",
        "docker",
        "terraform",
        "dropdb",
        "python3",
        "xargs",
        "printf",
        "test",
    ]
    ledger = {**LEDGER, "grants": [{"key": tool} for tool in tools]}
    # Unknown-command logging is faked for commands that defer to the native prompt.
    if expected == "allow":
        assert permission_gate.unallowed_parts(command, ledger) == []
    monkeypatch.setattr(permission_gate, "load_ledger", lambda: ledger)
    monkeypatch.setattr(
        permission_gate.sys,
        "stdin",
        StringIO(json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})),
    )
    permission_gate.main()
    output = capsys.readouterr().out
    if expected == "prompt":
        assert output == ""
        assert permission_gate.ask_match(command, ledger) is None
        permission_unknown_log.open.assert_called_once_with("a", encoding="utf-8")
        return
    assert output, f"Expected explicit {expected!r} decision; gate deferred with no output"
    result = json.loads(output)
    assert result["hookSpecificOutput"]["permissionDecision"] == expected
    assert (permission_gate.ask_match(command, ledger) is not None) is (expected == "ask")


@pytest.mark.parametrize("broad_grants", [False, True])
@pytest.mark.parametrize(
    "command",
    [
        "read 'a[$(git cl\"\"ean -fd)]' <<< x",
        r'read "a[\$(git merge x)]" <<< x',
        'while read -r line; do echo "$line"; done',
    ],
)
def test_read_requires_prompt_even_with_broad_grants(
    permission_gate: ModuleType,
    permission_unknown_log: Mock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    broad_grants: bool,
) -> None:
    """Read can evaluate array subscripts, so it must never be implicitly harmless."""
    ledger = {
        **LEDGER,
        "grants": [{"key": "git"}, {"key": "echo"}] if broad_grants else [],
        "allow_keys": {},
        "allow_patterns": [],
    }
    monkeypatch.setattr(permission_gate, "load_ledger", lambda: ledger)
    monkeypatch.setattr(
        permission_gate.sys,
        "stdin",
        StringIO(json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})),
    )
    permission_gate.main()
    output = capsys.readouterr().out
    if output:
        assert json.loads(output)["hookSpecificOutput"]["permissionDecision"] == "ask"
    else:
        permission_unknown_log.open.assert_called_once_with("a", encoding="utf-8")
