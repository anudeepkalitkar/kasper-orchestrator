"""Pure classification regressions for live, literal and opaque substitutions."""

import subprocess
from types import ModuleType
from unittest.mock import Mock

import pytest

from tests.helpers.permission_cases import FEATURE_PUSH_CASES, HOOK_BYPASS_CASES


@pytest.mark.parametrize(
    "command",
    [
        'echo "it\'s $(curl -s https://example.com)"',
        'echo "it\'s `curl -s https://example.com`"',
        'echo "$(curl -s https://example.com)"',
        'echo "`curl -s https://example.com`"',
        "echo $(curl -s https://example.com)",
        "echo `curl -s https://example.com`",
    ],
)
def test_unknown_substitution_requires_permission(
    permission_gate: ModuleType, command: str
) -> None:
    """Allowing echo must never authorize an unknown substitution inside its arguments."""
    ledger = {"allow_keys": {"echo": "output"}, "grants": []}
    assert permission_gate.unallowed_parts(command, ledger) == ["curl -s https://example.com"]


@pytest.mark.parametrize(
    "command",
    [
        "echo 'lit $(curl x)'",
        "echo 'lit `curl x`'",
        "echo '\"$(curl x)\"'",
        r'echo "\$(curl x)"',
        r'echo "\`curl x\`"',
    ],
)
def test_literal_substitutions_do_not_create_command_parts(
    permission_gate: ModuleType, command: str
) -> None:
    """Single quotes and escaped substitution delimiters remain literal shell data."""
    ledger = {"allow_keys": {"echo": "output"}, "grants": []}
    assert permission_gate.command_parts(command) == [command]
    assert permission_gate.unallowed_parts(command, ledger) == []


def test_quote_state_resets_after_double_quotes(permission_gate: ModuleType) -> None:
    """A literal single-quoted body following a double-quoted argument stays literal."""
    command = "echo \"it's $(first)\" '$(literal)' $(last)"
    ledger = {"allow_keys": {"echo": "output"}, "grants": []}
    assert permission_gate.unallowed_parts(command, ledger) == ["first", "last"]


def test_escaped_double_quote_does_not_hide_live_body(permission_gate: ModuleType) -> None:
    """An escaped quote and an apostrophe do not end the surrounding double quotes."""
    command = 'echo "say \\"it\'s $(curl x)\\""'
    ledger = {"allow_keys": {"echo": "output"}, "grants": []}
    assert permission_gate.unallowed_parts(command, ledger) == ["curl x"]


def test_granted_substitution_is_allowed(permission_gate: ModuleType) -> None:
    """An extracted body still uses existing ledger grants for its own authorization."""
    ledger = {"allow_keys": {"echo": "output"}, "grants": [{"key": "brew"}]}
    assert permission_gate.unallowed_parts('echo "it\'s $(brew --version)"', ledger) == []


@pytest.mark.parametrize("body", ["pwd", "git rev-parse HEAD", "date"])
@pytest.mark.parametrize("form", ["$({body})", '"$({body})"', "`{body}`", '"`{body}`"'])
def test_simple_substitution_classifies_body(
    permission_gate: ModuleType, body: str, form: str
) -> None:
    """Simple bodies remain separate pending commands in either outer quote context."""
    command = "echo " + form.format(body=body)
    ledger = {"allow_keys": {"echo": "output"}, "grants": []}
    assert permission_gate.command_parts(command)[1:] == [body]
    assert permission_gate.unallowed_parts(command, ledger) == [body]


@pytest.mark.parametrize("command", ["echo '$(curl x)'", "echo '`curl x`'"])
def test_single_quoted_body_is_not_extracted(permission_gate: ModuleType, command: str) -> None:
    """A wholly single-quoted opener contributes no inner command."""
    ledger = {"allow_keys": {"echo": "output"}, "grants": []}
    assert permission_gate._extract_substitutions(command) == (command, [])
    assert permission_gate.unallowed_parts(command, ledger) == []


@pytest.mark.parametrize(
    "body",
    ["a 'b'", 'a "b"', r"a \b", "a #b", "a `b`", "a $(b)"],
    ids=["single-quote", "double-quote", "backslash", "hash", "backtick", "nested-dollar"],
)
@pytest.mark.parametrize("quoted", [False, True], ids=["unquoted", "double-quoted"])
def test_opaque_body_emits_one_sentinel_and_preserves_outer_commands(
    permission_gate: ModuleType, body: str, quoted: bool
) -> None:
    """Each opacity trigger replaces the whole body while printf still needs permission."""
    substitution = f"$({body})"
    if quoted:
        substitution = f'"{substitution}"'
    command = f"echo {substitution}; printf X"
    outer = 'echo " __SUB__ "' if quoted else "echo  __SUB__ "
    sentinel = permission_gate._OPAQUE_SUBSTITUTION
    ledger = {"allow_keys": {"echo": "output"}, "grants": []}
    assert sentinel == "__opaque_substitution__"
    assert permission_gate.command_parts(command) == [outer, " printf X", sentinel]
    assert permission_gate.unallowed_parts(command, ledger) == [" printf X", sentinel]


@pytest.mark.parametrize("body", ["a 'b'", 'a "b"', r"a \b", "a #b", "a $(b)"])
def test_opaque_backtick_body_is_not_classified(permission_gate: ModuleType, body: str) -> None:
    """Backtick bodies use the same opacity rule before any command classification."""
    command = f"echo `{body}`"
    sentinel = permission_gate._OPAQUE_SUBSTITUTION
    ledger = {"allow_keys": {"echo": "output"}, "grants": []}
    assert permission_gate.command_parts(command) == ["echo  __SUB__ ", sentinel]
    assert permission_gate.unallowed_parts(command, ledger) == [sentinel]


@pytest.mark.parametrize(
    "command",
    ["echo $(", "echo $(echo ok", "echo `", "echo `echo ok", 'echo "$(foo', 'echo "`foo'],
)
def test_unterminated_substitution_requires_permission(
    permission_gate: ModuleType, command: str
) -> None:
    """Missing closers produce one pending sentinel even for an allowed command head."""
    sentinel = permission_gate._OPAQUE_SUBSTITUTION
    ledger = {"allow_keys": {"echo": "output"}, "grants": []}
    assert permission_gate.command_parts(command).count(sentinel) == 1
    assert permission_gate.unallowed_parts(command, ledger) == [sentinel]


def test_excessive_unclosed_nesting_fails_closed(permission_gate: ModuleType) -> None:
    """A thousand open substitutions prompt without exhausting Python's stack."""
    command = "echo " + "$(" * 1000
    sentinel = permission_gate._OPAQUE_SUBSTITUTION
    ledger = {"allow_keys": {"echo": "output"}, "grants": []}
    assert permission_gate.command_parts(command) == ["echo  __SUB__ ", sentinel]
    assert permission_gate.unallowed_parts(command, ledger) == [sentinel]


# Pass 8 reviewer probes, with literal HEAD parts pinned from the probe table.
_REVIEWER_PROBES: list[tuple[str, list[str], bool]] = [
    (
        "echo \"it's $(echo '(')\"; unknown_command",
        ["echo \"it's $(echo '(')\"", " unknown_command"],
        True,
    ),
    (
        "echo `echo 'x` ; printf REVIEW_MARKER; echo `echo x'`",
        ["echo  __SUB__  ", " printf REVIEW_MARKER", " echo  __SUB__ ", "echo 'x", "echo x'"],
        False,
    ),
    (
        'echo `echo "\\`echo ok; printf REVIEW_MARKER\\`"`',
        ["echo  __SUB__ echo ok", ' printf REVIEW_MARKER\\`"`', 'echo "\\'],
        False,
    ),
    (
        "echo $(echo ok # '\n); printf MARK # ')",
        ["echo  __SUB__ ", " printf MARK # ')", "echo ok # '\n"],
        False,
    ),
    (
        "echo \"$(echo a\\;#'foo\nbar')\"; printf MARK # ' )",
        ['echo " __SUB__ "', " printf MARK # ' )", "echo a\\;#'foo\nbar'"],
        False,
    ),
]


@pytest.mark.parametrize(("command", "head_parts", "sentinel_only"), _REVIEWER_PROBES)
def test_reviewer_bypass_prompts_and_retains_head_parts_or_sentinel(
    permission_gate: ModuleType, command: str, head_parts: list[str], sentinel_only: bool
) -> None:
    """Every reviewer bypass prompts; changed HEAD parts must carry an opaque sentinel."""
    ledger = {"allow_keys": {"echo": "output"}, "grants": []}
    parts = permission_gate.command_parts(command)
    pending = permission_gate.unallowed_parts(command, ledger)
    sentinel = permission_gate._OPAQUE_SUBSTITUTION
    assert pending != []
    assert parts == head_parts or sentinel in parts
    if sentinel_only:
        assert pending == [sentinel]


@pytest.mark.parametrize(
    ("command", "guarded"),
    HOOK_BYPASS_CASES,
)
def test_hook_bypass_guard_precedes_commit_allowance(
    permission_gate: ModuleType,
    command: str,
    guarded: bool,
) -> None:
    """Hook bypasses require permission even when ordinary local commits are allowed."""
    ledger = {
        "allow_keys": {"git commit": "local", "git status": "local", "echo": "output"},
        "ask_patterns": [
            {"pattern": r"^git\b[^&|;\n]*(--no-verify|core\.hooksPath)", "note": "hooks"}
        ],
        "grants": [],
    }
    assert permission_gate.ask_match(command, ledger) == ("hooks" if guarded else None)
    if not guarded:
        assert permission_gate.unallowed_parts(command, ledger) == []


@pytest.mark.parametrize(("depth", "allowed"), [(5, True), (6, False), (1000, False)])
def test_wrapper_depth_limit_fails_closed(
    permission_gate: ModuleType, depth: int, allowed: bool
) -> None:
    """Readable wrappers preserve permission; excessive nesting must still prompt."""
    command = "env " * depth + "git commit -m x"
    ledger = {"allow_keys": {"git commit": "local"}, "grants": []}
    assert (permission_gate.unallowed_parts(command, ledger) == []) is allowed


@pytest.mark.parametrize(("command", "allowed"), FEATURE_PUSH_CASES)
def test_feature_push_allow_pattern(
    permission_gate: ModuleType, command: str, allowed: bool
) -> None:
    """Feature prefixes are allowed while development and master still need permission."""
    ledger = {
        "allow_patterns": [
            {"pattern": r"\bgit\s+push\b.*\b(feat|fix|feature|docs|chore|refactor|test)/"}
        ],
    }
    assert permission_gate.unallowed_parts(command, ledger) == ([] if allowed else [command])


@pytest.mark.parametrize(
    ("wrapper", "options"),
    [
        ("xargs", ["-I", "echo"]),
        ("xargs", ["-n", "1"]),
        ("xargs", ["-P", "2"]),
        ("xargs", ["-L", "1"]),
        ("xargs", ["--max-args=1"]),
        ("xargs", ["-n1"]),
        ("timeout", ["-s", "KILL", "-k", "2"]),
        ("timeout", ["--signal=KILL"]),
        ("env", ["-u", "FOO", "-C", "directory with spaces"]),
        ("env", ["--unset=FOO"]),
        ("nice", ["-n", "5"]),
        ("nice", ["--adjustment=5"]),
        ("time", ["-f", "%E", "-o", "timing.txt"]),
        ("time", ["--output=timing.txt"]),
        ("xargs", ["-0", "-r", "--"]),
        ("xargs", []),
    ],
)
def test_wrapper_options_preserve_the_wrapped_command(
    permission_gate: ModuleType, wrapper: str, options: list[str]
) -> None:
    """Consume option values without mistaking them for executable names."""
    command = ["5", "git", "merge", "x"] if wrapper == "timeout" else ["git", "merge", "x"]
    assert permission_gate._skip_wrapper_options(wrapper, options + command) == command


@pytest.mark.parametrize(
    ("wrapper", "options"),
    [
        ("xargs", ["-Z"]),
        ("xargs", ["--unknown=value"]),
        ("xargs", ["-rt"]),
        ("env", ["-iv"]),
        ("env", ["-S", "git merge x"]),
        ("env", ["--split-string=git merge x"]),
    ],
)
def test_unreadable_wrapper_options_fail_closed(
    permission_gate: ModuleType, wrapper: str, options: list[str]
) -> None:
    """Unknown options, combined flags and split-string cannot authorize a command."""
    assert permission_gate._skip_wrapper_options(wrapper, options + ["git", "status"]) is None


@pytest.mark.parametrize(
    "command",
    [
        "if git merge x; then echo done; fi",
        "while git merge x; do echo done; done",
        "until git merge x; do echo done; done",
        "! git merge x",
        "{ git merge x; }",
        "(git merge x)",
        "printf x | xargs -I echo git merge x",
        "timeout -s KILL 5 git merge x",
    ],
)
def test_condition_and_wrapper_commands_reach_both_permission_checks(
    permission_gate: ModuleType, command: str
) -> None:
    """Regex guards cannot mask a condition or wrapper skipped by token classification."""
    ledger = {
        "allow_keys": {"echo": "output", "printf": "output"},
        "ask_words": [{"tool": "git", "all": [["merge"]], "note": "human merge"}],
    }
    assert permission_gate.unallowed_parts(command, ledger)
    assert permission_gate.ask_match(command, ledger) == "human merge"


@pytest.mark.parametrize(
    "command",
    [
        "declare 'a[$(x)]'",
        "let 'a[$(x)]'",
        "(( a[$(x)] ))",
        "((a[$(x)]))",
        "for (( i=$(x); ; ))",
        "eval '$(x)'",
        "printf '%s' '$(x)'",
        "declare 'a[`x`]'",
        "declare 'a[<(x)]'",
        "timeout -s KILL 5 eval '$(x)'",
        "xargs -Z git status",
    ],
)
def test_evaluated_payload_requires_confirmation_note(
    permission_gate: ModuleType, command: str
) -> None:
    """Evaluated text and unreadable wrappers require an explicit ask without ledger rules."""
    note = permission_gate.evaluated_payload(command)
    assert isinstance(note, str) and note.strip()
    assert permission_gate.ask_match(command, {})


def test_plain_arithmetic_expansion_has_no_evaluated_payload_ask(
    permission_gate: ModuleType,
) -> None:
    """Arithmetic without a payload must not trigger the new evaluation guard."""
    command = "echo $(( 1 + 2 ))"
    ledger = {"allow_keys": {"echo": "output"}}
    assert permission_gate.evaluated_payload(command) is None
    assert permission_gate.ask_match(command, ledger) is None


@pytest.mark.parametrize("command", ["echo '$(x)'", "git log --merges", "true || exit 1"])
def test_literal_payload_and_safe_scaffolding_remain_allowed(
    permission_gate: ModuleType, command: str
) -> None:
    """Literal echo text and harmless control flow must not acquire an evaluation guard."""
    ledger = {"allow_keys": {"echo": "output", "git": "broad", "true": "scaffolding"}}
    assert permission_gate.evaluated_payload(command) is None
    assert permission_gate.ask_match(command, ledger) is None
    assert permission_gate.unallowed_parts(command, ledger) == []


@pytest.mark.parametrize(
    ("command", "branch", "origin_head", "denied", "directory"),
    [
        ("git push", "feat/x", "trunk", False, "/repo"),
        ("git push -u origin HEAD", "feat/x", "trunk", False, "/repo"),
        ("git push origin feat/x", "feat/x", "trunk", False, "/repo"),
        ("git push origin v1.0.0", "feat/x", "trunk", False, "/repo"),
        ("git push --all", "feat/x", None, True, "/repo"),
        ("git push --mirror", "feat/x", None, True, "/repo"),
        ("git push --branches", "feat/x", None, True, "/repo"),
        ("git push origin +feat/x", "feat/x", None, True, "/repo"),
        ("git push origin HEAD:master", "feat/x", None, True, "/repo"),
        ("git push --repo origin master", "feat/x", None, True, "/repo"),
        ("git push --repo=origin master", "feat/x", None, True, "/repo"),
        ("git push -u origin master", "feat/x", None, True, "/repo"),
        ("git push --force", "feat/x", None, True, "/repo"),
        ("git push -f", "feat/x", None, True, "/repo"),
        ("git push --force-with-lease", "feat/x", None, True, "/repo"),
        ("git push", "master", None, True, "/repo"),
        ("git push", None, None, True, "/repo"),
        ("git push origin HEAD", None, None, True, "/repo"),
        ("cd /x && git push", "master", None, True, "/x"),
        ("git -C /x push", "master", None, True, "/x"),
        ("git push origin feat/x:trunk", "feat/x", "trunk", True, "/repo"),
        ("git push origin HEAD:development", "feat/x", None, True, "/repo"),
        ("git push origin HEAD:main", "feat/x", None, True, "/repo"),
        ("git push origin HEAD:qa", "feat/x", None, True, "/repo"),
        ("git push origin HEAD:refs/heads/master", "feat/x", None, True, "/repo"),
        ('git push origin "HEAD:$TARGET"', "feat/x", None, True, "/repo"),
        ("git push origin 'refs/heads/*:refs/heads/*'", "feat/x", None, True, "/repo"),
    ],
)
def test_push_denial_enforces_destination_and_force_policy(
    permission_gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    branch: str | None,
    origin_head: str | None,
    denied: bool,
    directory: str,
) -> None:
    """Ordinary pushes proceed; force, shared, and unreadable destinations are blocked."""
    lookup = Mock(return_value=(branch, origin_head))
    monkeypatch.setattr(permission_gate, "_branch_state", lookup)
    monkeypatch.setattr(
        permission_gate.subprocess, "run", Mock(side_effect=AssertionError("real subprocess"))
    )
    reason = permission_gate.push_denial(command, "/repo")
    if denied:
        assert isinstance(reason, str) and reason
    else:
        assert reason is None
    if lookup.called:
        lookup.assert_called_once_with(directory)


@pytest.fixture
def push_gate(permission_gate: ModuleType, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Fake branch discovery and reject any accidental real subprocess call."""

    def branch_state(cwd: str | None) -> tuple[str | None, str | None]:
        return ("feat/x", "trunk") if cwd is not None else (None, None)

    monkeypatch.setattr(permission_gate, "_branch_state", branch_state)
    monkeypatch.setattr(
        permission_gate.subprocess, "run", Mock(side_effect=AssertionError("real subprocess"))
    )
    return permission_gate


@pytest.mark.parametrize(
    "options",
    [
        "--repo origin",
        "--repo=origin",
        "--push-option master origin",
        "--push-option=master origin",
        "--receive-pack master origin",
        "--receive-pack=master origin",
        "--exec master origin",
        "--exec=master origin",
        "--recurse-submodules on-demand origin",
        "--recurse-submodules=on-demand origin",
        "-o master origin",
        "-omaster origin",
    ],
)
@pytest.mark.parametrize("destination", ["master", "feat/x"])
def test_push_value_options_preserve_destination(
    push_gate: ModuleType, options: str, destination: str
) -> None:
    """Option values must neither hide shared destinations nor become refspecs."""
    reason = push_gate.push_denial(f"git push {options} {destination}", "/repo")
    if destination == "master":
        assert isinstance(reason, str) and reason
    else:
        assert reason is None


@pytest.mark.parametrize(
    "option",
    [
        "--al",
        "--rep",
        "--rep=origin",
        "-uo",
        "--prune",
        "--future-option",
        "--force-with-lease=refs/heads/feat/x:abc",
        "--force-if-includes",
        "-uf",
    ],
)
def test_unknown_and_force_push_options_deny(push_gate: ModuleType, option: str) -> None:
    """Abbreviations, unsupported options and every force form fail closed."""
    reason = push_gate.push_denial(f"git push {option} origin feat/x", "/repo")
    assert isinstance(reason, str) and reason


@pytest.mark.parametrize(
    "option",
    [
        "--set-upstream",
        "--tags",
        "--follow-tags",
        "--no-follow-tags",
        "--delete",
        "--dry-run",
        "--porcelain",
        "--atomic",
        "--no-atomic",
        "--quiet",
        "--verbose",
        "--progress",
        "--no-progress",
        "--thin",
        "--no-thin",
        "--verify",
        "--no-verify",
        "--signed",
        "--signed=true",
        "--signed=if-asked",
        "--no-signed",
        "--ipv4",
        "--ipv6",
        "--no-recurse-submodules",
        "-u",
        "-q",
        "-v",
        "-n",
        "-d",
        "-4",
        "-6",
        "-uqvn4",
    ],
)
@pytest.mark.parametrize("destination", ["master", "feat/x"])
def test_known_push_flags_preserve_destination(
    push_gate: ModuleType, option: str, destination: str
) -> None:
    """Known flags allow feature pushes while retaining the shared-branch guard."""
    reason = push_gate.push_denial(f"git push {option} origin {destination}", "/repo")
    if destination == "master":
        assert isinstance(reason, str) and reason
    else:
        assert reason is None


@pytest.mark.parametrize(
    ("option", "forbidden"),
    [
        ("-C /x", False),
        ("-c push.default=matching", True),
        ("--git-dir /x", True),
        ("--git-dir=/x", True),
        ("--work-tree /x", True),
        ("--work-tree=/x", True),
        ("--namespace push", True),
        ("--namespace=push", True),
        ("--super-prefix push", True),
        ("--super-prefix=push", True),
        ("--attr-source push", True),
        ("--attr-source=push", True),
        ("--config-env push.default=POLICY", True),
        ("--config-env=push.default=POLICY", True),
    ],
)
@pytest.mark.parametrize("arguments", ["", "origin HEAD", "origin master", "origin feat/x"])
def test_global_options_do_not_hide_push_or_infer_redirected_destination(
    push_gate: ModuleType, option: str, forbidden: bool, arguments: str
) -> None:
    """Only directory and pager options may precede a provably safe push."""
    reason = push_gate.push_denial(f"git {option} push {arguments}", "/repo")
    denied = forbidden or arguments == "origin master"
    if denied:
        assert isinstance(reason, str) and reason
    else:
        assert reason is None


@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git push -u origin HEAD",
        "git push -u origin feat/x",
        "git push origin v1.0.0",
        "cd /repo && git push",
        "git -C /repo push -u origin HEAD",
        "git -C/repo push",
        "git --no-pager push",
        "git push 2>&1",
        "git push >/dev/null",
        "git push > output",
        "git push --tags",
        "git push origin --delete feat/x",
        "git push origin :feat/x",
        "git push origin HEAD:refs/heads/feat/y",
        "git push -u origin feat/x 2>&1 | tail -5",
        "git status && git push",
        "git push origin @",
        "if git push; then echo done; fi",
        "! git push",
        "{ git push; }",
    ],
)
def test_proven_feature_pushes_allow(push_gate: ModuleType, command: str) -> None:
    """Core overnight commands remain usable when branch discovery succeeds."""
    assert push_gate.push_denial(command, "/repo") is None


@pytest.mark.parametrize(
    "command",
    [
        "git push origin :",
        "git push origin feat/x:",
        "git push origin a:b:c",
        "git push origin feat/x:@",
        "git push origin HEAD:heads/master",
        "git push origin HEAD:refs/master",
        "git push origin HEAD:Master",
        "git push origin :master",
        "git push origin --delete master",
        "git push origin feat/x:main",
        "git push origin $(destination)",
        "git push origin 'feat/with space'",
        "GIT_WORK_TREE=/shared git push",
        "nohup git push",
        "nice git push",
        "time git push",
        "command git push",
        "xargs -Z git push",
        "cd /feature; git push",
        "cd /feature && git status && git push",
        "cd /feature && git push || git push",
        "builtin cd /feature; git push",
        ". setup; git push",
        "git push --repo origin :",
        "git push origin HEAD:heads/trunk",
        "git push origin HEAD:refs/heads/TRUNK",
        "git push origin HEAD:ma{ster,in}",
        "git push $OPTIONS origin feat/x",
        "(cd /feature); git push",
        "cd /feature | git push",
        "true || cd /feature; git push",
        "pushd /feature; git push",
        "popd; git push",
        "eval cd /feature; git push",
        "source setup; git push",
        "GIT_DIR=/shared git push",
        "FOO=bar git push",
        "env git push",
        "timeout 5 git push",
        "xargs git push",
        "git push origin HEAD:HEAD",
        "git push origin HEAD:refs/heads/HEAD",
        "git push origin HEAD:feat//x",
        "git push origin +feat/x",
        "git push `remote` feat/x",
        "git push origin feat/*",
        "git push origin feat/?",
        "git push origin feat/[x]",
        "git push origin ~user",
        "git push origin feat/!x",
        "git push origin HEAD^",
        "git push origin 'feat/x",
    ],
)
def test_unproven_pushes_deny(push_gate: ModuleType, command: str) -> None:
    """Reviewer bypasses and unreadable shell/refspec forms must fail closed."""
    reason = push_gate.push_denial(command, "/repo")
    assert isinstance(reason, str) and reason


def test_attached_directory_looks_up_actual_push_repository(
    permission_gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A feature cwd cannot authorize a push from a shared -C repository."""
    lookup = Mock(return_value=("master", None))
    monkeypatch.setattr(permission_gate, "_branch_state", lookup)
    assert permission_gate.push_denial("git -C/shared push", "/feature")
    lookup.assert_called_once_with("/shared")


@pytest.mark.parametrize(
    "command", ["git push", "git push origin feat/x", "git push origin HEAD:trunk"]
)
@pytest.mark.parametrize(
    "failure",
    [
        OSError("git unavailable"),
        subprocess.TimeoutExpired("git", 5),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid ref"),
        RuntimeError("unexpected lookup failure"),
    ],
)
def test_lookup_exceptions_deny_every_push(
    permission_gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    failure: Exception,
) -> None:
    """A failing collaborator must emit a refusal rather than crash the hook."""
    monkeypatch.setattr(permission_gate.subprocess, "run", Mock(side_effect=failure))
    assert permission_gate.push_denial(command, "/repo")


@pytest.mark.parametrize(
    "command", ["git push", "git push origin feat/x", "git push origin HEAD:trunk"]
)
def test_nonrepository_lookup_denies_every_push(
    permission_gate: ModuleType, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """A nonzero Git result proves no destination safe, even an explicit feature ref."""
    run = Mock(return_value=subprocess.CompletedProcess(["git"], 128, b"", b"not a repository"))
    monkeypatch.setattr(permission_gate.subprocess, "run", run)
    assert permission_gate.push_denial(command, "/repo")
    assert run.call_count == 1
    assert run.call_args.kwargs["timeout"] == 5


def test_invalid_utf8_branch_discovery_denies_push(
    permission_gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Undecodable repository state cannot prove an explicit destination safe."""
    run = Mock(
        return_value=subprocess.CompletedProcess(
            ["git"],
            0,
            b"*\trefs/heads/feat/x\t\n \trefs/remotes/origin/HEAD\trefs/remotes/origin/\xff\n",
            b"",
        )
    )
    monkeypatch.setattr(permission_gate.subprocess, "run", run)
    assert permission_gate.push_denial("git push origin HEAD:trunk", "/repo")


@pytest.mark.parametrize(
    "output",
    [
        b"*\trefs/heads/feat/x\t\n \trefs/heads/\xff\t\n",
        b"*\trefs/heads/feat/x\t\nmalformed\n",
        b"*\trefs/heads/feat/x\t\n \trefs/heads/other\t\textra\n",
        b"*\trefs/heads/feat/x\t\n \trefs/tags/v1.0.0\t\n",
        b"*\trefs/heads/feat/x\t\n*\trefs/heads/master\t\n",
        b"*\trefs/heads/feat/x\t\n \trefs/remotes/origin/HEAD\t\n",
        b"*\trefs/heads/feat/x\t\n \trefs/remotes/origin/HEAD\trefs/heads/trunk\n",
        b"*\trefs/heads/feat/x\t\n \trefs/remotes/origin/HEAD\trefs/remotes/origin/\n",
        b"*\trefs/heads/feat/x\t\n \trefs/remotes/origin/HEAD\trefs/remotes/origin/tr unk\n",
        b"*\trefs/heads/feat/x\t\n \trefs/heads/\t\n",
        b"*\trefs/heads/feat/x\t\n \trefs/heads/ma ster\t\n",
    ],
    ids=[
        "undecodable",
        "too-few-fields",
        "too-many-fields",
        "unexpected-ref",
        "multiple-current-branches",
        "missing-origin-target",
        "non-origin-target",
        "empty-origin-branch",
        "whitespace-origin-branch",
        "empty-local-branch",
        "whitespace-local-branch",
    ],
)
@pytest.mark.parametrize("command", ["git push", "git push origin feat/x"])
def test_malformed_branch_discovery_denies_every_push(
    permission_gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    output: bytes,
    command: str,
) -> None:
    """Invalid discovery cannot authorize either an implicit or explicit destination."""
    run = Mock(return_value=subprocess.CompletedProcess(["git"], 0, output, b""))
    monkeypatch.setattr(permission_gate.subprocess, "run", run)
    reason = permission_gate.push_denial(command, "/repo")
    assert isinstance(reason, str) and reason
    run.assert_called_once()


@pytest.mark.parametrize("command", ["git stash push", "git commit -m push", "git status"])
def test_nonpush_commands_do_not_discover_branches(
    permission_gate: ModuleType, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """Other subcommands mentioning push stay outside this guard."""
    lookup = Mock(side_effect=AssertionError("unexpected lookup"))
    monkeypatch.setattr(permission_gate, "_branch_state", lookup)
    assert permission_gate.push_denial(command, "/repo") is None
    lookup.assert_not_called()


@pytest.mark.parametrize("branch", ["master", "MAIN", "development", "qa", "TRUNK", None])
@pytest.mark.parametrize("refspec", ["", " origin HEAD", " origin @"])
def test_implicit_shared_or_detached_destinations_deny(
    permission_gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    branch: str | None,
    refspec: str,
) -> None:
    """All implicit spellings reject fixed and discovered shared branches or detached HEAD."""
    monkeypatch.setattr(permission_gate, "_branch_state", Mock(return_value=(branch, "trunk")))
    assert permission_gate.push_denial(f"git push{refspec}", "/repo")


@pytest.mark.parametrize(
    "origin", [b"", b" \trefs/remotes/origin/HEAD\trefs/remotes/origin/trunk\n"]
)
def test_valid_branch_discovery_allows_feature_push_without_requiring_origin_head(
    permission_gate: ModuleType, monkeypatch: pytest.MonkeyPatch, origin: bytes
) -> None:
    """One bounded lookup proves a feature branch safe with or without origin HEAD."""
    output = b"*\trefs/heads/feat/x\t\n" + origin
    run = Mock(return_value=subprocess.CompletedProcess(["git"], 0, output, b""))
    monkeypatch.setattr(permission_gate.subprocess, "run", run)
    assert permission_gate.push_denial("git push", "/repo") is None
    run.assert_called_once_with(
        [
            "git",
            "for-each-ref",
            "--format=%(HEAD)%09%(refname)%09%(symref)",
            "refs/heads",
            "refs/remotes/origin/HEAD",
        ],
        cwd="/repo",
        capture_output=True,
        check=False,
        timeout=5,
    )
