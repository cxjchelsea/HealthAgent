"""Trusted, opt-in S25-05 ordinary read-tool slice.

This registry is not an authorization source. Host must explicitly grant each
tool on the current turn; model calls and remote MCP metadata cannot grant it.
Existing tools remain on their original guarded paths during this slice.
"""
from dataclasses import dataclass
import re
from typing import Callable, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class StrictReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextStatsArgs(StrictReadArgs):
    text: str = Field(min_length=1, max_length=4000)


@dataclass(frozen=True)
class ReadToolDefinition:
    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    adapter: Callable[[BaseModel], dict]
    effect: Literal["read"] = "read"
    enabled: bool = True

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
               expected_version: str | None = None) -> dict:
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
        try:
            result = definition.adapter(parsed)
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


READ_REGISTRY = TrustedReadRegistry((
    ReadToolDefinition(
        name="text_stats", version="1.0.0",
        description="Count Unicode characters and whitespace-separated words in supplied text.",
        input_model=TextStatsArgs, adapter=_text_stats),
))


def approved_read_grants(*, host_disposition: str, host_route: str,
                         host_mode: str, feature_enabled: bool) -> tuple[str, ...]:
    """Explicit server-side Host policy; descriptions never grant permission."""
    if (feature_enabled and host_disposition == "allow"
            and host_route == "ordinary_tool" and host_mode == "read_only"):
        return ("text_stats",)
    return ()
