"""One finding shape, shared by every file the server validates.

A cut file and a titles file have entirely different rules, but the agent reads the
result of both the same way — ``{rule, id, message, fix_hint}``, errors before warnings,
all of them at once rather than the first. That agreement lives here so the two rule sets
cannot drift apart in shape while they evolve apart in content.

The rule code carries the severity: ``W`` for a warning that never blocks, anything else
for an error that does. Nothing outside a rule set needs to know which letters it uses.
"""

from __future__ import annotations

from dataclasses import dataclass


def severity_of(rule: str) -> str:
    """``error`` blocks the operation; ``warning`` is reported and never blocks."""
    return "warning" if rule.startswith("W") else "error"


@dataclass(frozen=True)
class Finding:
    """One rule firing on one thing, in the shape the agent reads."""

    rule: str
    id: str | None
    message: str
    fix_hint: str

    @property
    def severity(self) -> str:
        """``error`` blocks the operation; ``warning`` is reported and never blocks."""
        return severity_of(self.rule)

    def as_dict(self) -> dict[str, str | None]:
        return {
            "rule": self.rule,
            "id": self.id,
            "message": self.message,
            "fix_hint": self.fix_hint,
        }


def ordered(findings: list[Finding]) -> list[Finding]:
    """Errors before warnings, rule number ascending, document order within a rule."""

    def key(numbered: tuple[int, Finding]) -> tuple[int, int, int]:
        position, finding = numbered
        return (0 if finding.severity == "error" else 1, int(finding.rule[1:]), position)

    return [finding for _, finding in sorted(enumerate(findings), key=key)]


__all__ = ["Finding", "ordered", "severity_of"]
