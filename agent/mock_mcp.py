"""Safe in-process MCP-compatible mock gateway for evaluation only.

The adapter enforces a fixed tool list, schema, timeout budget via caller policy,
and never connects to arbitrary URLs or executes code.
"""
from pydantic import BaseModel, ConfigDict, Field, ValidationError

class ReferenceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term: str = Field(min_length=1, max_length=80)

CATALOG = {
    "agent": "An assistant that can choose actions and use tools to pursue a goal.",
    "mcp": "Model Context Protocol: a protocol for connecting tools and data sources.",
    "rag": "Retrieval-augmented generation uses retrieved context to ground a response.",
}

class MockMCPClient:
    def call(self, name: str, arguments: dict) -> dict:
        if name != "mcp_reference_lookup":
            return {"ok": False, "error": "mcp_tool_not_allowlisted"}
        try:
            request = ReferenceArgs.model_validate(arguments)
        except (ValidationError, ValueError, TypeError):
            return {"ok": False, "error": "invalid_arguments"}
        term = request.term.casefold().strip()
        if term not in CATALOG:
            return {"ok": False, "error": "reference_not_found"}
        return {"ok": True, "term": term, "definition": CATALOG[term],
                "source": "trusted_mock_catalog_v1"}
