#!/usr/bin/env python3
"""PreToolUse Bash permission gate — allow only read-only forms the gate fully models.

Reads the PreToolUse hook JSON on stdin and classifies the Bash command against the permission
ledger (``.claude/permissions-ledger.json``). Three answers (ADR-0015):

  * ``allow`` — every part of the command is a literal, readable command line that matches one
    *form* in ``_FORMS`` (tool + subcommand + exact options + positional roles) selected by a
    ledger key, and nothing in the command mutates shell state: no assignment word, no env
    prefix, no write redirect, no builtin outside ``_NO_MUTATION_BUILTINS``. Inert forms are
    read-only; project forms (``pytest``, ``git commit``, the ``.claude/scripts`` CLIs, …) run
    the repository's own code and are allowed only through an explicit full-form key — a stated
    act of trust in the repository.
  * ``ask`` — a guarded thing is involved: an unreadable head (non-literal command name, opaque
    substitution, unreadable grammar), a regex ``ask_patterns``/``denials`` hit, an ``ask_words``
    hit at the command words, a guarded tool (git, gh, aws, docker, terraform, an interpreter, a
    shell, a wrapper, a spawner, a repo-code runner) outside an allowed form, an execution option
    on an inert tool, or a non-allowed part that mentions an execution-bearing path.
  * none — nothing above: the harness's own prompt (or auto mode's classifier) decides, and the
    command is logged to ``.claude/permission-unknowns.log``.

The lexer is the gate's own: one pass with a quote state and an escape rule per state, words
carrying a literal flag, segments split on unquoted operators, substitutions extracted and
refused when opaque (ADR-0011), heredoc bodies kept as live text, nested command lines of
wrappers and shell-string tools re-lexed. No filesystem lookups: the gate judges the text.
"""

import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

Verdict = Literal["allow", "ask", "none"]


def _config_dir() -> Path:
    """The .claude config dir holding the ledger — repo-local if present, else the global ~/.claude.

    Prefers ``$CLAUDE_PROJECT_DIR/.claude`` (the ledger travels with this portable setup); falls
    back to ``~/.claude`` for when the setup is installed as the global config.
    """
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if project:
        local = Path(project) / ".claude"
        if (local / "permissions-ledger.json").is_file():
            return local
    return Path.home() / ".claude"


LEDGER_PATH: Path = _config_dir() / "permissions-ledger.json"
UNKNOWN_LOG: Path = _config_dir() / "permission-unknowns.log"

# ---------------------------------------------------------------------------------------------
# The word model
# ---------------------------------------------------------------------------------------------

_BLANKS: frozenset[str] = frozenset(" \t\n")  # never \r: the shell keeps it inside a word
_DQ_ESCAPABLE: frozenset[str] = frozenset('$`"\\\n')
_NONLITERAL_CHARS: frozenset[str] = frozenset("$`*?[")
_HEREDOC_TAG: re.Pattern[str] = re.compile(r"\s*(?P<q>['\"]?)(?P<tag>[A-Za-z0-9_]+)(?P=q)")
_BRACKET_WORDS: frozenset[str] = frozenset({"[", "[[", "]", "]]"})
_ASSIGNMENT: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=")
_PURE_ARITHMETIC: re.Pattern[str] = re.compile(r"^[0-9+\-*/%<>&|^~!=?:()\s]*$")
_FD_DUP: re.Pattern[str] = re.compile(r"^[0-9]*>&[0-9]+-?$|^[0-9]*<&[0-9]+-?$")
_REDIRECT_OP: re.Pattern[str] = re.compile(r"^[0-9]*(>>|>\||>&|&>>|&>|<>|<&|<<<|>|<)$")
# Stand-in for a substitution body the gate refuses to read (ADR-0011): a command carrying one
# always prompts instead of being classified on a guessed body.
_OPAQUE_SUBSTITUTION: str = "__opaque_substitution__"
_OPAQUE_BODY_CHARS: frozenset[str] = frozenset("'\"\\#`")
_MAX_NESTING: int = 6  # re-lexing depth for nested command lines before the gate gives up


@dataclass(frozen=True)
class Word:
    """One shell word after dequoting.

    Attributes:
        text: The word's value with quotes and escapes resolved as the shell would, when literal.
        literal: True when the runtime value is fixed by the text (no expansion of any kind).
        quoted: True when any part of the word was quoted (``"git"``, ``me''rge``).
    """

    text: str
    literal: bool
    quoted: bool = False


@dataclass(frozen=True)
class Redirect:
    """One redirection: the operator (``>``, ``2>``, ``<``, ``2>&1``, ``<<<`` …) and its target."""

    op: str
    target: Word

    @property
    def inert(self) -> bool:
        """True for fd duplication, ``/dev/null`` sinks and a literal input file or herestring."""
        if _FD_DUP.match(self.op + self.target.text):
            return True
        if self.target.text == "/dev/null" and self.op.lstrip("0123456789") in {
            ">",
            ">>",
            "&>",
            ">|",
        }:
            return True
        if self.op.lstrip("0123456789") in {"<", "<<<"}:
            return self.target.literal and _in_project_path(self.target.text)
        return False


@dataclass(frozen=True)
class Part:
    """One modelled command: a segment, a substitution body, or a nested command line.

    Attributes:
        head: The dequoted first word (``""`` for a part with no command, e.g. an assignment).
        words: Every word, head first (assignments of the env prefix excluded).
        env: The leading ``NAME=value`` words.
        redirects: Every redirection with its target word.
        heredocs: Heredoc bodies, with their delimiter quoting (``True`` when quoted/literal).
        readable: False when the gate could not read the part (opaque body, unreadable grammar).
        nested: The command lines a wrapper, spawner or shell-string tool runs, modelled.
        source: The text this part came from.
        structural: Leading keywords (``if``, ``!``, ``{`` …) found before the head.
        binds: True when the part binds a name (``for x in``, ``select``), defeating allow.
    """

    head: str
    words: tuple[Word, ...]
    env: tuple[Word, ...] = ()
    redirects: tuple[Redirect, ...] = ()
    heredocs: tuple[tuple[str, bool], ...] = ()
    readable: bool = True
    nested: tuple["Part", ...] = ()
    source: str = ""
    structural: tuple[str, ...] = ()
    binds: bool = False

    @property
    def argv(self) -> tuple[str, ...]:
        """The dequoted words, head first."""
        return tuple(w.text for w in self.words)

    @property
    def literal(self) -> bool:
        """True when every word (env prefix and redirect targets included) is literal."""
        return (
            all(w.literal for w in self.words)
            and all(w.literal for w in self.env)
            and all(r.target.literal for r in self.redirects)
        )

    @property
    def write_redirect(self) -> bool:
        """True when any redirect is not inert (so the part may write a file)."""
        return any(not r.inert for r in self.redirects)


def _project_root() -> str:
    """The project root the gate judges relative paths against (text only, no stat)."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def _in_project_path(text: str) -> bool:
    """True for a literal input path inside the project: relative without ``..``, or absolute under
    the project root; never ``:``, a leading ``~`` or ``=``, or an empty word.
    """
    if not text or ":" in text or text[0] in "~=":
        return False
    if text.startswith("/"):
        root = _project_root().rstrip("/")
        if text != root and not text.startswith(root + "/"):
            return False
    return ".." not in text.split("/")


class _Unreadable(Exception):
    """Raised by the lexer on grammar the gate does not model (RC4, class I)."""


class _WordBuilder:
    """Accumulates one word's characters with its literal and quoted flags."""

    def __init__(self) -> None:
        self.chars: list[str] = []
        self.literal = True
        self.quoted = False
        self.started = False

    def add(self, char: str, *, literal: bool = True) -> None:
        self.started = True
        self.chars.append(char)
        if not literal:
            self.literal = False

    def finish(self) -> Word | None:
        if not self.started:
            return None
        text = "".join(self.chars)
        # ``[``/``]`` as whole words are the test builtin's brackets, not a glob.
        literal = self.literal or (not self.quoted and text in _BRACKET_WORDS)
        return Word(text, literal, self.quoted)


@dataclass
class _Lexed:
    """A raw lexed segment: tokens in order, with operators as ``_Op`` markers."""

    words: list[Word] = field(default_factory=list)
    redirects: list[Redirect] = field(default_factory=list)
    heredocs: list[tuple[str, bool]] = field(default_factory=list)
    bodies: list[str] = field(default_factory=list)  # substitution bodies (or the opaque stand-in)
    readable: bool = True


def _substitution_end(text: str, body: int, closer: str) -> int:
    """Index just past the closing delimiter of a substitution whose body starts at ``body``.

    Naive on purpose — a backtick body ends at the next backtick, a paren body at the ``)`` that
    brings the raw count to zero — because delimiting one exactly takes the shell's own lexer.
    ``_is_opaque`` refuses every body those rules could apply to (ADR-0011). Returns -1 when the
    delimiter is never closed.
    """
    if closer == "`":
        end = text.find("`", body)
        return -1 if end < 0 else end + 1
    depth, i = 1, body
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if not depth:
                return i + 1
        i += 1
    return -1


def _is_opaque(body: str) -> bool:
    """True when the shell might end this substitution body elsewhere than the naive scan did."""
    return any(c in _OPAQUE_BODY_CHARS for c in body) or "$(" in body


def _lex_segments(text: str, depth: int = 0) -> list[_Lexed]:
    """Split a command line into segments of dequoted words, redirects, heredocs and bodies.

    One left-to-right pass with a quote state and an escape rule per state. Segment boundaries
    are the unquoted ``;``, ``&&``, ``||``, ``|``, ``|&``, ``&``, newline, and a subshell's
    ``(``/``)`` at command position. Raises ``_Unreadable`` on grammar the gate does not model:
    a paren outside the four modelled places, ``${…}`` holding ``(``/``!``/a substitution, a
    non-numeric arithmetic body, a subscripted assignment, ``$'…'`` is read as non-literal.
    """
    if depth > _MAX_NESTING:
        raise _Unreadable("nested command lines too deep")
    segments: list[_Lexed] = [_Lexed()]
    current = _WordBuilder()
    quote: str | None = None
    subshell_depth = 0
    pending_heredocs: list[tuple[str, bool]] = []  # (tag, quoted) awaiting their bodies
    pending_redirect: str | None = None
    i, n = 0, len(text)

    def flush() -> None:
        nonlocal current, pending_redirect
        word = current.finish()
        current = _WordBuilder()
        if word is None:
            return
        if pending_redirect is not None:
            segments[-1].redirects.append(Redirect(pending_redirect, word))
            pending_redirect = None
        else:
            segments[-1].words.append(word)

    def new_segment() -> None:
        flush()
        if pending_redirect is not None:
            raise _Unreadable("redirect without a target")
        segments.append(_Lexed())

    def at_command_position() -> bool:
        return not current.started and not segments[-1].words and pending_redirect is None

    while i < n:
        char = text[i]
        if quote == "'":
            if char == "'":
                quote = None
            else:
                current.add(char)
            i += 1
            continue
        if quote == '"':
            if char == '"':
                quote = None
                i += 1
            elif char == "\\" and i + 1 < n and text[i + 1] in _DQ_ESCAPABLE:
                if text[i + 1] != "\n":
                    current.add(text[i + 1])
                i += 2
            elif char == "$" or char == "`":
                i = _consume_expansion(text, i, current, segments[-1], depth)
            else:
                current.add(char)
                i += 1
            continue
        # unquoted
        if char == "\\":
            if i + 1 >= n:
                raise _Unreadable("trailing backslash")
            if text[i + 1] != "\n":
                current.add(text[i + 1])
            i += 2
            continue
        if char == "'":
            quote = "'"
            current.quoted = True
            current.started = True
            i += 1
            continue
        if char == '"':
            quote = '"'
            current.quoted = True
            current.started = True
            i += 1
            continue
        if char == "#" and not current.started:
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        if char == "\n":
            new_segment()
            i += 1
            if pending_heredocs:
                i = _read_heredoc_bodies(text, i, pending_heredocs, segments[-2])
                pending_heredocs = []
            continue
        if char in _BLANKS:
            flush()
            i += 1
            continue
        if text.startswith("&&", i) or text.startswith("||", i) or text.startswith("|&", i):
            new_segment()
            i += 2
            continue
        if text.startswith(";;", i):
            new_segment()
            i += 2
            continue
        if char in ";|":
            new_segment()
            i += 1
            continue
        if char == "&" and not text.startswith("&>", i):
            new_segment()
            i += 1
            continue
        if char == "(":
            if text.startswith("((", i):
                raise _Unreadable("arithmetic command `((` outside a modelled place")
            if at_command_position():
                subshell_depth += 1
                i += 1
                continue
            raise _Unreadable("unquoted `(` outside a modelled place")
        if char == ")":
            if subshell_depth and not pending_redirect:
                subshell_depth -= 1
                new_segment()
                i += 1
                continue
            raise _Unreadable("unquoted `)` outside a modelled place")
        if char in "<>&":
            if current.started:
                if _fd_prefix(current):
                    current = _WordBuilder()  # the digits belong to the operator (``2>&1``)
                else:
                    flush()
            m = _match_redirect(text, i)
            if m is None:
                raise _Unreadable("unreadable redirect")
            op, i = m
            core = op.lstrip("0123456789")
            if core in {"<<", "<<-"}:
                tag = _HEREDOC_TAG.match(text, i)
                if tag is None:
                    raise _Unreadable("heredoc without a delimiter")
                pending_heredocs.append((tag.group("tag"), bool(tag.group("q"))))
                i = tag.end()
                continue
            if text.startswith("(", i) and core in {"<", ">"}:
                body_start = i + 1
                end = _substitution_end(text, body_start, ")")
                if end < 0:
                    raise _Unreadable("unclosed process substitution")
                segments[-1].bodies.append(_body_or_opaque(text[body_start : end - 1]))
                current.add(op + "(…)", literal=False)
                i = end
                continue
            pending_redirect = op
            continue
        if char == "$" or char == "`":
            i = _consume_expansion(text, i, current, segments[-1], depth)
            continue
        if char == "{" and _brace_group(text, i):
            current.add(char, literal=False)  # brace expansion: the value is not fixed
            i += 1
            continue
        if char in "~=" and not current.started:
            current.add(char, literal=False)
            i += 1
            continue
        current.add(char, literal=char not in "*?[")
        i += 1
    if quote is not None:
        raise _Unreadable("unbalanced quote")
    if pending_heredocs:
        raise _Unreadable("heredoc body missing")
    if subshell_depth:
        raise _Unreadable("unclosed subshell")
    flush()
    if pending_redirect is not None:
        raise _Unreadable("redirect without a target")
    return [s for s in segments if s.words or s.redirects or s.bodies or s.heredocs]


def _fd_prefix(current: _WordBuilder) -> bool:
    """True when the word being built is a redirect's fd prefix (the ``2`` in ``2>&1``)."""
    return not current.quoted and "".join(current.chars).isdigit()


def _match_redirect(text: str, i: int) -> tuple[str, int] | None:
    """Return the redirect operator at ``i`` (with its fd prefix) and the index after it."""
    j = i
    while j > 0 and text[j - 1].isdigit():
        j -= 1
    m = re.match(r"[0-9]*(<<-|<<<|<<|>>|>\||>&|&>>|&>|<>|<&|>|<)", text[j:])
    if m is None:
        return None
    return m.group(0), j + m.end()


def _brace_group(text: str, i: int) -> bool:
    """True when ``{`` at ``i`` opens a brace expansion (``{a,b}`` or ``{1..3}``)."""
    end = text.find("}", i)
    if end < 0:
        return False
    body = text[i + 1 : end]
    return ("," in body or ".." in body) and " " not in body


def _body_or_opaque(body: str) -> str:
    """A substitution body to classify on its own, or the opaque stand-in."""
    return _OPAQUE_SUBSTITUTION if _is_opaque(body) else body


def _consume_expansion(text: str, i: int, current: _WordBuilder, seg: _Lexed, depth: int) -> int:
    """Read one ``$…`` or backtick expansion starting at ``i``; return the index after it.

    The word becomes non-literal. ``$(…)`` and backtick bodies are extracted into ``seg.bodies``
    (or the opaque stand-in); ``$((…))`` must be a pure numeric expression; ``${…}`` must hold no
    ``(``, ``!``, ``$(`` or backtick; a ``$'…'`` word is read to its end.
    """
    current.started = True
    if text[i] == "`":
        end = _substitution_end(text, i + 1, "`")
        if end < 0:
            raise _Unreadable("unclosed backtick")
        seg.bodies.append(_body_or_opaque(text[i + 1 : end - 1]))
        current.add("`…`", literal=False)
        return end
    if text.startswith("$((", i):
        end = text.find("))", i + 3)
        if end < 0:
            raise _Unreadable("unclosed arithmetic")
        body = text[i + 3 : end]
        if not _PURE_ARITHMETIC.match(body):
            raise _Unreadable("arithmetic with a non-numeric operand")
        current.add("$((…))", literal=False)
        return end + 2
    if text.startswith("$(", i):
        end = _substitution_end(text, i + 2, ")")
        if end < 0:
            raise _Unreadable("unclosed substitution")
        seg.bodies.append(_body_or_opaque(text[i + 2 : end - 1]))
        current.add("$(…)", literal=False)
        return end
    if text.startswith("${", i):
        end = text.find("}", i + 2)
        if end < 0:
            raise _Unreadable("unclosed parameter expansion")
        body = text[i + 2 : end]
        if any(mark in body for mark in ("(", "!", "$(", "`")):
            raise _Unreadable("parameter expansion the gate cannot read")
        current.add("${" + body + "}", literal=False)
        return end + 1
    if text.startswith("$'", i):
        j = i + 2
        while j < len(text) and text[j] != "'":
            j += 2 if text[j] == "\\" else 1
        if j >= len(text):
            raise _Unreadable("unclosed $'…'")
        current.add(text[i : j + 1], literal=False)
        return j + 1
    current.add("$", literal=False)
    return i + 1


def _read_heredoc_bodies(text: str, i: int, pending: list[tuple[str, bool]], seg: _Lexed) -> int:
    """Consume the heredoc bodies that start at line index ``i``; return the index after them.

    A quoted delimiter's body is literal data; an unquoted one is live text whose substitutions
    are extracted as bodies of the segment that owns the heredoc.
    """
    for tag, quoted in pending:
        lines: list[str] = []
        while True:
            end = text.find("\n", i)
            line = text[i:] if end < 0 else text[i:end]
            i = len(text) if end < 0 else end + 1
            if line.strip("\t ") == tag:
                break
            lines.append(line)
            if end < 0:
                raise _Unreadable("heredoc body missing its terminator")
        body = "\n".join(lines)
        seg.heredocs.append((body, quoted))
        if not quoted:
            _extract_live_bodies(body, seg)
    return i


def _extract_live_bodies(body: str, seg: _Lexed) -> None:
    """Pull ``$(…)``/backtick substitutions out of a live heredoc body into the segment."""
    i = 0
    while i < len(body):
        if body.startswith("$(", i) or body[i] == "`":
            backtick = body[i] == "`"
            start = i + (1 if backtick else 2)
            end = _substitution_end(body, start, "`" if backtick else ")")
            if end < 0:
                seg.bodies.append(_OPAQUE_SUBSTITUTION)
                return
            seg.bodies.append(_body_or_opaque(body[start : end - 1]))
            i = end
        elif body[i] == "\\":
            i += 2
        else:
            i += 1


# ---------------------------------------------------------------------------------------------
# Heads: the four kinds and the builtin rules
# ---------------------------------------------------------------------------------------------

# Shell keywords that precede a real command and are skipped to find it.
_STRUCTURAL: frozenset[str] = frozenset(
    {"if", "then", "else", "elif", "fi", "while", "until", "do", "done", "esac", "{", "}", "!"}
)
# Keywords that bind a name or open grammar the gate does not model: the command cannot allow.
_BINDING_KEYWORDS: frozenset[str] = frozenset({"for", "select", "in"})
# Builtins that evaluate or define code: always ask.
_CODE_BUILTINS: frozenset[str] = frozenset(
    {"eval", "source", ".", "trap", "alias", "exec", "builtin", "hash", "enable"}
    | {"coproc", "function", "case"}
)
# Builtins that evaluate an argument as a variable name (array subscript) or arithmetic — carried
# over from the previous gate plus the name-binding builtins. With a payload marker, a subscript
# or ``-v`` they ask; otherwise the command cannot allow (they mutate shell state).
_EVALUATING_HEADS: frozenset[str] = frozenset(
    {"unset", "local", "export", "declare", "typeset", "readonly", "read", "eval", "source", "."}
    | {"trap", "let", "printf", "test", "[", "[[", "mapfile", "readarray"}
)
_PAYLOAD_MARKERS: tuple[str, ...] = ("$(", "`", "<(")  # what the shell runs when it evaluates text
_ARITHMETIC_COMPARISONS: frozenset[str] = frozenset({"-eq", "-ne", "-lt", "-le", "-gt", "-ge"})
# The only builtins an auto-allowed command may contain (review pass 5): none of them can
# re-point PATH or otherwise mutate shell state. ``set`` only with these flags.
_NO_MUTATION_BUILTINS: frozenset[str] = frozenset({"true", "false", ":", "exit", "echo", "set"})
_SET_FLAG_LETTERS: frozenset[str] = frozenset("euxo")  # ``-o`` only ever pairs with pipefail
# Every other shell builtin the gate knows: never allowed (``set -A path``, ``printf '%n' PATH``,
# ``cd``, ``typeset`` … all mutate state); none unless another rule asks.
_OTHER_BUILTINS: frozenset[str] = frozenset(
    {"cd", "pwd", "pushd", "popd", "dirs", "type", "which", "whence", "where", "command"}
    | {"shift", "break", "continue", "return", "wait", "jobs", "fg", "bg", "disown", "kill"}
    | {"shopt", "setopt", "unsetopt", "ulimit", "umask", "getopts", "times", "fc", "history"}
    | {"bind", "bindkey", "emulate", "autoload", "zmodload", "compdef", "rehash", "unalias"}
    | {"unfunction", "unhash", "print", "vared", "zle", "sched", "limit", "unlimit", "float"}
    | {"integer", "functions", "complete", "compgen", "caller", "help", "logout", "suspend"}
)
_VERSION_WORDS: frozenset[str] = frozenset({"--version", "--help", "-h", "-V"})

# Guarded heads: allowed only through a form; any other use asks.
_GUARDED_TOOLS: frozenset[str] = frozenset({"git", "gh", "aws", "docker", "terraform"})
_GUARDED_DB: frozenset[str] = frozenset({"dropdb", "psql"})
_INTERPRETERS: frozenset[str] = frozenset(
    {"node", "nodejs", "deno", "bun", "perl", "ruby", "php", "lua", "Rscript", "tclsh", "expect"}
    | {"osascript", "pwsh", "powershell", "awk", "gawk", "mawk", "nawk", "sed", "gsed"}
)
_PYTHON: re.Pattern[str] = re.compile(r"^python[0-9.]*$")
_SHELLS: frozenset[str] = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
_WRAPPERS: frozenset[str] = frozenset(
    {"env", "timeout", "nohup", "nice", "time", "stdbuf", "caffeinate"}
)
_SPAWNERS: frozenset[str] = frozenset(
    {"xargs", "uv", "uvx", "poetry", "pipx", "npm", "npx", "pnpm", "yarn", "cargo", "go"}
    | {"bundle", "entr", "parallel"}
)
_SHELL_STRING_TOOLS: frozenset[str] = frozenset(
    {"su", "ssh", "watch", "script", "hyperfine", "at", "crontab"}
)
_RUNNERS: frozenset[str] = frozenset({"make", "tox", "pre-commit", "pytest", "ruff", "mypy"})
_INSTALLERS: frozenset[str] = frozenset(
    {"pip", "pip3", "brew", "apt", "apt-get", "gem", "conda", "mamba", "pipenv"}
)
_OUTWARD: frozenset[str] = frozenset({"scp", "rsync", "sudo", "doas"})
# Per wrapper: options taking a separate value, and bare flags — used only to find the nested
# command so the reason can name the inner guard; a wrapper part never allows.
_WRAPPER_VALUE_OPTS: dict[str, frozenset[str]] = {
    "xargs": frozenset(
        {"-I", "-J", "-R", "-S", "-n", "-P", "-L", "-s", "-E", "-d", "-a"}
        | {"--arg-file", "--delimiter", "--eof", "--max-args", "--max-procs", "--max-lines"}
        | {"--max-chars", "--process-slot-var"}
    ),
    "timeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
    "env": frozenset({"-u", "-C", "-P", "--unset", "--chdir"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "time": frozenset({"-f", "-o", "--format", "--output"}),
    "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "watch": frozenset({"-n", "--interval", "-d", "--differences"}),
    "script": frozenset({"-c", "--command"}),
    "su": frozenset({"-c", "--command"}),
}
_WRAPPER_FLAGS: dict[str, frozenset[str]] = {
    "xargs": frozenset(
        {"-0", "-r", "-t", "-p", "-x", "-o", "-i", "-l", "-e"}
        | {"--null", "--no-run-if-empty", "--verbose", "--interactive", "--exit", "--open-tty"}
    ),
    "timeout": frozenset({"-v", "-f", "-p", "--verbose", "--preserve-status", "--foreground"}),
    "env": frozenset({"-", "-i", "-0", "-v", "--ignore-environment", "--null", "--debug"}),
    "time": frozenset({"-p", "-v", "-a", "-l", "--portability", "--verbose", "--append"}),
    "nohup": frozenset(),
    "watch": frozenset({"-t", "-b", "-e", "-g", "-c", "-x", "--no-title", "--beep"}),
    "script": frozenset({"-q", "-a", "-k", "-f", "-F", "-r"}),
}
# Shell-string tools: the option (or position) whose value is re-lexed as a command line.
_SHELL_STRINGS: dict[str, str] = {
    "sh": "-c",
    "bash": "-c",
    "zsh": "-c",
    "dash": "-c",
    "ksh": "-c",
    "fish": "-c",
    "su": "-c",
    "script": "-c",
    "osascript": "-e",
    "expect": "-c",
}
# Options on inert tools that run a program or delete: the part becomes a spawner part and asks.
_EXECUTION_OPTIONS: dict[str, frozenset[str]] = {
    "find": frozenset({"-exec", "-execdir", "-ok", "-okdir", "-delete"}),
    "rg": frozenset({"--pre", "--pre-glob"}),
    "tar": frozenset(
        {"--to-command", "-I", "--use-compress-program", "--checkpoint-action", "-c", "-x"}
        | {"-r", "-u", "-A", "--create", "--extract", "--get", "--append", "--update"}
        | {"--catenate", "--concatenate", "--delete"}
    ),
}
_TAR_WRITE_CLUSTER: re.Pattern[str] = re.compile(r"^-[a-zA-Z]*[cxruA]")

# ---------------------------------------------------------------------------------------------
# The lexical execution-bearing test (decision 14a)
# ---------------------------------------------------------------------------------------------

_EXEC_BEARING_COMPONENTS: frozenset[str] = frozenset({".git", ".claude"})
_EXEC_BEARING_BASENAMES: frozenset[str] = frozenset(
    {"Makefile", "GNUmakefile", "makefile", "package.json", "package-lock.json"}
    | {"pyproject.toml", "setup.py", "setup.cfg", "conftest.py", "tox.ini", "pytest.ini"}
    | {".pre-commit-config.yaml", ".npmrc", ".envrc", "Dockerfile", "docker-compose.yml"}
    | {"compose.yaml"}
)
_EXEC_BEARING_SUFFIXES: tuple[str, ...] = (".sh", ".bash", ".zsh")
_EXEC_BEARING_PREFIXES: tuple[str, ...] = (
    "~",
    "$HOME",
    "/etc/",
    "/usr/",
    "/bin/",
    "/sbin/",
    "/Library/",
    "/private/",
)


def _execution_bearing(text: str) -> bool:
    """True when a word names an execution-bearing path by its text alone (no stat, no realpath)."""
    parts = text.split("/")
    return (
        any(p in _EXEC_BEARING_COMPONENTS for p in parts)
        or parts[-1] in _EXEC_BEARING_BASENAMES
        or text.endswith(_EXEC_BEARING_SUFFIXES)
        or text.startswith(_EXEC_BEARING_PREFIXES)
    )


# ---------------------------------------------------------------------------------------------
# The form table (A, G, 14c-e)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Form:
    """One allowable command shape: tool + subcommand words, exact options, positional roles.

    Attributes:
        words: The leading command words (``("git", "status")``).
        options: Allowed option -> value shapes (``()`` for a flag).
        positional: Positional roles in order; a trailing ``*`` repeats, ``?`` is optional.
        kind: ``inert`` (read-only; a whole-tool key selects it) or ``project`` (runs repo code;
            only the exact key selects it).
        key: The ledger key that selects a project form.
        required: Groups of options; every group needs one of its members present.
    """

    words: tuple[str, ...]
    options: Mapping[str, tuple[str, ...]]
    positional: tuple[str, ...]
    kind: Literal["inert", "project"]
    key: str
    required: tuple[tuple[str, ...], ...] = ()


def _form(
    words: str,
    options: str = "",
    positional: str = "",
    *,
    kind: Literal["inert", "project"] = "inert",
    key: str | None = None,
    required: tuple[tuple[str, ...], ...] = (),
) -> Form:
    """Build a Form from compact specs: options ``"-n -A:int --color:enum{a,b} --arg:lit:lit"``,
    positionals ``"lit in*"``.
    """
    opts: dict[str, tuple[str, ...]] = {}
    for token in options.split():
        name, _, shapes = token.partition(":")
        opts[name] = tuple(shapes.split(":")) if shapes else ()
    word_tuple = tuple(words.split())
    return Form(word_tuple, opts, tuple(positional.split()), kind, key or words, required)


_LOG_OPTS = (
    "--oneline --graph --decorate --stat --name-only --name-status -p -U:int -n:int -N"
    " --max-count:int --since:lit --until:lit --author:lit --grep:lit -S:lit -G:lit --all"
    " --first-parent --no-merges --merges --pretty:lit --format:lit --date:lit --abbrev-commit"
    " --reverse --cached --staged -w -b --color:lit --no-color --word-diff -M -C --find-renames"
    " --diff-filter:lit --numstat --shortstat --summary --quiet --exit-code --no-prefix"
    " --relative -z --"
)
_AWS_GLOBALS = (
    "--profile:lit --region:lit --output:enum{json,text,table,yaml} --query:lit --no-cli-pager"
    " --no-paginate --cli-read-timeout:int --cli-connect-timeout:int --no-verify-ssl --debug"
    " --color:enum{on,off,auto}"
)
_AWS_PAGING = " --max-items:int --page-size:int --starting-token:lit"
_GH_GLOBALS = " -R:lit --repo:lit --hostname:lit"
_GH_ISSUE_LIST = (
    "-s:lit --state:lit -L:int --limit:int --search:lit -A:lit --author:lit -l:lit --label:lit"
    " --json:lit -q:lit --jq:lit -t:lit --template:lit"
)
_GH_CREATE = (
    "-t:lit --title:lit -b:lit --body:lit -F:path --body-file:path -l:lit --label:lit"
    " -a:lit --assignee:lit"
)
_GH_EDIT = "-t:lit -b:lit -F:path --add-label:lit --remove-label:lit"
_GH_COMMENT = "-b:lit -F:path"
_PYTEST_OPTS = (
    "-q -v -vv -x -s -k:lit -m:lit --tb:lit --maxfail:int --lf --ff --co --collect-only"
    " --durations:int -r:lit --no-header --disable-warnings -W:lit --ignore:lit --deselect:lit"
    " --log-level:lit -p:no:"
)
_MYPY_OPTS = (
    "--strict --ignore-missing-imports --no-error-summary --show-error-codes --pretty"
    " --follow-imports:lit --python-version:lit --warn-unused-ignores --no-incremental"
    " --explicit-package-bases --namespace-packages"
)
_RUFF_OPTS = (
    "--select:lit --ignore:lit --extend-select:lit --line-length:int --output-format:lit"
    " --statistics --no-cache -q --quiet --exit-zero --show-fixes --diff --isolated"
)


def _aws(service: str, operation: str, params: str = "") -> Form:
    return _form(f"aws {service} {operation}", _AWS_GLOBALS + " " + params)


def _gh(words: str, options: str = "", positional: str = "") -> Form:
    return _form(words, options + _GH_GLOBALS, positional, kind="project")


def _git(
    words: str,
    options: str = "",
    positional: str = "",
    *,
    key: str | None = None,
    required: tuple[tuple[str, ...], ...] = (),
) -> Form:
    return _form(words, options, positional, kind="project", key=key, required=required)


_FORM_LIST: tuple[Form, ...] = (
    # --- inert: coreutils and readers ---
    _form("echo", "-n -e -E", "text*"),
    _form("sleep", "", "int"),
    _form("seq", "-s:lit -w -f:lit", "int*"),
    _form("date", "-u -R -I -j -d:lit", "text?"),
    _form("uname", "-a -s -m -r -n -p -v"),
    _form("hostname"),
    _form("whoami"),
    _form("id", "-u -g -n"),
    _form("basename", "-a -s:lit", "text*"),
    _form("dirname", "", "text*"),
    _form("realpath", "-e -m -q -s", "in*"),
    _form("readlink", "-f -e -m -n", "in*"),
    _form("env"),
    _form(
        "ls",
        "-l -a -A -h -t -r -S -R -d -1 -F -G -p -i -n -s -L --color:enum{never,always,auto}",
        "in*",
    ),
    _form("cat", "-n -b -s -A -e -t -v -u", "in*"),
    _form("head", "-n:int -N -c:int -q -v -f -F -r", "in*"),
    _form("tail", "-n:int -N -c:int -q -v -f -F -r", "in*"),
    _form("wc", "-l -w -c -m -L", "in*"),
    _form("uniq", "-c -d -u -i -f:int -s:int -w:int", "in?"),
    _form("cut", "-d:lit -f:lit -c:lit -b:lit -s --complement --output-delimiter:lit", "in*"),
    _form("tr", "-d -s -c -C", "lit lit?"),
    _form(
        "diff",
        "-u -U:int -r -q -N -w -b -B -i -p -y -a -c -x:lit --exclude:lit --brief"
        " --color:enum{never,always,auto} --strip-trailing-cr",
        "in1 in1",
    ),
    _form("cmp", "-l -s -n:int", "in1 in1"),
    _form(
        "grep",
        "-r -R -n -i -l -L -c -v -w -x -E -F -P -e:lit -A:int -B:int -C:int -o -h -H -s -q"
        " -m:int -z -a -I --null --color:enum{never,always,auto} --include:lit --exclude:lit"
        " --exclude-dir:lit --",
        "lit? in*",
    ),
    _form(
        "rg",
        "-n -i -S -l -c -v -w -x -F -e:lit -A:int -B:int -C:int -o -g:lit -t:lit -T:lit"
        " --type-add:lit --files --json --hidden --no-ignore --max-count:int --sort:lit"
        " --color:enum{never,always,auto,ansi} -r:lit --no-messages --no-config --",
        "lit? in*",
    ),
    _form("file", "-b -i --mime-type --mime-encoding -L -h", "in*"),
    _form("stat", "-c:lit --format:lit -f:lit -L -t -x", "in*"),
    _form("du", "-h -s -a -c -d:int --max-depth:int -x -k -m", "in*"),
    _form("df", "-h -k -H -i -P", "in*"),
    _form("ps", "aux -ef -A -e -f -x -p:lit -o:lit -u:lit -ww -c"),
    _form("pgrep", "-f -l -a -u:lit -x -n -o -d:lit", "lit"),
    _form("lsof", "-i:lit -p:lit -n -P -t -c:lit -u:lit -a", "in*"),
    _form("base64", "-d -D -w:int -b:int", "in*"),
    _form("shasum", "-a:int -b", "in*"),
    _form("column", "-t -s:lit -c:int -x", "in*"),
    _form(
        "jq",
        "-r -c -e -n -s -S -j -a --tab --indent:int --arg:lit:lit --argjson:lit:lit"
        " --raw-output --compact-output --slurp --null-input --exit-status --sort-keys",
        "lit in*",
    ),
    _form(
        "find",
        "-L -H -P -maxdepth:int -mindepth:int -depth -prune -name:lit -iname:lit -path:lit"
        " -ipath:lit -regex:lit -iregex:lit -type:lit -size:lit -empty -mtime:lit -mmin:lit"
        " -perm:lit -user:lit -group:lit -links:int -print -print0 -ls -not ! -a -o ( )",
        "dir*",
    ),
    _form("sed", "-n -E -r -s -u -z -e:lit --expression:lit --sandbox", "lit? in*"),
    _form("gsed", "-n -E -r -s -u -z -e:lit --expression:lit --sandbox", "lit? in*"),
    _form("awk", "-F:lit -v:lit --", "lit in|assign*"),
    _form("gawk", "-F:lit -v:lit --", "lit in|assign*"),
    _form("mawk", "-F:lit -v:lit --", "lit in|assign*"),
    _form("nawk", "-F:lit -v:lit --", "lit in|assign*"),
    _form("tar", "-t -f:in -z -j -J -v", required=(("-t",), ("-f",))),
    _form("unzip -l", "-q", "in1"),
    _form("unzip -t", "-q", "in1"),
    _form("unzip -p", "-q", "in1"),
    _form("unzip -Z", "-q", "in1"),
    _form("gitleaks detect", "--staged --no-banner -v --verbose --redact --exit-code:int --no-git"),
    _form(
        "gitleaks protect", "--staged --no-banner -v --verbose --redact --exit-code:int --no-git"
    ),
    _form("gitleaks version"),
    # --- inert: aws read operations ---
    _aws("sts", "get-caller-identity"),
    _aws("s3", "ls"),
    _aws("s3api", "list-buckets"),
    _aws("s3api", "list-objects-v2", "--bucket:lit --prefix:lit --delimiter:lit" + _AWS_PAGING),
    _aws("s3api", "head-object", "--bucket:lit --key:lit"),
    _aws("ec2", "describe-instances", "--instance-ids:lit --filters:lit" + _AWS_PAGING),
    _aws("ec2", "describe-security-groups", "--group-ids:lit --filters:lit" + _AWS_PAGING),
    _aws("ec2", "describe-vpcs", "--vpc-ids:lit --filters:lit" + _AWS_PAGING),
    _aws("ec2", "describe-subnets", "--subnet-ids:lit --filters:lit" + _AWS_PAGING),
    _aws("iam", "get-user", "--user-name:lit"),
    _aws("iam", "list-roles", "--path-prefix:lit --max-items:int"),
    _aws("lambda", "list-functions", "--max-items:int"),
    _aws("logs", "describe-log-groups", "--log-group-name-prefix:lit --max-items:int"),
    _aws(
        "logs",
        "filter-log-events",
        "--log-group-name:lit --log-stream-names:lit --filter-pattern:lit --start-time:int"
        " --end-time:int --limit:int --max-items:int",
    ),
    _aws("cloudformation", "describe-stacks", "--stack-name:lit"),
    _aws("cloudformation", "describe-stack-events", "--stack-name:lit --max-items:int"),
    _aws("ecs", "list-clusters"),
    _aws("ecs", "list-services", "--cluster:lit"),
    _aws("ecs", "describe-services", "--cluster:lit --services:lit"),
    _aws("rds", "describe-db-instances", "--db-instance-identifier:lit"),
    # --- inert: docker and terraform read forms ---
    _form("docker ps", "-a -q --format:lit --filter:lit -n:int -l -s"),
    _form("docker images", "-a -q --format:lit --filter:lit --digests"),
    _form("docker logs", "-f --tail:int --since:lit --until:lit -t -n:int", "name"),
    _form("docker info"),
    _form("docker version"),
    _form("docker inspect", "--format:lit --type:lit", "name*"),
    _form("terraform fmt -check", "-diff -recursive -no-color", "dir?"),
    _form("terraform version", "-json"),
    # --- project forms: the toolchain and this repo's own CLIs ---
    _form("pytest", _PYTEST_OPTS, "path*", kind="project"),
    _form("python3 -m pytest", _PYTEST_OPTS, "path*", kind="project"),
    _form("python -m pytest", _PYTEST_OPTS, "path*", kind="project"),
    _form("mypy", _MYPY_OPTS, "path*", kind="project"),
    _form("python3 -m mypy", _MYPY_OPTS, "path*", kind="project"),
    _form("python -m mypy", _MYPY_OPTS, "path*", kind="project"),
    _form("ruff check", _RUFF_OPTS, "path*", kind="project"),
    _form("python3 -m ruff check", _RUFF_OPTS, "path*", kind="project"),
    _form(
        "python3 install.py",
        "--status --dry-run",
        kind="project",
        required=(("--status", "--dry-run"),),
    ),
    _form(
        "python3 .claude/scripts/codex_seat.py",
        "--brief:path --out:path --stack:lit --timeout:int --dry-run",
        "enum{tester,reviewer,arch-reviewer}",
        kind="project",
    ),
    _form(
        "python3 .claude/scripts/debate.py",
        "--brief:path --out:path --seat:lit --judge:lit --max-calls:int --timeout:int"
        " --no-critique --dry-run",
        "enum{researcher,architect}",
        kind="project",
    ),
    _form(
        "python3 .claude/scripts/model_route.py",
        "--agent:lit --brief:path --explain",
        kind="project",
    ),
    _form(
        "python3 .claude/scripts/git_checkpoint.py",
        "--message-file:path --no-push",
        "lit?",
        kind="project",
    ),
    _form("python3 .claude/scripts/new_task.py", "--title-file:path", "lit?", kind="project"),
    _form("python3 .claude/scripts/bash_permission_gate.py", kind="project"),
    # --- project forms: git ---
    _git(
        "git status",
        "-s --short -b --branch --porcelain -u:lit --untracked-files:lit --ignored -z"
        " --no-ahead-behind",
        "path*",
    ),
    _git("git log", _LOG_OPTS, "ref* path*"),
    _git("git show", _LOG_OPTS, "ref* path*"),
    _git("git diff", _LOG_OPTS, "ref* path*"),
    _git(
        "git rev-parse",
        "--show-toplevel --abbrev-ref --verify --short --git-dir --is-inside-work-tree"
        " --symbolic-full-name",
        "ref*",
    ),
    _git("git rev-list", "--count -n:int --max-count:int --all --reverse", "ref*"),
    _git("git merge-base", "--is-ancestor", "ref ref"),
    _git("git describe", "--tags --always --abbrev:int --dirty", "ref?"),
    _git("git ls-files", "-z -c -o -m -d --exclude-standard --error-unmatch", "path*"),
    _git("git ls-tree", "-r -z --name-only", "ref path*"),
    _git("git cat-file", "-t -p -s -e", "ref"),
    _git("git blame", "-L:lit -w -s --porcelain", "path"),
    _git("git shortlog", "-s -n -e", "ref*"),
    _git("git reflog", "show -n:int", "ref?"),
    _git("git check-ignore", "-q -v", "path*"),
    _git("git count-objects", "-v"),
    _git("git grep", "-n -i -l -c -w -E -F -e:lit --cached --", "lit ref? path*"),
    _git("git remote", "-v"),
    _git("git remote show", "", "name", key="git remote"),
    _git("git remote get-url", "--push --all", "name", key="git remote"),
    _git(
        "git config",
        "--get:lit --get-all:lit --get-regexp:lit -l --list --show-origin --show-scope"
        " --global --local",
        required=(("--get", "--get-all", "--get-regexp", "-l", "--list"),),
    ),
    _git("git stash list", "-p", key="git stash"),
    _git("git stash show", "-p --stat", "lit?", key="git stash"),
    _git("git stash push", "-m:lit -u -k --", "path*", key="git stash"),
    _git("git stash pop", "", "lit?", key="git stash"),
    _git("git stash apply", "", "lit?", key="git stash"),
    _git("git worktree list", "--porcelain", key="git worktree"),
    _git("git worktree add", "-b:ref --detach", "path ref?", key="git worktree"),
    _git("git worktree prune", key="git worktree"),
    _git("git worktree remove", "", "path", key="git worktree"),
    _git("git tag", "-l --list -n:int --sort:lit --contains:ref -a -m:lit -d:ref", "ref? ref?"),
    _git("git ls-remote", "--heads --tags -q", "name? ref*"),
    _git("git add", "-A --all -u -N --intent-to-add -f -v -n --dry-run --", "path*"),
    _git(
        "git commit",
        "-m:lit --message:lit -a --all --amend --no-edit --allow-empty --allow-empty-message -q"
        " --date:lit --author:lit -F:path --file:path --",
        "path*",
    ),
    _git("git switch", "-c:ref --create:ref --detach -q -t --track", "ref?"),
    _git("git checkout", "-b:ref -q --detach -t --track --", "ref? path*"),
    _git("git restore", "-S --staged -W --worktree -s:ref --source:ref -q --", "path*"),
    _git(
        "git branch",
        "-a -r -v -vv -l --list --show-current --contains:ref --merged:ref --no-merged:ref"
        " -d:ref --delete:ref -m:ref:ref -u:ref --set-upstream-to:ref --unset-upstream"
        " --format:lit --sort:lit",
        "ref*",
    ),
    _git(
        "git fetch", "--prune -p --tags -t --depth:int -q --all --no-tags --dry-run", "name? ref*"
    ),
    _git(
        "git pull",
        "--ff-only --rebase -q --no-tags --depth:int --prune",
        "name? ref?",
        required=(("--ff-only", "--rebase"),),
    ),
    _git("git push", "-u --set-upstream -q --tags --dry-run -n", "name ref"),
    _git("git rebase", "--onto:ref -q --autostash --continue --abort --skip --quit", "ref*"),
    _git("git reset", "--soft --mixed -q --", "ref? path*"),
    _git("git rm", "--cached -r -q -n --", "path*"),
    _git("git init", "-q -b:ref --initial-branch:ref"),
    # --- project forms: gh ---
    _gh(
        "gh pr list",
        _GH_ISSUE_LIST + " -B:lit --base:lit -H:lit --head:lit",
    ),
    _gh("gh pr view", "--json:lit -q:lit --jq:lit -c --comments", "lit?"),
    _gh("gh pr status"),
    _gh("gh pr checks", "--watch", "lit?"),
    _gh("gh pr diff", "--patch --color:lit --name-only", "lit?"),
    _gh(
        "gh pr create",
        _GH_CREATE + " -B:lit --base:lit -H:lit --head:lit -d --draft -r:lit --reviewer:lit"
        " -f --fill",
    ),
    _gh("gh pr edit", _GH_EDIT + " --add-reviewer:lit", "lit?"),
    _gh("gh pr comment", _GH_COMMENT, "lit?"),
    _gh(
        "gh pr review",
        "-a --approve -c --comment -r --request-changes -b:lit -F:path",
        "lit?",
    ),
    _gh("gh pr ready", "", "lit?"),
    _gh("gh pr close", "-c:lit", "lit?"),
    _gh("gh pr reopen", "", "lit?"),
    _gh("gh pr checkout", "-b:lit", "lit"),
    _gh("gh issue list", _GH_ISSUE_LIST),
    _gh("gh issue view", "--json:lit -q:lit --jq:lit -c --comments", "lit?"),
    _gh("gh issue create", _GH_CREATE),
    _gh("gh issue edit", _GH_EDIT, "lit?"),
    _gh("gh issue comment", _GH_COMMENT, "lit?"),
    _gh("gh issue close", "-c:lit", "lit?"),
    _gh("gh issue reopen", "", "lit?"),
    _gh("gh run list", "-L:int -w:lit -b:lit --json:lit -q:lit"),
    _gh("gh run view", "--log --log-failed -j:lit --json:lit -q:lit", "lit?"),
    _gh("gh run watch", "--exit-status -i:int", "lit?"),
    _gh("gh run rerun", "--failed", "lit?"),
    _gh("gh repo view", "--json:lit -q:lit -b:lit", "lit?"),
    _gh("gh auth status"),
    _gh(
        "gh api",
        "-H:lit --header:lit -q:lit --jq:lit -t:lit --template:lit --cache:lit -p --preview:lit"
        " --paginate --slurp --silent -i --include --verbose -X:enum{GET} --method:enum{GET}",
        "endpoint",
    ),
)
_FORMS: dict[str, tuple[Form, ...]] = {}
for _f in _FORM_LIST:
    _FORMS.setdefault(_f.words[0], ())
    _FORMS[_f.words[0]] = (*_FORMS[_f.words[0]], _f)

# Subcommands that take a verb, per tool, so ``ask_words`` can match command words only.
_VERB_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git": frozenset({"remote", "stash", "worktree", "bisect", "submodule", "notes"}),
    "docker": frozenset(
        {"container", "image", "volume", "network", "compose", "buildx", "context", "system"}
        | {"builder", "manifest", "plugin", "trust", "swarm", "node", "service", "stack"}
        | {"config", "secret"}
    ),
    "terraform": frozenset({"state", "workspace", "providers"}),
}
_GH_SINGLE_WORD: frozenset[str] = frozenset({"api", "browse", "status", "completion", "help"})
# Global options a resolver may skip before the command words (value-taking ones listed with
# the number of values they take); anything else before the command words is unresolvable.
_RESOLVER_GLOBALS: dict[str, dict[str, int]] = {
    "gh": {"-R": 1, "--repo": 1, "--hostname": 1},
    "aws": {
        "--profile": 1,
        "--region": 1,
        "--output": 1,
        "--query": 1,
        "--no-cli-pager": 0,
        "--no-paginate": 0,
        "--cli-read-timeout": 1,
        "--cli-connect-timeout": 1,
        "--no-verify-ssl": 0,
        "--debug": 0,
        "--color": 1,
    },
}

_SED_ADDR = r"(?:\d+|\$|/(?:[^/\\]|\\.)*/)"
_SED_COMMAND: re.Pattern[str] = re.compile(
    rf"\s*(?:{_SED_ADDR}(?:[,~]{_SED_ADDR})?!?)?\s*"
    r"(?:s([^\\\n])(?:(?!\1)[^\\]|\\.)*\1(?:(?!\1)[^\\]|\\.)*\1[gIimp0-9]*"
    r"|y([^\\\n])(?:(?!\2)[^\\]|\\.)*\2(?:(?!\2)[^\\]|\\.)*\2"
    r"|[dp=qQnNDPhHgGxl]"
    r"|[aic]\\ [^\n;]*)"
    r"\s*(?:[;\n]|$)"
)
_AWK_EXITS: tuple[str, ...] = ("system", "getline", "|", ">", "@")
_ENDPOINT: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_./{}-]+$")
_SHAPE_RE: dict[str, re.Pattern[str]] = {
    "int": re.compile(r"^[0-9]+$"),
    "name": re.compile(r"^[A-Za-z0-9_.-]+$"),
    "ref": re.compile(r"^[A-Za-z0-9_./@^~{}-]+$"),
    "no:": re.compile(r"^no:[A-Za-z0-9_.-]+$"),
    "assign": re.compile(r"^[A-Za-z_][A-Za-z0-9_]*="),
    "endpoint": _ENDPOINT,
}


def _sed_script_safe(script: str) -> bool:
    """True when a sed script holds only the read-only commands the grammar lists."""
    pos = 0
    text = script.strip()
    if not text:
        return False
    while pos < len(text):
        m = _SED_COMMAND.match(text, pos)
        if m is None or m.end() == pos:
            return False
        pos = m.end()
    return True


def _awk_program_safe(program: str) -> bool:
    """True when an awk program holds none of awk's command and file exits."""
    return not any(exit_ in program for exit_ in _AWK_EXITS)


# ---------------------------------------------------------------------------------------------
# Form matching
# ---------------------------------------------------------------------------------------------


def _shape_ok(shape: str, word: Word) -> bool:
    """True when a literal word fits one value shape (``int``, ``lit``, ``enum{a,b}``, ``path``)."""
    if not word.literal:
        return False
    text = word.text
    for alternative in shape.split("|"):
        if alternative == "lit":
            ok = bool(text) and not text.startswith("-")
        elif alternative == "text":
            ok = True
        elif alternative in {"in", "dir"}:
            ok = _in_project_path(text)
        elif alternative == "path":
            ok = _in_project_path(text) and not text.startswith("/")
        elif alternative.startswith("enum{"):
            ok = text in alternative[5:-1].split(",")
        elif alternative in _SHAPE_RE:
            ok = bool(_SHAPE_RE[alternative].match(text))
            if alternative == "ref":
                ok = ok and not text.startswith("-") and ":" not in text
        else:
            ok = False
        if ok:
            return True
    return False


@dataclass(frozen=True)
class _Match:
    """A successful form match: the option values seen and the positional words."""

    form: Form
    options: dict[str, list[list[str]]]
    positional: list[str]


def _match_form(form: Form, args: list[Word]) -> _Match | None:
    """Match the words after a form's command words against its options and positional roles.

    Options may sit anywhere; a short flag may be clustered (``-la``); a short option with a value
    takes it attached (``-n1``) or next and must end its cluster; a long option with one value
    may use ``--opt=value``. Anything unlisted, any non-literal word, a positional beyond the
    roles: no match.
    """
    seen: dict[str, list[list[str]]] = {}
    positional: list[Word] = []
    options_done = False
    i = 0
    while i < len(args):
        word = args[i]
        if not word.literal:
            return None
        text = word.text
        if options_done or text == "-" or not text.startswith("-") and text not in form.options:
            positional.append(word)
            i += 1
            continue
        if text == "--":
            if "--" not in form.options:
                return None
            options_done = True
            i += 1
            continue
        if "-N" in form.options and text[1:].isdigit():
            seen.setdefault("-n", []).append([text[1:]])  # ``head -20`` is ``head -n 20``
            i += 1
            continue
        if text in form.options:
            consumed = _take_values(form, text, args, i + 1, seen)
            if consumed is None:
                return None
            i = consumed
            continue
        if form.positional and form.positional[-1] == "text*":
            options_done = True  # ``echo --no-verify`` prints its word; nothing else reads it
            positional.append(word)
            i += 1
            continue
        if text.startswith("--"):
            name, eq, value = text.partition("=")
            if name not in form.options or not eq:
                return None
            shapes = form.options[name]
            if len(shapes) != 1 or not _shape_ok(shapes[0], Word(value, True)):
                return None
            seen.setdefault(name, []).append([value])
            i += 1
            continue
        letters = text[1:]
        advanced = False
        for k, letter in enumerate(letters):
            opt = "-" + letter
            if opt not in form.options:
                return None
            shapes = form.options[opt]
            if not shapes:
                seen.setdefault(opt, []).append([])
                continue
            rest = letters[k + 1 :]
            if rest:
                if len(shapes) != 1 or not _shape_ok(shapes[0], Word(rest, True)):
                    return None
                seen.setdefault(opt, []).append([rest])
                i += 1
            else:
                consumed = _take_values(form, opt, args, i + 1, seen)
                if consumed is None:
                    return None
                i = consumed
            advanced = True
            break
        if not advanced:
            i += 1
    for group in form.required:
        if not any(opt in seen for opt in group):
            return None
    matched = _match_positional(form.positional, positional)
    if matched is None:
        return None
    return _Match(form, seen, matched)


def _take_values(
    form: Form, opt: str, args: list[Word], start: int, seen: dict[str, list[list[str]]]
) -> int | None:
    """Consume an option's values from ``args[start:]``; return the next index, or None."""
    shapes = form.options[opt]
    values: list[str] = []
    for offset, shape in enumerate(shapes):
        if start + offset >= len(args) or not _shape_ok(shape, args[start + offset]):
            return None
        values.append(args[start + offset].text)
    seen.setdefault(opt, []).append(values)
    return start + len(shapes)


def _match_positional(roles: tuple[str, ...], words: list[Word]) -> list[str] | None:
    """Fit positional words to roles in order; return their texts, or None."""
    i = 0
    for role in roles:
        quantifier = role[-1] if role[-1] in "*?1" else ""
        shape = role[:-1] if quantifier else role
        if quantifier == "*":
            while i < len(words) and _shape_ok(shape, words[i]):
                i += 1
            if i < len(words):
                return None
            continue
        if i < len(words) and _shape_ok(shape, words[i]):
            i += 1
        elif quantifier != "?":
            return None
    if i != len(words):
        return None
    return [w.text for w in words]


def _tool_check(match: _Match) -> bool:
    """Per-tool semantic checks a form cannot express: sed's script grammar, awk's exits."""
    tool = match.form.words[0]
    if tool in {"sed", "gsed"}:
        scripts = [v[0] for opt in ("-e", "--expression") for v in match.options.get(opt, [])]
        positional = list(match.positional)
        if not scripts:
            if not positional:
                return False
            scripts.append(positional.pop(0))
        return all(_sed_script_safe(s) for s in scripts) and all(
            _in_project_path(p) for p in positional
        )
    if tool in {"awk", "gawk", "mawk", "nawk"}:
        return bool(match.positional) and _awk_program_safe(match.positional[0])
    if tool == "grep" or tool == "rg":
        if not match.options.get("-e") and not match.options.get("--files"):
            return bool(match.positional)
    return True


def _resolve(tool: str, args: list[Word]) -> tuple[tuple[str, ...], list[Word]] | None:
    """Find a guarded tool's command words (subcommand, and its verb where one is taken).

    Known global options are skipped (``gh -R o/r``, ``aws --profile p``); any other option
    before the command words makes the part unresolvable (None): decision 4, the part asks.
    Returns the command words after the tool and the remaining argument words.
    """
    globals_ = _RESOLVER_GLOBALS.get(tool, {})
    command: list[str] = []
    rest: list[Word] = []
    wanted = 2 if tool == "aws" else 1
    i = 0
    while i < len(args):
        word = args[i]
        if len(command) >= wanted:
            rest.extend(args[i:])
            break
        text = word.text
        if text.startswith("-") and text != "-":
            name = text.partition("=")[0]
            if name not in globals_:
                if command and tool != "gh":
                    rest.extend(args[i:])  # an optional verb: ``git remote -v`` lists
                    break
                return None
            take = 1 + (globals_[name] if "=" not in text else 0)
            rest.extend(args[i : i + take])
            i += take
            continue
        if not word.literal:
            return None
        command.append(text)
        if len(command) == 1 and tool != "aws":
            takes_verb = text in _VERB_SUBCOMMANDS.get(tool, frozenset()) or (
                tool == "gh" and text not in _GH_SINGLE_WORD
            )
            wanted = 2 if takes_verb else 1
        i += 1
    return tuple(command), rest


def _find_form(head: str, args: list[Word]) -> _Match | None:
    """The form the part matches, if any: the longest command-word prefix wins."""
    forms = _FORMS.get(head, ())
    if not forms:
        return None
    if head in _RESOLVER_GLOBALS:
        resolved = _resolve(head, args)
        if resolved is None:
            return None
        command, rest = resolved
        for form in forms:
            if form.words[1:] == command:
                match = _match_form(form, rest)
                if match is not None and _tool_check(match):
                    return match
        return None
    texts = [w.text if w.literal else None for w in args]
    for form in sorted(forms, key=lambda f: -len(f.words)):
        n = len(form.words) - 1
        if texts[:n] == list(form.words[1:]):
            match = _match_form(form, args[n:])
            if match is not None and _tool_check(match):
                return match
    return None


def _selected(form: Form, ledger: dict[str, Any], part_text: str) -> bool:
    """True when the ledger selects this form: a key prefix for an inert form, the exact key for
    a project form, or an ``allow_patterns`` regex matching the part's normalized text.
    """
    keys = ledger_allow_keys(ledger)
    if form.kind == "inert":
        if any(tuple(k.split()) == form.words[: len(k.split())] for k in keys):
            return True
    elif form.key in keys:
        return True
    for entry in ledger.get("allow_patterns") or []:
        pattern = entry.get("pattern") if isinstance(entry, dict) else None
        if not pattern:
            continue
        try:
            if re.search(pattern, part_text):
                return True
        except re.error:
            continue
    return False


# ---------------------------------------------------------------------------------------------
# Modelling a command into parts
# ---------------------------------------------------------------------------------------------


def _unreadable_part(source: str, reason: str) -> Part:
    return Part(head="", words=(), readable=False, source=f"{source} [{reason}]")


def _part_from_lexed(lexed: _Lexed, depth: int) -> Part:
    """Turn one lexed segment into a Part: structural keywords, env prefix, head, nested lines."""
    words = list(lexed.words)
    structural: list[str] = []
    binds = False
    while words and words[0].literal and words[0].text in _STRUCTURAL | _BINDING_KEYWORDS:
        keyword = words.pop(0).text
        structural.append(keyword)
        if keyword in _BINDING_KEYWORDS:
            binds = True  # ``for NAME in …`` binds a name: the command cannot allow
            words = []
            break
    env: list[Word] = []
    if not binds:
        while words and words[0].literal and _ASSIGNMENT.match(words[0].text):
            env.append(words.pop(0))
    head = words[0].text if words else ""
    part = Part(
        head=head,
        words=tuple(words),
        env=tuple(env),
        redirects=tuple(lexed.redirects),
        heredocs=tuple(lexed.heredocs),
        structural=tuple(structural),
        binds=binds,
        source=" ".join(w.text for w in lexed.words),
    )
    nested = _nested_parts(part, depth)
    return replace(part, nested=tuple(nested)) if nested else part


def _model(text: str, depth: int) -> list[Part]:
    """Model a command line into parts, substitution bodies included (recursively)."""
    try:
        lexed = _lex_segments(text, depth)
    except _Unreadable as err:
        return [_unreadable_part(text, str(err))]
    parts: list[Part] = []
    for segment in lexed:
        parts.append(_part_from_lexed(segment, depth))
        for body in segment.bodies:
            if body == _OPAQUE_SUBSTITUTION:
                parts.append(_unreadable_part(body, "opaque substitution body"))
            else:
                parts.extend(_model(body, depth + 1))
    return parts


def model_command(command: str) -> list[Part]:
    """Model a Bash command into every part the verdict judges.

    Args:
        command: The raw command text from the hook input.

    Returns:
        Top-level parts (segments and substitution bodies, in order); a wrapper's nested command
        lines sit in each part's ``nested``.
    """
    return _model(command, 0)


def _nested_parts(part: Part, depth: int) -> list[Part]:
    """The command lines a wrapper, spawner or shell-string tool runs, modelled for the reason.

    Never widens anything: a part with a nested command always asks; the nested model only
    names the inner guard, and an unreadable inner line is reported as such.
    """
    if depth >= _MAX_NESTING or not part.words:
        return [_unreadable_part(part.source, "nested too deep")] if part.words else []
    head, rest = part.head, list(part.words[1:])
    if head in _SHELL_STRINGS and not (head == "script" and "-c" not in part.argv):
        return _shell_string_parts(head, rest, depth)
    if head in _SHELLS:
        if part.heredocs:
            return [p for body, _ in part.heredocs for p in _model(body, depth + 1)]
        herestring = [r.target for r in part.redirects if r.op.endswith("<<<")]
        if herestring:
            return _as_shell_string(herestring[0], depth)
        return []
    if head in _WRAPPERS or head in {"xargs", "watch", "script", "ssh", "hyperfine", "command"}:
        skipped = _skip_wrapper_options(head, rest)
        if skipped is None:
            return [_unreadable_part(part.source, f"{head} option not readable")]
        if head == "timeout" and skipped:
            skipped = skipped[1:]
        if head == "ssh" and skipped:
            skipped = skipped[1:]
        if head == "command" and skipped and skipped[0].text == "-v":
            return []
        return [_argv_part(skipped, depth)] if skipped else []
    if head == "find":
        for marker in ("-exec", "-execdir", "-ok", "-okdir"):
            if marker in part.argv:
                start = part.argv.index(marker) + 1
                inner = [w for w in part.words[start:] if w.text not in {";", "+"}]
                return [_argv_part(inner, depth)] if inner else []
    return []


def _skip_wrapper_options(wrapper: str, rest: list[Word]) -> list[Word] | None:
    """Drop a wrapper's leading options (value-taking ones with their value); None when unknown."""
    values = _WRAPPER_VALUE_OPTS.get(wrapper, frozenset())
    flags = _WRAPPER_FLAGS.get(wrapper, frozenset())
    while rest and rest[0].text.startswith("-"):
        option = rest[0].text
        if option == "--":
            return rest[1:]
        if option in values:
            rest = rest[2:]
        elif option in flags or option.split("=", 1)[0] in values | flags:
            rest = rest[1:]
        elif option[:2] in values and not option.startswith("--"):
            rest = rest[1:]
        else:
            return None
    while wrapper == "env" and rest and rest[0].literal and _ASSIGNMENT.match(rest[0].text):
        rest = rest[1:]
    return rest


def _argv_part(words: list[Word], depth: int) -> Part:
    """A nested part built from an argv already split into words."""
    inner = Part(head=words[0].text, words=tuple(words), source=" ".join(w.text for w in words))
    nested = _nested_parts(inner, depth + 1)
    return replace(inner, nested=tuple(nested)) if nested else inner


def _shell_string_parts(head: str, rest: list[Word], depth: int) -> list[Part]:
    """Re-lex the string a ``-c``-style tool runs; the operand after the option is the line."""
    option = _SHELL_STRINGS.get(head, "-c")
    for i, word in enumerate(rest):
        if word.text == option and i + 1 < len(rest):
            return _as_shell_string(rest[i + 1], depth)
        if word.text.startswith(option) and len(word.text) > len(option):
            return _as_shell_string(Word(word.text[len(option) :], word.literal), depth)
    if head in {"ssh", "watch", "hyperfine", "at", "crontab", "su"}:
        operands = [w for w in rest if not w.text.startswith("-")]
        if operands:
            return _as_shell_string(operands[-1], depth)
    return []


def _as_shell_string(word: Word, depth: int) -> list[Part]:
    """Model a word as a command line, or report it unreadable when it is not literal."""
    if not word.literal:
        return [_unreadable_part(word.text, "non-literal command string")]
    return _model(word.text, depth + 1)


# ---------------------------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Judgement:
    """One part's outcome: the verdict, its reason, and the form it matched (if any)."""

    kind: Verdict
    reason: str
    form: Form | None = None
    keyed: bool = False


def _is_bare_name(head: str) -> bool:
    return "/" not in head and not head.startswith((".", "~", "=")) and head != ""


def _head_kind(head: str) -> str:
    """Classify a literal bare-name head: code builtin, evaluating, scaffold, builtin, guarded
    (with its family), inert, or unknown.
    """
    if head in _CODE_BUILTINS or head == "command":
        return "code"
    if head in _EVALUATING_HEADS:
        return "evaluating"
    if head in _NO_MUTATION_BUILTINS:
        return "scaffold"
    if head in _OTHER_BUILTINS:
        return "builtin"
    if head in _WRAPPERS or head in _SPAWNERS or head in _SHELL_STRING_TOOLS or head in _SHELLS:
        return "wrapper"
    if (
        head in _GUARDED_TOOLS
        or head in _GUARDED_DB
        or head in _INTERPRETERS
        or _PYTHON.match(head)
        or head in _RUNNERS
        or head in _INSTALLERS
        or head in _OUTWARD
    ):
        return "guarded"
    if head in _FORMS:
        return "inert"
    return "unknown"


def _ask_words_note(head: str, args: list[Word], ledger: dict[str, Any]) -> str | None:
    """The note of the first ``ask_words`` rule the part trips at its command words, else None.

    A rule is ``{"tool": str, "all": [[word, ...], ...], "note": str}``: the head is ``tool`` and
    every group has a member among the resolved command words (a word ending ``-`` matches as a
    prefix; an empty ``all`` guards the tool itself).
    """
    rules = [r for r in ledger.get("ask_words") or [] if isinstance(r, dict) and r.get("tool")]
    rules = [r for r in rules if r["tool"] == head]
    if not rules:
        return None
    resolved = _resolve(head, args)
    if resolved is None:
        return (
            f"{head}: the subcommand could not be read (an option or a non-literal word) — confirm"
        )
    command_words = resolved[0]
    for rule in rules:
        groups: list[list[str]] = rule.get("all") or []
        if all(any(_word_hit(w, t) for w in group for t in command_words) for group in groups):
            return str(rule.get("note") or "matched a guarded word rule")
    return None


def _word_hit(word: str, token: str) -> bool:
    """Whether a token is a guarded word: equal to it, or — for a word ending in ``-`` (an
    ``aws`` verb prefix such as ``delete-``) — starting with it.
    """
    return token.startswith(word) if word.endswith("-") else token == word


def _evaluating_note(part: Part) -> str | None:
    """Ask-reason for an evaluating builtin with a payload, a subscript, ``-v`` or an arithmetic
    comparison on a non-numeric operand; None when it merely cannot allow.
    """
    head = part.head
    texts = [w.text for w in part.words[1:]]
    if head in {"printf", "test", "[", "[["} and "-v" in texts:
        return f"{head} -v evaluates a variable name — confirm"
    payload = any(m in t for t in texts for m in _PAYLOAD_MARKERS)
    if payload:
        return "a command substitution the shell may evaluate (builtin operand) — confirm"
    if any("[" in t and "]" in t for t in texts) and head not in {"test", "[", "[["}:
        return "a subscript an evaluating builtin may run — confirm"
    if head in {"test", "[", "[["}:
        for i, t in enumerate(texts):
            if t in _ARITHMETIC_COMPARISONS:
                operands = [texts[i - 1] if i else "", texts[i + 1] if i + 1 < len(texts) else ""]
                if not all(o.isdigit() for o in operands):
                    return "an arithmetic test on a non-numeric operand runs code — confirm"
    return None


def _scaffold_allowed(part: Part) -> bool:
    """True for the no-mutation builtins in harmless shapes (``exit 1``, ``set -euo pipefail``)."""
    args = [w.text for w in part.words[1:]]
    if part.head in {"true", "false", ":"}:
        return not args
    if part.head == "exit":
        return len(args) <= 1 and all(a.isdigit() for a in args)
    if part.head == "set":
        flags = [a for a in args if a != "pipefail"]
        return bool(args) and all(
            a.startswith("-") and len(a) > 1 and set(a[1:]) <= _SET_FLAG_LETTERS for a in flags
        )
    return False


def _judge(part: Part, ledger: dict[str, Any], *, assume_keyed: bool = False) -> _Judgement:
    """Judge one part on its own: ``allow`` (a candidate — the command-level rules still apply),
    ``ask`` with the reason, or ``none``.
    """
    if not part.readable:
        return _Judgement("ask", f"unreadable command text: {part.source}")
    if not part.head:
        reason = "assignment or redirect only" if (part.env or part.redirects) else "scaffolding"
        if not part.env and not part.redirects and not part.binds:
            return _Judgement("allow", reason)
        return _judgement_none(part, reason)
    head_word = part.words[0]
    if not head_word.literal:
        return _Judgement("ask", f"non-literal command name: {head_word.text}")
    head = part.head
    args = list(part.words[1:])
    if not _is_bare_name(head):
        basename = head.rsplit("/", 1)[-1]
        if _head_kind(basename) in {"guarded", "wrapper", "code"}:
            return _Judgement("ask", f"a path head whose program is guarded: {head}")
        return _judgement_none(part, f"path head {head} never auto-allows")
    nested_reason = next((j.reason for j in _judge_nested(part, ledger) if j.kind == "ask"), None)
    if len(args) == 1 and args[0].literal and args[0].text in _VERSION_WORDS:
        return _judgement_none(part, f"{head} {args[0].text}")
    kind = _head_kind(head)
    if head == "env" and not args:
        kind = "inert"  # bare ``env`` prints the environment; with operands it is a wrapper
    note = _ask_words_note(head, args, ledger) if kind in {"guarded", "wrapper"} else None
    if note is not None:
        return _Judgement("ask", note)
    if kind == "code":
        if head == "command" and args and args[0].text == "-v":
            return _judgement_none(part, "command -v is a builtin query")
        return _Judgement("ask", nested_reason or f"{head} evaluates or defines code — confirm")
    if kind == "evaluating":
        note = _evaluating_note(part)
        if note is not None:
            return _Judgement("ask", note)
        return _judgement_none(part, f"{head}: a builtin outside the no-mutation list")
    if kind == "wrapper":
        return _Judgement("ask", nested_reason or f"{head} runs another command — confirm")
    if kind == "builtin":
        return _judgement_none(part, f"{head} is a shell builtin outside the no-mutation list")
    if kind == "scaffold" and head != "echo":
        if _scaffold_allowed(part) and _part_clean(part):
            return _Judgement("allow", "scaffolding", keyed=True)
        return _judgement_none(part, f"{head} with arguments the gate does not model")
    if kind == "unknown":
        return _judge_unknown(part, ledger)
    if head in _EXECUTION_OPTIONS and any(
        w.text.partition("=")[0] in _EXECUTION_OPTIONS[head]
        or (head == "tar" and _TAR_WRITE_CLUSTER.match(w.text))
        for w in args
    ):
        return _Judgement(
            "ask", nested_reason or f"{head} with an option that runs or deletes — confirm"
        )
    match = _find_form(head, args)
    if match is not None and _part_clean(part):
        keyed = assume_keyed or _selected(match.form, ledger, " ".join(part.argv))
        if keyed:
            return _Judgement("allow", f"form {' '.join(match.form.words)}", match.form, True)
        return _judgement_none(
            part, f"form {' '.join(match.form.words)} has no ledger key", match.form
        )
    if kind == "guarded":
        return _Judgement("ask", f"{head} outside an allowed form — confirm")
    return _judgement_none(part, f"{head}: matches no inert form", match.form if match else None)


def _part_clean(part: Part) -> bool:
    """True when nothing about the part itself defeats allow: all literal, no env prefix, no
    write redirect, no name binding, no nested command line.
    """
    return (
        part.literal
        and not part.env
        and not part.write_redirect
        and not part.binds
        and not part.nested
    )


def _judgement_none(part: Part, reason: str, form: Form | None = None) -> _Judgement:
    """None — unless the part mentions an execution-bearing word, which asks (decision 14a)."""
    mentioned = [w.text for w in (*part.words, *part.env, *(r.target for r in part.redirects))]
    hit = next((w for w in mentioned if _execution_bearing(w)), None)
    if hit is not None:
        return _Judgement("ask", f"a non-allowed command mentions an execution-bearing path: {hit}")
    return _Judgement("none", reason, form)


def _judge_nested(part: Part, ledger: dict[str, Any]) -> list[_Judgement]:
    return [_judge(n, ledger) for n in part.nested]


def _judge_unknown(part: Part, ledger: dict[str, Any]) -> _Judgement:
    """An unknown literal head: its operands are re-lexed (H) — as an argv and each literal
    operand as a shell string; a guarded, unreadable or asking part inside asks. Otherwise a
    non-literal word asks and an all-literal part is none.
    """
    args = list(part.words[1:])
    if any(not w.literal for w in part.words) or any(not w.literal for w in part.env):
        return _Judgement("ask", f"{part.head}: a non-literal argument on an unknown command")
    candidates: list[Part] = []
    operands = [w for w in args if not w.text.startswith("-")]
    if operands and _is_bare_name(operands[0].text):
        candidates.append(Part(head=operands[0].text, words=tuple(args[args.index(operands[0]) :])))
    for word in operands:
        candidates.extend(_model(word.text, 1))
    for candidate in candidates:
        if candidate.head in _NO_MUTATION_BUILTINS or candidate.head in _OTHER_BUILTINS:
            continue
        judged = _judge(candidate, ledger, assume_keyed=True)
        if judged.kind == "ask":
            return _Judgement("ask", judged.reason)
        if candidate.words and _head_kind(candidate.head) == "guarded":
            return _Judgement("ask", f"{part.head} operand names a guarded tool: {candidate.head}")
    return _judgement_none(part, f"{part.head}: unknown command")


def _regex_note(command: str, parts: list[Part], ledger: dict[str, Any]) -> str | None:
    """The note of the first ``ask_patterns``/``denials`` regex hit on the raw command or on any
    part's normalized text (dequoted words joined), else None.
    """
    views = [command, *(" ".join(p.argv) for p in _flatten(parts) if p.words)]
    for entry in list(ledger.get("ask_patterns") or []) + list(ledger.get("denials") or []):
        pattern = entry.get("pattern") if isinstance(entry, dict) else None
        if not pattern:
            continue
        try:
            if any(re.search(pattern, view) for view in views):
                return str(entry.get("note") or "matched a guarded pattern")
        except re.error:
            continue
    return None


def _flatten(parts: list[Part]) -> list[Part]:
    out: list[Part] = []
    for part in parts:
        out.append(part)
        out.extend(_flatten(list(part.nested)))
    return out


def ask_match(command: str, ledger: dict[str, Any]) -> str | None:
    """Return the reason the command must prompt, else None (a convenience over ``verdict``)."""
    kind, reason = verdict(command, ledger)
    return reason if kind == "ask" else None


def verdict(command: str, ledger: dict[str, Any]) -> tuple[Verdict, str]:
    """Classify a command against the ledger.

    Args:
        command: The raw Bash command.
        ledger: The loaded permission ledger.

    Returns:
        ``("allow", reason)`` when every part is a keyed form and nothing mutates shell state;
        ``("ask", reason)`` when a guarded thing is involved; ``("none", reason)`` otherwise.
    """
    parts = model_command(command)
    note = _regex_note(command, parts, ledger)
    if note is not None:
        return "ask", note
    judgements = [_judge(p, ledger) for p in parts]
    for judged in judgements:
        if judged.kind == "ask":
            return "ask", judged.reason
    if not parts:
        return "none", "empty command"
    if all(j.kind == "allow" for j in judgements):
        return "allow", "every part is a keyed form"
    reason = next(j.reason for j in judgements if j.kind != "allow")
    return "none", reason


def unkeyed_inert_forms(command: str, ledger: dict[str, Any]) -> list[tuple[Form, str]] | None:
    """The inert forms whose missing ledger key is the only thing between the command and allow.

    Used by the recorder: each entry is the form and the part's normalized text. Returns None
    when anything else stands in the way (an ask, a project form, an unknown tool, a non-literal
    part, an assignment word), so nothing outside the form table can ever become a grant.
    """
    parts = model_command(command)
    if not parts or _regex_note(command, parts, ledger) is not None:
        return None
    unkeyed: list[tuple[Form, str]] = []
    for part in parts:
        judged = _judge(part, ledger, assume_keyed=True)
        if judged.kind != "allow":
            return None
        if judged.form is not None and not _selected(judged.form, ledger, " ".join(part.argv)):
            if judged.form.kind != "inert":
                return None
            unkeyed.append((judged.form, " ".join(part.argv)))
    return unkeyed


# ---------------------------------------------------------------------------------------------
# Ledger and hook I/O
# ---------------------------------------------------------------------------------------------


def load_ledger() -> dict[str, Any]:
    """Read the permission ledger; on any failure return an empty ledger (=> nothing allows)."""
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def ledger_allow_keys(ledger: dict[str, Any]) -> list[str]:
    """All allow keys: the seeded ``allow_keys`` plus recorded ``grants``."""
    keys = [k for k in (ledger.get("allow_keys") or {}) if isinstance(k, str) and k.strip()]
    keys += [g["key"] for g in (ledger.get("grants") or []) if isinstance(g, dict) and g.get("key")]
    return sorted({" ".join(k.split()) for k in keys})


def _decision(kind: str, reason: str) -> str:
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": kind,
                "permissionDecisionReason": reason,
            }
        }
    )


def main() -> None:
    """Classify the incoming Bash command against the ledger and emit a permission decision
    (or none, logging the command to the unknowns log).
    """
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command", "")
    if not command.strip():
        return
    kind, reason = verdict(command, load_ledger())
    if kind == "ask":
        print(_decision("ask", f"guarded by permission ledger: {reason}"))
        return
    if kind == "allow":
        print(_decision("allow", "all parts covered by the permission ledger"))
        return
    try:
        UNKNOWN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with UNKNOWN_LOG.open("a", encoding="utf-8") as handle:
            handle.write(command.splitlines()[0][:200] + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
