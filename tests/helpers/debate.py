"""Independent debate output contracts shared by both test tiers."""

HEADINGS = {
    "propose": ["## Proposal", "## Why this works", "## Risks and unknowns"],
    "critique": ["## Critique", "## Revised position"],
    "judge": ["## Answer", "## Why this over the alternatives", "## Dissent", "## Critique effect"],
}


def headings(phase: str, role: str) -> list[str]:
    """Return the mandatory headings from the design, independently of production constants."""
    result = HEADINGS[phase].copy()
    if role == "researcher" and phase in ("propose", "judge"):
        result.append("## Sources")
    if role == "architect" and phase == "judge":
        result.append("## Claims to verify against the code")
    return result


def answer(phase: str, role: str = "architect") -> str:
    """Return a complete Unicode answer, including all four possible critic targets."""
    return (
        "\n\n".join(
            heading
            + "\n"
            + (
                "\n".join(f"### Seat {label}\nEvidence café" for label in "ABCD")
                if heading == "## Critique"
                else "Evidence café"
            )
            for heading in headings(phase, role)
        )
        + "\n"
    )
