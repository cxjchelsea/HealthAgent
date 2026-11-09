"""S25-05 minimal, read-only ToolInvoker boundary.

This is an execution adapter, not an authorization or dispatch authority.
The API Host remains responsible for supplying granted tool names. Saved-plan,
M4, Pending and write execution are intentionally outside this module.
"""
from dataclasses import dataclass

from agent.tool_registry import READ_REGISTRY, ReadToolRuntime, TrustedReadRegistry


@dataclass(frozen=True)
class ReadInvocation:
    name: str
    arguments: object
    granted_names: tuple[str, ...]
    exposed_names: tuple[str, ...]
    expected_version: str | None


class OrdinaryReadInvoker:
    def __init__(self, registry: TrustedReadRegistry = READ_REGISTRY):
        self._registry = registry

    def invoke(self, invocation: ReadInvocation, *, runtime: ReadToolRuntime | None = None) -> dict:
        """Recheck grant, exposure, schema and version at the call boundary."""
        return self._registry.invoke(
            invocation.name, invocation.arguments,
            granted_names=invocation.granted_names,
            exposed_names=invocation.exposed_names,
            expected_version=invocation.expected_version,
            runtime=runtime,
        )
