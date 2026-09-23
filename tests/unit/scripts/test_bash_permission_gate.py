"""Pure classification regressions for live, literal and opaque substitutions."""

from types import ModuleType

import pytest

from tests.helpers.permission_cases import HOOK_BYPASS_CASES


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
