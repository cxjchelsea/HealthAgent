"""Trusted, opt-in S25-05 ordinary read-tool slice.

This registry is not an authorization source. Host must explicitly grant each
tool on the current turn; model calls and remote MCP metadata cannot grant it.
Existing tools remain on their original guarded paths during this slice.
"""
from dataclasses import dataclass
import re
from typing import Any, Callable, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from agent.tools import CalcArgs, EmptyArgs, MemoryKey, ReferenceArgs, execute_tool
from agent.mcp_stdio_client import StdioMCPClient


class StrictReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextStatsArgs(StrictReadArgs):
    text: str = Field(min_length=1, max_length=4000)


@dataclass(frozen=True)
class ReadToolRuntime:
    """Trusted, invocation-local dependencies; not model-exposed tool input."""
    clock: Any = None
    task_store: Any = None
    memory_store: Any = None
    mcp_client: Any = None
    tasks: list[str] | None = None


@dataclass(frozen=True)
class ReadToolDefinition:
    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    adapter: Callable[..., dict]
    effect: Literal["read"] = "read"
    enabled: bool = True
    requires_runtime: bool = False

    def __post_init__(self):
        if (not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", self.name)
                or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", self.version)
                or not self.description or self.effect != "read"):
            raise ValueError("invalid_tool_definition")
        if (not isinstance(self.input_model, type)
                or not issubclass(self.input_model, BaseModel)
                or not callable(self.adapter)):
            raise ValueError("invalid_tool_definition")

    def provider_schema(self) -> dict:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": schema}}


class TrustedReadRegistry:
    def __init__(self, definitions: tuple[ReadToolDefinition, ...]):
        self._definitions: dict[str, ReadToolDefinition] = {}
        for definition in definitions:
            if definition.name in self._definitions:
                raise ValueError("duplicate_tool_registration")
            self._definitions[definition.name] = definition

    def version_for(self, name: str) -> str | None:
        definition = self._definitions.get(name)
        return definition.version if definition and definition.enabled else None

    def exposed(self, granted_names: tuple[str, ...]) -> list[dict]:
        return [definition.provider_schema()
                for name, definition in self._definitions.items()
                if name in granted_names and definition.enabled]

    def invoke(self, name: str, arguments: object, *,
               granted_names: tuple[str, ...], exposed_names: tuple[str, ...],
               expected_version: str | None = None,
               runtime: ReadToolRuntime | None = None) -> dict:
        definition = self._definitions.get(name)
        if definition is None:
            return {"ok": False, "error": "unknown_tool"}
        if not definition.enabled:
            return {"ok": False, "error": "tool_disabled"}
        if name not in granted_names:
            return {"ok": False, "error": "permission_denied"}
        if name not in exposed_names:
            return {"ok": False, "error": "tool_not_exposed"}
        if not isinstance(arguments, dict):
            return {"ok": False, "error": "invalid_arguments"}
        try:
            parsed = definition.input_model.model_validate(arguments)
        except ValidationError:
            return {"ok": False, "error": "invalid_arguments"}
        # A missing expected version is not permission to use the latest one.
        # Every successful invocation must carry a trusted version binding.
        if expected_version != definition.version:
            return {"ok": False, "error": "tool_version_mismatch"}
        if definition.requires_runtime and runtime is None:
            return {"ok": False, "error": "tool_runtime_unavailable"}
        try:
            result = (definition.adapter(parsed, runtime) if definition.requires_runtime
                      else definition.adapter(parsed))
            if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
                return {"ok": False, "error": "tool_execution_error"}
            # Stable server-origin metadata only; never forward model or MCP claims.
            return {**result, "tool_name": definition.name,
                    "tool_version": definition.version}
        except Exception:
            return {"ok": False, "error": "tool_execution_error"}


def _text_stats(args: TextStatsArgs) -> dict:
    return {"ok": True, "characters": len(args.text),
            "words": len(args.text.split())}


def _legacy_calculate(args: CalcArgs) -> dict:
    """Preserve the established calculator observation and error contract."""
    return execute_tool("calculate", args.model_dump(), [])


def _legacy_utc_time(args: EmptyArgs, runtime: ReadToolRuntime) -> dict:
    return execute_tool("utc_time", args.model_dump(), [],
                        clock=runtime.clock)


def _legacy_list_tasks(args: EmptyArgs, runtime: ReadToolRuntime) -> dict:
    if runtime.task_store is None and runtime.tasks is None:
        return {"ok": False, "error": "tool_runtime_unavailable"}
    return execute_tool("list_tasks", args.model_dump(),
                        runtime.tasks if runtime.tasks is not None else [],
                        task_store=runtime.task_store)


def _legacy_recall_memory(args: MemoryKey, runtime: ReadToolRuntime) -> dict:
    # No global fallback. The Host owns the user-scoped MemoryStore.
    if runtime.memory_store is None:
        return {"ok": False, "error": "memory_unavailable"}
    return execute_tool("recall_memory", args.model_dump(), [],
                        memory_store=runtime.memory_store)


def _trusted_mcp_reference(args: ReferenceArgs, runtime: ReadToolRuntime) -> dict:
    # Only a trusted, fixed local server binding may be invoked in this slice.
    # Remote discovery must not provide a command, tool name or schema.
    if type(runtime.mcp_client) is not StdioMCPClient:
        return {"ok": False, "error": "mcp_untrusted_client"}
    return runtime.mcp_client.call_trusted_reference(args.model_dump())


READ_REGISTRY = TrustedReadRegistry((
    ReadToolDefinition(
        name="mcp_reference_lookup", version="1.0.0",
        description="Look up agent engineering terms in a trusted fixed MCP reference catalog.",
        input_model=ReferenceArgs, adapter=_trusted_mcp_reference, requires_runtime=True),
    ReadToolDefinition(
        name="recall_memory", version="1.0.0",
        description="Look up user-owned long-term memory using an exact key.",
        input_model=MemoryKey, adapter=_legacy_recall_memory, requires_runtime=True),
    ReadToolDefinition(
        name="utc_time", version="1.0.0",
        description="Read the current time in UTC.",
        input_model=EmptyArgs, adapter=_legacy_utc_time, requires_runtime=True),
    ReadToolDefinition(
        name="list_tasks", version="1.0.0",
        description="List persistent tasks in the current conversation.",
        input_model=EmptyArgs, adapter=_legacy_list_tasks, requires_runtime=True),
    ReadToolDefinition(
        name="calculate", version="1.0.0",
        description="Calculate bounded numeric expressions (+,-,*,/,parentheses).",
        input_model=CalcArgs, adapter=_legacy_calculate),
    ReadToolDefinition(
        name="text_stats", version="1.0.0",
        description="Count Unicode characters and whitespace-separated words in supplied text.",
        input_model=TextStatsArgs, adapter=_text_stats),
))


def approved_read_grants(*, host_disposition: str, host_route: str,
                         host_mode: str, feature_enabled: bool,
                         calculate_registry_enabled: bool = False,
                         utc_time_registry_enabled: bool = False,
                         list_tasks_registry_enabled: bool = False,
                         recall_memory_registry_enabled: bool = False,
                         mcp_reference_registry_enabled: bool = False) -> tuple[str, ...]:
    """Explicit server-side Host policy; descriptions never grant permission."""
    if not (host_disposition == "allow" and host_route == "ordinary_tool"
            and host_mode == "read_only"):
        return ()
    granted = []
    if feature_enabled:
        granted.append("text_stats")
    if calculate_registry_enabled:
        granted.append("calculate")
    if utc_time_registry_enabled:
        granted.append("utc_time")
    if list_tasks_registry_enabled:
        granted.append("list_tasks")
    if recall_memory_registry_enabled:
        granted.append("recall_memory")
    if mcp_reference_registry_enabled:
        granted.append("mcp_reference_lookup")
    return tuple(granted)
