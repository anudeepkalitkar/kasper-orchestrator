"""Agent metadata contract."""

from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

AGENT_FILES = sorted((REPO_ROOT / ".claude" / "agents").glob("*.md"))
REQUIRED_FIELDS = {"name", "description", "tools", "model"}


@pytest.mark.parametrize("agent_file", AGENT_FILES, ids=lambda path: path.stem)
def test_agent_starts_with_required_frontmatter_matching_filename(agent_file: Path) -> None:
    """Each agent declares nonempty metadata and a name matching its file stem."""
    lines = agent_file.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0] == "---", f"{agent_file.name}: missing opening delimiter"
    assert "---" in lines[1:], f"{agent_file.name}: missing closing delimiter"
    header = lines[1 : lines.index("---", 1)]
    fields = dict(line.split(":", 1) for line in header if ":" in line)
    assert REQUIRED_FIELDS <= fields.keys(), f"{agent_file.name}: missing required metadata"
    for key in REQUIRED_FIELDS:
        value = fields[key].strip()
        assert value, f"{agent_file.name}: empty {key}"
        # YAML plain scalars cannot contain a mapping separator (colon plus space).
        if not value.startswith(('"', "'", "|", ">", "[", "{")):
            assert ": " not in value and ":\t" not in value, (
                f"{agent_file.name}: {key} contains an unquoted YAML mapping separator"
            )
    assert fields["name"].strip().strip("\"'") == agent_file.stem
