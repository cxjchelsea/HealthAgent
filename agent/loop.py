"""LangGraph S2-A execution loop, bounded and observable."""
import json
import uuid
from pathlib import Path
from typing import Any, TypedDict
from langgraph.graph import END, StateGraph
from agent.decision import NativeToolDecision
from agent.tools import TOOL_SCHEMAS, execute_tool
from agent.skills import exposed_tools, load_instructions, SKILL_VERSION

PROMPT_VERSION = "s2a-core-v1"
PROMPT = (Path(__file__).parent / "prompts" / "s2a_core_v1.md").read_text(encoding="utf-8")

class AgentState(TypedDict):
    messages: list[dict]
    traces: list[dict]
    tasks: list[str]
    final: str
    status: str
    iterations: int
    audit_enabled: bool
    audit: list[dict]
    allowed_tools: list[dict]

class AgentLoop:
    def __init__(self, decider: Any, max_steps: int = 6, clock=None, task_store=None, memory_store=None, mcp_client=None, skill_strategy='dynamic', skill_budget_chars=600):
        if max_steps < 1 or max_steps > 20:
            raise ValueError("max_steps must be 1..20")
        self.decider = decider
        self.max_steps = max_steps
        self.clock = clock
        self.task_store = task_store
        self.memory_store = memory_store
        self.mcp_client = mcp_client
        self.skill_strategy = skill_strategy
        self.skill_budget_chars = skill_budget_chars
        flow = StateGraph(AgentState)
        flow.add_node("decide", self._decide)
        flow.add_node("tools", self._tools)
        flow.set_entry_point("decide")
        flow.add_conditional_edges("decide", self._next, {"tools": "tools", "done": END})
        flow.add_edge("tools", "decide")
        self.graph = flow.compile()

    async def _decide(self, state: AgentState) -> dict:
        decision = await self.decider.decide(state["messages"], state["allowed_tools"])
        iteration = state["iterations"] + 1
        if not decision.tool_calls:
            return {"iterations": iteration, "final": decision.content,
                    "status": "completed", "traces": state["traces"] + [
                        {"step": iteration, "type": "final", "chars": len(decision.content)}]}
        if iteration >= self.max_steps:
            return {"iterations": iteration, "final": "Agent reached its decision step limit.",
                    "status": "step_limit", "traces": state["traces"] + [
                        {"step": iteration, "type": "step_limit"}]}
        if len(decision.tool_calls) > 4:
            return {"iterations": iteration, "final": "Model requested too many tools.",
                    "status": "tool_limit", "traces": state["traces"] + [
                        {"step": iteration, "type": "tool_limit"}]}
        # OpenAI and Ollama support tool_call syntax but differ on tool result metadata.
        assistant_calls = [{"id": c["id"], "type": "function", "function": {
            "name": c["name"], "arguments": c["arguments"]}} for c in decision.tool_calls]
        return {"iterations": iteration, "messages": state["messages"] + [
            {"role": "assistant", "content": decision.content, "tool_calls": assistant_calls}],
            "traces": state["traces"] + [{"step": iteration, "type": "decision",
                                         "tools": [c["name"] for c in decision.tool_calls]}]}

    def _next(self, state: AgentState) -> str:
        return "done" if state["status"] != "running" else "tools"

    async def _tools(self, state: AgentState) -> dict:
        last = state["messages"][-1]
        tasks = list(state["tasks"])
        observations = []
        traces = list(state["traces"])
        audit = list(state["audit"])
        # Limit fanout, never allow an arbitrary number of model tool calls.
        for index, call in enumerate(last.get("tool_calls", [])[:4]):
            function = call["function"]
            name = function.get("name")
            if name not in {tool["function"]["name"] for tool in state["allowed_tools"]}:
                result = {"ok": False, "error": "tool_not_exposed"}
            else:
                result = execute_tool(name, function.get("arguments"), tasks, self.clock,
                                      task_store=self.task_store, memory_store=self.memory_store,
                                      mcp_client=self.mcp_client)
            observations.append({"role": "tool", "tool_name": name,
                                 "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
            traces.append({"step": state["iterations"], "type": "tool",
                           "name": name, "ok": result["ok"]})
            if state["audit_enabled"]:
                audit.append({"step": state["iterations"], "name": name,
                              "arguments": function.get("arguments"), "observation": result})
        return {"messages": state["messages"] + observations,
                "tasks": tasks, "traces": traces, "audit": audit}

    async def run(self, goal: str, *, include_evidence: bool = False,
                  history: list[dict] | None = None) -> dict:
        if not goal.strip() or len(goal) > 12000:
            raise ValueError("Goal must contain 1..12000 characters")
        run_id = str(uuid.uuid4())
        clean_history = []
        for entry in history or []:
            if (not isinstance(entry, dict) or entry.get("role") not in ("user", "assistant")
                    or not isinstance(entry.get("content"), str)):
                raise ValueError("Invalid conversation history")
            clean_history.append({"role": entry["role"], "content": entry["content"]})
        initial: AgentState = {"messages": [
            {"role": "system", "content": PROMPT + "\nSelected skill instructions (" + SKILL_VERSION + "):\n" + load_instructions(goal, strategy=self.skill_strategy, max_chars=self.skill_budget_chars)},
            *clean_history, {"role": "user", "content": goal}],
            "traces": [], "tasks": [], "final": "", "status": "running", "iterations": 0,
            "audit_enabled": include_evidence, "audit": [],
            "allowed_tools": exposed_tools(goal, TOOL_SCHEMAS, strategy=self.skill_strategy)}
        try:
            outcome = await self.graph.ainvoke(initial, config={"recursion_limit": 2 * self.max_steps + 4})
        except Exception as exc:
            from agent.providers import ModelUnavailable
            if isinstance(exc, ModelUnavailable):
                return {"run_id": run_id, "status": "provider_error", "answer": "",
                        "trace": [], "prompt_version": PROMPT_VERSION}
            raise
        result = {"run_id": run_id, "status": outcome["status"],
                  "answer": outcome["final"], "trace": outcome["traces"],
                  "prompt_version": PROMPT_VERSION, "steps": outcome["iterations"]}
        if include_evidence:
            result["audit"] = outcome["audit"]
            result["tasks"] = outcome["tasks"]
        return result
