"""Documented routing rubric and CLI contracts, with all input held in memory."""

import io
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (word, (0, 2, 0, 0, 2))
        for word in (
            "new",
            "create",
            "design",
            "from scratch",
            "architecture",
            "introduce",
            "greenfield",
        )
    ]
    + [
        (word, (0, -1, 0, 0, 0))
        for word in ("fix", "rename", "typo", "bump", "docstring", "comment", "reword", "format")
    ]
    + [
        (word, (0, 0, 1, 0, 1))
        for word in (
            "investigate",
            "debug",
            "root cause",
            "unclear",
            "decide",
            "choose",
            "options",
            "why does",
            "intermittent",
            "flaky",
        )
    ]
    + [
        (word, (0, 0, 0, 2, 2))
        for word in (
            "migration",
            "auth",
            "security",
            "concurrency",
            "race",
            "async",
            "crypto",
            "payment",
            "delete",
            "data loss",
            "production",
            "terraform",
            "infra",
            "permission",
            "ledger",
        )
    ],
)
def test_each_signal_in_isolation(
    model_route: ModuleType, text: str, expected: tuple[int, int, int, int, int]
) -> None:
    """Each documented phrase contributes only to its own signal."""
    assert model_route.score_brief(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", (0, 0, 0, 0, 0)),
        ("new fix rename", (0, 2, 0, 0, 2)),
        ("new create design", (0, 2, 0, 0, 2)),
        ("debug DEBUG debug auth AUTH auth", (0, 0, 1, 2, 3)),
        ("asyncio", (0, 0, 0, 0, 0)),
        ("asyncio async", (0, 0, 0, 2, 2)),
        ("renewed redesign prefix debugging authorization", (0, 0, 0, 0, 0)),
        ("ROOT\nCAUSE data\tLOSS FROM   SCRATCH", (0, 2, 1, 2, 5)),
        ("debug unclear choose investigate auth migration crypto payment", (0, 0, 3, 6, 9)),
        ("fix debug auth", (0, -1, 1, 2, 2)),
    ],
)
def test_distinct_whole_phrases_caps_and_total(
    model_route: ModuleType, text: str, expected: tuple[int, int, int, int, int]
) -> None:
    """Repetition, substrings, whitespace, caps, and routine discounts follow the rubric."""
    assert model_route.score_brief(text) == expected


@pytest.mark.parametrize(
    ("count", "points"), [(0, 0), (1, 0), (2, 1), (3, 1), (4, 2), (8, 2), (9, 3)]
)
@pytest.mark.parametrize(("length", "bonus"), [(2500, 0), (2501, 1)])
def test_size_boundaries(
    model_route: ModuleType, count: int, points: int, length: int, bonus: int
) -> None:
    """Path step boundaries and the length bonus compose independently."""
    text = " ".join(f"file{i}.py" for i in range(count))
    text += " " * (length - len(text))
    assert model_route.score_brief(text) == (points + bonus, 0, 0, 0, points + bonus)


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("`src/a.py`, (src/a.py) src/a.py", 1),
        ("/ /// prose", 0),
        ("src/one bare.PY config.toml", 3),
        ("[a.md] b.json; c.yaml!", 3),
    ],
)
def test_distinct_paths(model_route: ModuleType, text: str, count: int) -> None:
    """Punctuation and repeated mentions do not inflate distinct path counts."""
    assert model_route.count_paths(text) == count


@pytest.mark.parametrize(
    ("total", "tier"),
    [
        (0, "haiku"),
        (1, "haiku"),
        (2, "sonnet"),
        (5, "sonnet"),
        (6, "opus"),
        (11, "opus"),
        (12, "fable"),
        (100, "fable"),
    ],
)
def test_tier_boundaries(model_route: ModuleType, total: int, tier: str) -> None:
    """Threshold endpoints belong to the cheaper tier."""
    assert model_route.tier_for(total) == tier


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        ("architect", ("opus", "opus", "opus", "fable")),
        ("git-workflow", ("haiku", "sonnet", "sonnet", "sonnet")),
        ("researcher", ("haiku", "sonnet", "sonnet", "sonnet")),
        ("unknown-agent", ("haiku", "sonnet", "opus", "fable")),
    ],
)
def test_agent_bounds(model_route: ModuleType, agent: str, expected: tuple[str, ...]) -> None:
    """Both extremes and interior tiers honor each documented agent's bounds."""
    assert set(model_route.AGENT_BOUNDS) == {"architect", "git-workflow", "researcher"}
    for tier, clamped in zip(("haiku", "sonnet", "opus", "fable"), expected, strict=True):
        assert model_route.clamp(tier, agent) == clamped


@pytest.mark.parametrize(
    ("agent", "expected"), [("architect", "fable"), ("researcher", "sonnet"), ("other", "fable")]
)
def test_route_composes_scores_and_bounds(
    model_route: ModuleType, agent: str, expected: str
) -> None:
    """A complex brief reaches fable except when the agent ceiling intervenes."""
    text = "new debug unclear choose auth migration crypto " + " ".join(
        f"f{i}.py" for i in range(9)
    )
    assert model_route.route(agent, text) == (expected, (3, 2, 3, 6, 14))


@pytest.mark.parametrize("explain", [False, True])
def test_cli_stdin_output_channels(
    model_route: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    explain: bool,
) -> None:
    """Only the tier reaches stdout; explanations retain the negative novelty signal."""
    monkeypatch.setattr(model_route.sys, "stdin", io.StringIO("fix"))
    argv = ["--agent", "architect"] + (["--explain"] if explain else [])
    assert model_route.main(argv) == 0
    captured = capsys.readouterr()
    assert captured.out == "opus\n"
    assert captured.err == (
        "size: 0\nnovelty: -1\nambiguity: 0\nrisk: 0\n"
        "total: 0 -> haiku\nbounds: architect [opus, fable] -> opus\n"
        if explain
        else ""
    )


@pytest.mark.parametrize(
    "argv",
    [[], ["--agent"], ["--agent", "architect", "--brief"], ["--agent", "architect", "--invalid"]],
)
def test_cli_bad_arguments_exit_two(
    model_route: ModuleType,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    """Malformed arguments terminate without emitting a tier."""
    with pytest.raises(SystemExit) as exc:
        model_route.main(argv)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err


def test_cli_file_input(
    model_route: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An explicit file is read as UTF-8 instead of consuming stdin."""
    read = Mock(return_value="new")
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(model_route.sys, "stdin", Mock(read=Mock(side_effect=AssertionError)))
    assert model_route.main(["--agent", "other", "--brief", "brief.md"]) == 0
    read.assert_called_once_with(encoding="utf-8")
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("sonnet\n", "")


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("missing"),
        PermissionError("denied"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
    ],
)
def test_cli_unreadable_file_returns_two(
    model_route: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    """Unreadable or invalid UTF-8 briefs produce diagnostics without a tier."""
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=error))
    assert model_route.main(["--agent", "other", "--brief", "brief.md"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot read brief:" in captured.err
