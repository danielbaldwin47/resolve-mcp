"""One finding shape, shared by every file the server validates.

A cut file and a titles file have entirely different rules, but the agent reads the
result of both the same way — ``{rule, id, message, fix_hint}``, errors before warnings,
all of them at once rather than the first. That agreement lives here so the two rule sets
cannot drift apart in shape while they evolve apart in content.

The rule code carries the severity: ``W`` for a warning that never blocks, anything else
for an error that does. Nothing outside a rule set needs to know which letters it uses.

Packaging lives here too. ``report`` is the one place a list of findings becomes the
``{errors, warnings}`` pair a reply carries, and ``refuse`` is the one place errors in
that list become a refusal — so a rule set that grows a severity, or a report that grows
a key, changes in one file rather than at the six sites that used to build the dict by
hand.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import ResolveMcpError


class Refusal(Protocol):
    """The error a rule set raises when its own rules blocked.

    A protocol rather than a bare callable so the two keywords ``refuse`` passes stay
    checked: an error class that stopped taking a ``detail`` would be caught here rather
    than at the raise.
    """

    def __call__(self, *, cause: str, detail: dict[str, Any]) -> ResolveMcpError: ...


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


def errors_in(findings: Iterable[Finding]) -> list[Finding]:
    """The findings that block. One definition, so a pre-flight and a report cannot differ."""
    return [finding for finding in findings if finding.severity == "error"]


def warnings_in(findings: Iterable[Finding]) -> list[Finding]:
    """The findings that are reported and never block."""
    return [finding for finding in findings if finding.severity == "warning"]


def report(findings: Iterable[Finding]) -> dict[str, list[dict[str, str | None]]]:
    """The ``{errors, warnings}`` pair, split by severity, in the order they were found.

    Both keys are always present, including on a reply that could only ever carry
    warnings: the agent reads one shape whether it is holding a refusal or a success, and
    an empty ``errors`` is the statement that the rules found none.
    """
    listed = list(findings)
    return {
        "errors": [finding.as_dict() for finding in errors_in(listed)],
        "warnings": [finding.as_dict() for finding in warnings_in(listed)],
    }


def refuse(
    findings: Iterable[Finding],
    *,
    error: Refusal,
    what: str,
    consequence: str,
    detail: dict[str, Any],
) -> None:
    """Raise ``error`` if anything in ``findings`` is an error; otherwise return.

    Every validating operation opens the same way — run the rules, and if any of them
    blocked, refuse before touching the project — so the sentence the agent reads is
    built once here: *The <what> has N error(s), so <consequence>.* ``detail`` carries
    the provenance the caller owns (the file, its hash) and the packaged findings are
    merged onto it, so a refusal reports every rule that fired, not just the count.
    """
    packaged = report(findings)
    if not packaged["errors"]:
        return
    raise error(
        cause=f"The {what} has {len(packaged['errors'])} error(s), so {consequence}.",
        detail={**detail, **packaged},
    )


__all__ = [
    "Finding",
    "Refusal",
    "errors_in",
    "ordered",
    "refuse",
    "report",
    "severity_of",
    "warnings_in",
]
