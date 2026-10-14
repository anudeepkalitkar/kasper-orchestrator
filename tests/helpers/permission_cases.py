"""Shared hook-bypass expectations for pure matching and the shipped ledger."""

HOOK_BYPASS_CASES: list[tuple[str, bool]] = [
    ("git commit --no-verify -m x", True),
    ('git commit --no-""verify -m x', True),
    ("git commit \\\n--no-verify -m x", True),
    ("git -c core.hooksPath=/tmp/h commit -m x", True),
    ("echo git --no-verify", False),
    ("git status && echo --no-verify", False),
    ("git commit -m x", False),
    ("git commit -m x # --no-verify", True),
    ('MODE=test /usr/bin/git commit --no-""verify -m x', True),
    ('env command git commit --no-""verify -m x', True),
    ('echo ready && git commit --no-""verify -m x', True),
    ('echo ready; git commit --no-""verify -m x', True),
    ('echo ready\ngit commit --no-""verify -m x', True),
]


FEATURE_PUSH_CASES: list[tuple[str, bool]] = [
    ("git push -u origin docs/x", True),
    ("git push -u origin chore/release-v19", True),
    ("git push origin refactor/y", True),
    ("git push origin test/z", True),
    ("git push -u origin feat/x", True),
    ("git push -u origin fix/x", True),
    ("git push -u origin feature/x", True),
    ("git push origin development", False),
    ("git push origin master", False),
]
