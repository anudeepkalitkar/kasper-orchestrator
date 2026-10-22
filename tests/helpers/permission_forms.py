"""Independent examples from the design's option tables, never from production forms."""

# Keys and operands are deliberately explicit: a missing seed form must fail coverage.
FORM_EXAMPLES: dict[str, str] = {
    "echo": "echo hello",
    "sleep": "sleep 1",
    "seq": "seq 1 3",
    "date": "date +%Y",
    "uname": "uname -a",
    "hostname": "hostname",
    "whoami": "whoami",
    "id": "id -u",
    "basename": "basename a.txt",
    "dirname": "dirname a/b",
    "realpath": "realpath f",
    "readlink": "readlink f",
    "env": "env",
    "ls": "ls -la",
    "cat": "cat -n f",
    "head": "head -n 3 f",
    "tail": "tail -n3 f",
    "wc": "wc -l f",
    "uniq": "uniq -c f",
    "cut": "cut -d , -f 1 f",
    "tr": "tr a b",
    "diff": "diff -u a b",
    "cmp": "cmp a b",
    "grep": "grep -n needle f",
    "rg": "rg -n needle f",
    "file": "file f",
    "stat": "stat -c %s f",
    "du": "du -s f",
    "df": "df -h .",
    "ps": "ps aux",
    "pgrep": "pgrep -f app",
    "lsof": "lsof -p 123",
    "base64": "base64 f",
    "shasum": "shasum -a 256 f",
    "column": "column -t f",
    "jq": "jq . f",
    "find": "find . -name '*.py' -print",
    "sed": "sed -n '1,3p' f",
    "awk": "awk '{print $1}' f",
    "tar": "tar -tf a.tar",
    "unzip": "unzip -l a.zip",
    "gitleaks": "gitleaks detect --no-banner",
    "docker": "docker ps -a",
    "terraform": "terraform fmt -check",
    "python3 install.py": "python3 install.py --status",
    "python3 .claude/scripts/codex_seat.py": "python3 .claude/scripts/codex_seat.py tester",
    "python3 .claude/scripts/debate.py": "python3 .claude/scripts/debate.py architect",
    "python3 .claude/scripts/model_route.py": "python3 .claude/scripts/model_route.py --explain",
    "python3 .claude/scripts/git_checkpoint.py": (
        "python3 .claude/scripts/git_checkpoint.py --no-push"
    ),
    "python3 .claude/scripts/new_task.py": "python3 .claude/scripts/new_task.py title",
    "python3 .claude/scripts/bash_permission_gate.py": (
        "python3 .claude/scripts/bash_permission_gate.py"
    ),
    "git status": "git status --short",
    "git log": "git log --oneline",
    "git show": "git show HEAD",
    "git diff": "git diff --stat",
    "git rev-parse": "git rev-parse HEAD",
    "git rev-list": "git rev-list --count HEAD",
    "git merge-base": "git merge-base a b",
    "git describe": "git describe --tags",
    "git ls-files": "git ls-files",
    "git ls-tree": "git ls-tree HEAD",
    "git cat-file": "git cat-file -t HEAD",
    "git blame": "git blame f",
    "git shortlog": "git shortlog -s",
    "git reflog": "git reflog show",
    "git check-ignore": "git check-ignore f",
    "git count-objects": "git count-objects -v",
    "git grep": "git grep needle",
    "git remote": "git remote -v",
    "git config": "git config --get user.name",
    "git stash": "git stash list",
    "git worktree": "git worktree list",
    "git tag": "git tag -l",
    "git ls-remote": "git ls-remote origin",
    "git add": "git add f",
    "git commit": "git commit -m message",
    "git switch": "git switch feat/x",
    "git checkout": "git checkout feat/x",
    "git restore": "git restore f",
    "git branch": "git branch --list",
    "git fetch": "git fetch origin",
    "git pull": "git pull --ff-only",
    "git rebase": "git rebase master",
    "git reset": "git reset --soft HEAD",
    "git rm": "git rm --cached f",
    "git init": "git init",
    "gh auth status": "gh auth status",
    "gh api": "gh api repos/o/r -XGET",
    "gh repo view": "gh repo view",
    "gh run list": "gh run list -L 3",
    "gh run view": "gh run view 1",
    "gh run watch": "gh run watch 1",
    "gh run rerun": "gh run rerun 1",
}
for _runner in ("pytest", "mypy", "ruff check"):
    for _prefix in ("", "python3 -m ", "python -m "):
        if _runner == "ruff check" and _prefix == "python -m ":
            continue
        FORM_EXAMPLES[_prefix + _runner] = _prefix + _runner + " tests/unit"
for _noun, _verbs in (
    ("pr", "list view status checks diff create edit comment review ready close reopen checkout"),
    ("issue", "list view create edit comment close reopen"),
):
    for _verb in _verbs.split():
        _key = f"gh {_noun} {_verb}"
        FORM_EXAMPLES[_key] = _key + (" 1" if _verb == "checkout" else "")
for _operation in (
    "sts get-caller-identity",
    "s3 ls",
    "s3api list-buckets",
    "s3api list-objects-v2",
    "s3api head-object",
    "ec2 describe-instances",
    "ec2 describe-security-groups",
    "ec2 describe-vpcs",
    "ec2 describe-subnets",
    "iam get-user",
    "iam list-roles",
    "lambda list-functions",
    "logs describe-log-groups",
    "logs filter-log-events",
    "cloudformation describe-stacks",
    "cloudformation describe-stack-events",
    "ecs list-clusters",
    "ecs list-services",
    "ecs describe-services",
    "rds describe-db-instances",
):
    FORM_EXAMPLES["aws " + _operation] = "aws " + _operation

# Explicit forbidden option classes in the spec. Applied to each relevant seeded form.
FORBIDDEN_OPTIONS: dict[str, list[str]] = {
    "pytest": [
        "-p plugin",
        "-o x=y",
        "--override-ini x=y",
        "-c c.ini",
        "--rootdir d",
        "--basetemp d",
        "--junitxml out",
        "--pdb",
        "--pdbcls mod:Class",
    ],
    "mypy": [
        "--config-file c.ini",
        "--install-types",
        "--python-executable p",
        "--cache-dir d",
        "--html-report d",
        "-c code",
    ],
    "ruff check": ["--fix", "--config c.toml", "-o out"],
    "python3 install.py": ["--uninstall", "--home d", "--python p"],
    "python3 .claude/scripts/new_task.py": ["--tasks-dir d"],
    "git log": ["--output out", "--ext-diff", "--textconv", "-O order", "--open-files-in-pager"],
    "git show": ["--output out", "--ext-diff", "--textconv", "-O order", "--open-files-in-pager"],
    "git diff": ["--output out", "--ext-diff", "--textconv", "-O order", "--open-files-in-pager"],
    "git add": ["-p", "-i", "-e"],
    "git commit": [
        "-e",
        "--edit",
        "-p",
        "-i",
        "-S",
        "--gpg-sign",
        "-t f",
        "--template f",
        "-c HEAD",
        "-C HEAD",
        "--no-verify",
    ],
    "git branch": ["-D main", "-M a b", "--edit-description"],
    "git tag": ["-s", "-u key", "-e", "-F f"],
    "git rebase": [
        "-i",
        "-x cmd",
        "--exec cmd",
        "-s strategy",
        "-X option",
        "--autosquash",
        "--edit-todo",
    ],
    "git reset": ["--hard", "--merge", "--keep", "-p"],
    "git worktree": ["--force"],
    "gh api": [
        "-f x=y",
        "-F x=y",
        "--field x=y",
        "--raw-field x=y",
        "--input f",
        "-X POST",
        "--method=PUT",
        "-XPATCH",
        "--method DELETE",
    ],
    "grep": ["-f patterns"],
    "rg": ["-f patterns", "--pre cmd", "--pre-glob '*.py'"],
    "shasum": ["-c checksums"],
    "jq": ["-f f", "--rawfile x f", "--slurpfile x f", "-L d"],
    "find": [
        "-exec cmd {} +",
        "-execdir cmd {} +",
        "-ok cmd {} +",
        "-okdir cmd {} +",
        "-delete",
        "-fprint out",
        "-fprintf out %p",
        "-fls out",
        "-newer f",
    ],
    "sed": ["-i", "-f script"],
    "awk": ["-f f", "-e code", "--source code", "-l lib", "-i lib", "-E f", "--exec f", "-M"],
    "tar": [
        "--to-command=cmd",
        "-I cmd",
        "--use-compress-program=cmd",
        "--checkpoint-action=exec=cmd",
    ],
    "gitleaks": ["--source d", "--config f", "--report-path out", "--log-opts x"],
}


def forbidden_cases() -> list[tuple[str, str, str]]:
    """Expand spec option exclusions for every relevant seeded form; no production lookup."""
    rows: list[tuple[str, str, str]] = []
    for key, command in FORM_EXAMPLES.items():
        options = list(FORBIDDEN_OPTIONS.get(key, []))
        for runner in ("pytest", "mypy", "ruff check"):
            if key.endswith("-m " + runner):
                options += FORBIDDEN_OPTIONS[runner]
        if key.startswith("aws "):
            options += ["--cli-input-json file://f", "--cli-input-yaml file://f"]
        if key.startswith("gh "):
            options += ["--web", "--editor", "-e"]
        guarded = key.split()[0] in {
            "git",
            "gh",
            "aws",
            "docker",
            "terraform",
            "python3",
            "python",
            "pytest",
            "mypy",
            "ruff",
            "sed",
            "awk",
        }
        for option in options:
            execution = (
                key == "tar"
                or key == "rg"
                and option.startswith("--pre")
                or key == "find"
                and option.split()[0] in {"-exec", "-execdir", "-ok", "-okdir", "-delete"}
            )
            rows.append((key, command + " " + option, "ask" if guarded or execution else "none"))
        if key.startswith("git "):
            for global_option in (
                "-C d",
                "-c x=y",
                "--git-dir d",
                "--work-tree d",
                "--exec-path d",
                "--config-env x=y",
                "--namespace n",
                "--attr-source HEAD",
                "-p",
                "-P",
                "--no-pager",
            ):
                rows.append((key, "git " + global_option + command[3:], "ask"))
    return rows
