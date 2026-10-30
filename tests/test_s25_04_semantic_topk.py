"""Independent model shortlist cannot widen Host authority and safely falls back."""
import asyncio
import json

from agent.decision import Decision
from agent.loop import AgentLoop
from agent.semantic_topk import select_read_tools
from agent.tools import TOOL_SCHEMAS


class Provider:
    def __init__(self, payload=None, fail=False):
        self.payload = payload
        self.fail = fail

    async def complete(self, **kwargs):
        if self.fail:
            raise RuntimeError("provider down")
        return json.dumps(self.payload)


def allowed():
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] in {
        "calculate", "utc_time", "list_tasks"}]


def test_shortlist_and_invalid_fallback():
    tools = allowed()
    expected = {"calculate", "utc_time", "list_tasks"}
    assert asyncio.run(select_read_tools(
        Provider({"tools": ["calculate"], "uncertain": False}),
        "test", "How much is seven of ten", tools)) == ({"calculate"}, "selected")
    assert asyncio.run(select_read_tools(
        Provider({"tools": ["save_memory"], "uncertain": False}),
        "test", "Please save", tools))[0] == expected
    assert asyncio.run(select_read_tools(
        Provider({"tools": [], "uncertain": True}),
        "test", "Ambiguous", tools))[0] == expected
    assert asyncio.run(select_read_tools(
        Provider(fail=True), "test", "anything", tools))[0] == expected


class Probe:
    def __init__(self):
        self.seen = []

    async def decide(self, messages, tools):
        self.seen.append({t["function"]["name"] for t in tools})
        return Decision("Done", [])


def test_host_readonly_subset_and_respond_only():
    probe = Probe()
    asyncio.run(AgentLoop(
        probe, skill_strategy="semantic_topk",
        candidate_selector=Provider({"tools": ["calculate"], "uncertain": False}),
        selector_model="test").run("Please verify 7/10", execution_mode="read_only"))
    assert probe.seen == [{"request_clarification", "calculate"}]
    probe = Probe()
    asyncio.run(AgentLoop(
        probe, skill_strategy="semantic_topk",
        candidate_selector=Provider({"tools": ["calculate"], "uncertain": False}),
        selector_model="test").run("Explain the idea", execution_mode="respond_only"))
    assert probe.seen == [set()]


def test_topk_never_admits_untrusted_write():
    probe = Probe()
    asyncio.run(AgentLoop(
        probe, skill_strategy="semantic_topk",
        candidate_selector=Provider({"tools": ["create_task"], "uncertain": False}),
        selector_model="test").run("Create a task", execution_mode="read_only"))
    assert "create_task" not in probe.seen[0]
    assert "save_memory" not in probe.seen[0]


def test_topk_observability_exposes_only_names_and_bounded_metrics():
    probe = Probe()
    result = asyncio.run(AgentLoop(
        probe, skill_strategy="semantic_topk",
        candidate_selector=Provider({"tools": ["calculate"], "uncertain": False}),
        selector_model="test").run("Verify seven of ten", execution_mode="read_only"))
    selected = [event for event in result["trace"] if event["type"] == "topk_selection"]
    assert len(selected) == 1
    trace = selected[0]
    assert trace["reason"] == "selected"
    assert trace["selected_read_tools"] == ["calculate"]
    assert trace["candidate_count"] >= 1
    assert trace["selector_duration_ms"] >= 0
    assert trace["selector_model_calls"] == 1
    assert trace["exposed_tools"] == ["request_clarification", "calculate"]
    assert "Verify seven of ten" not in str(trace)


def test_topk_fallback_reports_reason_and_never_loses_read_candidates():
    probe = Probe()
    result = asyncio.run(AgentLoop(
        probe, skill_strategy="semantic_topk",
        candidate_selector=Provider(fail=True), selector_model="test").run(
            "Need a checked result", execution_mode="read_only"))
    trace = next(event for event in result["trace"]
                 if event["type"] == "topk_selection")
    assert trace["reason"] == "fallback_provider_error"
    assert "calculate" in trace["exposed_tools"]
    assert "create_task" not in trace["exposed_tools"]
