"""Typed host admission for clarification and read-only continuation.

Model reviews are untrusted proposals. This module is the sole authority for
turning them into a route; it never grants write permission or tool results.
"""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ClarificationRoute:
    kind: Literal["clarify", "unsupported", "read_only"]
    unsupported_name: str | None = None

    @property
    def skip_persistence(self) -> bool:
        return self.kind == "read_only"


def admit_clarification(
    *,
    missing_argument_action: str | None,
    unsupported_name: str | None,
    complete_read_only_request: bool,
) -> ClarificationRoute:
    """Fail closed on pending tool arguments; no review grants write authority."""
    if unsupported_name:
        return ClarificationRoute("unsupported", unsupported_name)
    if missing_argument_action is None and complete_read_only_request:
        return ClarificationRoute("read_only")
    return ClarificationRoute("clarify")
