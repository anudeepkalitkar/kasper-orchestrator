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
