"""LangGraph S2-A execution loop, bounded and observable."""
import json
import uuid
from pathlib import Path
from typing import Any, TypedDict
from langgraph.graph import END, StateGraph
from agent.decision import NativeToolDecision
from agent.tools import TOOL_SCHEMAS, execute_tool

PROMPT_VERSION = "s2a-core-v1"
PROMPT = (Path(__file__).parent / "prompts" / "s2a_core_v1.md").read_text(encoding="utf-8")

class AgentState(TypedDict):
    messages: list[dict]
    traces: list[dict]
    tasks: list[str]
    final: str
    status: str
    iterations: int

class AgentLoop:
    def __init__(self, decider: Any, max_steps: int = 6, clock=None):
        if max_steps < 1 or max_steps > 20:
            raise ValueError("max_steps must be 1..20")
        self.decider = decider
        self.max_steps = max_steps
        self.clock = clock
        flow = StateGraph(AgentState)
        flow.add_node("decide", self._decide)
        flow.add_node("tools", self._tools)
        flow.set_entry_point("decide")
        flow.add_conditional_edges("decide", self._next, {"tools": "tools", "done": END})
        flow.add_edge("tools", "decide")
        self.graph = flow.compile()

    async def _decide(self, state: AgentState) -> dict:
        decision = await self.decider.decide(state["messages"], TOOL_SCHEMAS)
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
        # Limit fanout, never allow an arbitrary number of model tool calls.
        for index, call in enumerate(last.get("tool_calls", [])[:4]):
            function = call["function"]
            name = function.get("name")
            result = execute_tool(name, function.get("arguments"), tasks, self.clock)
            observations.append({"role": "tool", "tool_name": name,
                                 "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
            traces.append({"step": state["iterations"], "type": "tool",
                           "name": name, "ok": result["ok"]})
        return {"messages": state["messages"] + observations,
                "tasks": tasks, "traces": traces}

    async def run(self, goal: str) -> dict:
        if not goal.strip() or len(goal) > 12000:
            raise ValueError("Goal must contain 1..12000 characters")
        run_id = str(uuid.uuid4())
        initial: AgentState = {"messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": goal}],
            "traces": [], "tasks": [], "final": "", "status": "running", "iterations": 0}
        try:
            outcome = await self.graph.ainvoke(initial, config={"recursion_limit": 2 * self.max_steps + 4})
        except Exception as exc:
            from agent.providers import ModelUnavailable
            if isinstance(exc, ModelUnavailable):
                return {"run_id": run_id, "status": "provider_error", "answer": "",
                        "trace": [], "prompt_version": PROMPT_VERSION}
            raise
        return {"run_id": run_id, "status": outcome["status"],
                "answer": outcome["final"], "trace": outcome["traces"],
                "prompt_version": PROMPT_VERSION, "steps": outcome["iterations"]}
