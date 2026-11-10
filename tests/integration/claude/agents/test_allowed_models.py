"""Agent model declarations must agree with the authoritative routing table."""

import re
from pathlib import Path


def test_agent_allowed_models_match_routing_table(repo_root: Path) -> None:
    """Require one body declaration per agent, exact coverage, and only valid tiers."""
    claude = repo_root / ".claude"
    routing = (claude / "rules" / "model-routing.md").read_text(encoding="utf-8")
    rows = [line.strip() for line in routing.splitlines() if line.strip().startswith("|")]
    assert len(rows) > 2, "Missing allowed-model table"
    assert [cell.strip() for cell in rows[0].strip("|").split("|")] == [
        "Agent",
        "Allowed models",
    ]
    valid_tiers = {"haiku", "sonnet", "opus", "fable"}
    allowed: dict[str, set[str]] = {}
    for row in rows[2:]:
        cells = re.split(r"(?<!\\)\|", row.strip("|"))
        assert len(cells) == 2, f"Malformed routing row: {row}"
        names, models = cells
        tiers = {tier.strip() for tier in models.replace(r"\|", "|").split("|")}
        assert tiers and tiers <= valid_tiers, f"Invalid table tiers: {row}"
        for name in names.split(","):
            name = name.strip()
            assert name and name not in allowed, f"Empty or duplicate table agent: {name!r}"
            allowed[name] = tiers

    agent_files = sorted((claude / "agents").glob("*.md"))
    assert {path.stem for path in agent_files} == allowed.keys()
    for path in agent_files:
        lines = path.read_text(encoding="utf-8").splitlines()
        declarations = [
            index for index, line in enumerate(lines) if line.strip().startswith("Allowed models:")
        ]
        assert len(declarations) == 1, f"{path.name}: expected exactly one Allowed models line"
        index = declarations[0]
        assert lines[0] == "---" and index > lines.index("---", 1), (
            f"{path.name}: Allowed models must be in the body"
        )
        tiers = {tier.strip() for tier in lines[index].split(":", 1)[1].split("|")}
        assert tiers <= valid_tiers, f"{path.name}: invalid tiers {tiers - valid_tiers}"
        assert tiers == allowed[path.stem], f"{path.name}: tiers differ from routing table"
