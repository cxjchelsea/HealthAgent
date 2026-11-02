"""Experimental model-mediated tool shortlist. Never an execution authority.

Only narrows Host-admitted read-only candidates. Inconclusive/invalid model
responses use the original complete Host-admitted set.
"""
import json
from pydantic import BaseModel, ConfigDict, Field


class ToolRanking(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools: list[str] = Field(default_factory=list, max_length=3)
    uncertain: bool


async def select_read_tools(provider, model: str, goal: str, allowed: list[dict],
                            *, top_k: int = 3) -> tuple[set[str], str]:
    if not 1 <= top_k <= 3:
        raise ValueError("top_k must be 1..3")
    safe = {t["function"]["name"]: t["function"].get("description", "")
            for t in allowed if t["function"]["name"] in {
                "calculate", "utc_time", "list_tasks", "recall_memory",
                "mcp_reference_lookup"}}
    if not safe:
        return set(), "empty"
    prompt = (
        "Match the CURRENT user's information need to tool capabilities using meaning, "
        "not word overlap. Return JSON with tools (0 to 3 unique exact tool names) "
        "and uncertain boolean. Choose a tool when its observation is needed to "
        "verify or retrieve facts (including implicit needs). If the need is "
        "ambiguous or no safe match can be distinguished, set uncertain=true. "
        "Never infer permissions, execute any tool, or suggest writes. "
        "For an ordinary explanation with no needed observation choose tools=[]."
    )
    try:
        raw = await provider.complete(model=model, messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({
                "request": goal, "candidate_tools": safe}, ensure_ascii=False)},
        ], response_schema=ToolRanking.model_json_schema())
        proposal = ToolRanking.model_validate_json(raw)
        chosen = proposal.tools
        if (proposal.uncertain or len(chosen) > top_k or
                len(chosen) != len(set(chosen)) or not set(chosen) <= safe.keys()):
            return set(safe), "fallback_invalid_or_uncertain"
        # Empty selections are not trusted for read-only tool requests:
        # a mistaken omission must not remove all retrieval capabilities.
        if not chosen:
            return set(safe), "fallback_empty"
        return set(chosen), "selected"
    except (Exception):
        # A tool-ranking model failure must not become an API failure or a
        # wider Host permission. The original Host-admitted set is authoritative.
        return set(safe), "fallback_provider_error"
