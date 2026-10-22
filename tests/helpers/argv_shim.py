"""Conservative, independent admission and recording shims for real-shell tests."""

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

# Never include env/command/builtin/exec/hash/enable: these bypass a shim-only PATH.
SHIM_HEADS = frozenset(
    {
        "git",
        "gh",
        "aws",
        "docker",
        "terraform",
        "python3",
        "python",
        "node",
        "perl",
        "awk",
        "sed",
        "find",
        "xargs",
        "timeout",
        "nice",
        "nohup",
        "stdbuf",
        "watch",
        "script",
        "parallel",
        "make",
        "npm",
        "npx",
        "dropdb",
        "psql",
        "cat",
        "wc",
        "ls",
        "head",
        "tail",
        "rg",
        "grep",
        "jq",
        "tee",
        "sh",
        "bash",
        "zsh",
    }
)
GUARDED_SHIMS = SHIM_HEADS - {"cat", "wc", "ls", "head", "tail", "rg", "grep", "jq", "tee"}
BUILTINS = frozenset({"true", "false", ":", "echo", "exit"})
PARSE_ONLY = re.compile(r"\b(?:set|printf|typeset|read|env|command|builtin|exec|hash|enable)\b")

# These expansions have been manually reviewed independently of model_command. All possible
# command heads are fixed shims or echo; none can name a path or mutate the environment.
REVIEWED_EXPANSIONS = {
    "git $(echo merge) y",
    "$(echo git) merge y",
    "$(echo gh) pr merge 1",
    "$(echo terraform) destroy -auto-approve",
    "`echo docker` push img",
    "$(echo dropdb) db",
    "$(echo aws) s3 rm s3://b/k",
    "$(echo git) clean -fdx",
    "echo $(git merge y)",
    "echo `git merge y`",
    'echo "$(git rev-parse HEAD)"',
}


def eligible(command: str) -> bool:
    """Admit only reviewed expansions or literal simple pipelines/and-or command lists.

    Reject dynamic heads, assignments, redirects, function/control grammar and path heads.
    Safety does not depend on the production parser's interpretation or verdict.
    """
    if PARSE_ONLY.search(command):
        return False
    if command in REVIEWED_EXPANSIONS:
        return True
    if any(c in command for c in "$`\n\r<>(){}*?[]~"):
        return False
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        words = list(lexer)
    except ValueError:
        return False
    need_head = True
    for word in words:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word):
            return False
        if word in {";", "&&", "||", "|"}:
            if need_head:
                return False
            need_head = True
        elif word in {"&", "|&", ";;"}:
            return False
        elif need_head:
            if word not in SHIM_HEADS | BUILTINS:
                return False
            need_head = False
    return bool(words) and not need_head


def run_shims(shell: str, command: str, directory: Path) -> list[tuple[str, ...]]:
    """Run an admitted row in a disposable cwd with only recording programs on PATH."""
    assert eligible(command), f"Unsafe differential row: {command}"
    bindir = directory / "bin"
    bindir.mkdir()
    home = directory / "home"
    home.mkdir()
    log = directory / "argv.jsonl"
    # The absolute interpreter runs only this fixed recorder. Tool arguments are data,
    # never code, and neither imports nor subprocesses depend on the shim argv.
    program = (
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['ARGV_LOG'], 'a', encoding='utf-8') as out:\n"
        "    out.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + '\\n')\n"
    )
    for head in SHIM_HEADS:
        path = bindir / head
        path.write_text(program, encoding="utf-8")
        path.chmod(0o700)
    flags = ["--noprofile", "--norc"] if shell == "/bin/bash" else ["-f"]
    completed = subprocess.run(
        [shell, *flags, "-c", command],
        cwd=directory,
        env={"PATH": str(bindir), "HOME": str(home), "ARGV_LOG": str(log)},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 0, (command, completed.returncode, completed.stderr)
    return (
        [tuple(json.loads(line)) for line in log.read_text().splitlines()] if log.exists() else []
    )
