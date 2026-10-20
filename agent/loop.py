"""LangGraph S2-A execution loop, bounded and observable."""
import json
import re
import uuid
from pathlib import Path
from typing import Any, TypedDict
from langgraph.graph import END, StateGraph
from agent.decision import NativeToolDecision
from agent.tools import TOOL_SCHEMAS, execute_tool
from agent.skills import exposed_tools, load_instructions, SKILL_VERSION
from agent.planning import PlanLedger
from agent.tools import SCHEMAS
from pydantic import ValidationError

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
    plan: PlanLedger
    last_success: dict | None
    superseded_step_ids: list[int]

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
            pending = [s["id"] for s in state["plan"].snapshot()
                       if s["status"] != "completed"]
            superseded = [step_id for step_id in state["superseded_step_ids"]
                          if any(s["id"] == step_id and s["status"] == "completed"
                                 for s in state["plan"].snapshot())]
            is_partial = bool(pending or superseded)
            final = decision.content
            if superseded:
                final = ("目标更正尚未完成：旧步骤 " + ", ".join(map(str, superseded)) +
                         " 的完成证据仍有效，不能作为新目标的完成证明。\n" + final)
            if is_partial:
                final = ("计划尚未完成（未验证步骤：" + ", ".join(map(str, pending)) +
                         "）。以下是模型当前回复，不代表全部完成。\n" + final)
            return {"iterations": iteration, "final": final,
                    "status": "partial" if is_partial else "completed", "traces": state["traces"] + [
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
            elif name in {"propose_plan", "revise_plan", "complete_plan_step"}:
                try:
                    args = SCHEMAS[name].model_validate(function.get("arguments")).model_dump()
                    ledger = state["plan"]
                    if name == "propose_plan":
                        result = ledger.propose(args["steps"])
                    elif name == "revise_plan":
                        result = ledger.revise(args["expected_revision"] or ledger.revision,
                                               args["pending_steps"], args["reason"],
                                               args["invalidate_step_ids"])
                    else:
                        result = ledger.complete(args["step_id"], args["evidence_tool"],
                                                 state["last_success"])
                except ValidationError as exc:
                    result = {"ok": False, "error": "invalid_arguments",
                              "details": [{"field": ".".join(str(x) for x in issue["loc"]),
                                           "kind": issue["type"]}
                                          for issue in exc.errors()[:6]],
                              "plan_revision": state["plan"].revision}
                except (TypeError, ValueError):
                    result = {"ok": False, "error": "invalid_arguments",
                              "plan_revision": state["plan"].revision}
            else:
                result = execute_tool(name, function.get("arguments"), tasks, self.clock,
                                      task_store=self.task_store, memory_store=self.memory_store,
                                      mcp_client=self.mcp_client)
            if name not in {"propose_plan", "revise_plan", "complete_plan_step"}:
                state["plan"].observe(name, result, function.get("arguments"))
                if result.get("ok"):
                    state["last_success"] = {"name": name, **result}
                else:
                    state["last_success"] = None
            if name == "propose_plan" and result.get("ok"):
                result["guidance"] = "Plan revision is 1; use bound steps with expected_tool/expected_arguments for verified automatic progress."
            if name == "revise_plan" and not result.get("ok"):
                result["guidance"] = "Retry with current revision or omit expected_revision, preserving verified steps."
            observations.append({"role": "tool", "tool_name": name,
                                 "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
            traces.append({"step": state["iterations"], "type": "tool",
                           "name": name, "ok": result["ok"]})
            if state["audit_enabled"]:
                audit.append({"step": state["iterations"], "name": name,
                              "arguments": function.get("arguments"), "observation": result})
        return {"messages": state["messages"] + observations,
                "tasks": tasks, "traces": traces, "audit": audit,
                "plan": state["plan"], "last_success": state["last_success"]}

    async def run(self, goal: str, *, include_evidence: bool = False,
                  history: list[dict] | None = None,
                  plan_checkpoint: dict | None = None) -> dict:
        if not goal.strip() or len(goal) > 12000:
            raise ValueError("Goal must contain 1..12000 characters")
        run_id = str(uuid.uuid4())
        clean_history = []
        for entry in history or []:
            if (not isinstance(entry, dict) or entry.get("role") not in ("user", "assistant")
                    or not isinstance(entry.get("content"), str)):
                raise ValueError("Invalid conversation history")
            clean_history.append({"role": entry["role"], "content": entry["content"]})
        resumed = PlanLedger.restore(plan_checkpoint) if plan_checkpoint else PlanLedger()
        # Conservative guard for explicit arithmetic replacement, not additive goals.
        # No automatic evidence deletion: the model must invalidate via revise_plan.
        superseded_step_ids = []
        correction = re.search(r"不是\\s*([0-9]+\\s*[*+/-]\\s*[0-9]+)\\s*[,，；;。]?\\s*而是", goal)
        if correction:
            old_expression = re.sub(r"\\s+", "", correction.group(1))
            superseded_step_ids = [
                step["id"] for step in resumed.snapshot()
                if step["status"] == "completed"
                and step.get("expected_tool") == "calculate"
                and step.get("expected_arguments") == {"expression": old_expression}]
        selected_tools = exposed_tools(goal, TOOL_SCHEMAS, strategy=self.skill_strategy)
        if resumed.revision:
            names = {t["function"]["name"] for t in selected_tools}
            selected_tools += [t for t in TOOL_SCHEMAS if t["function"]["name"]
                               in {"propose_plan", "revise_plan", "complete_plan_step"}
                               and t["function"]["name"] not in names]
        resume_context = ("\nTrusted checkpoint: revision " + str(resumed.revision) +
                          "; steps: " + json.dumps(resumed.snapshot(), ensure_ascii=False)
                          if resumed.revision else "")
        initial: AgentState = {"messages": [
            {"role": "system", "content": PROMPT + "\nSelected skill instructions (" + SKILL_VERSION + "):\n" + load_instructions(goal, strategy=self.skill_strategy, max_chars=self.skill_budget_chars) + resume_context},
            *clean_history, {"role": "user", "content": goal}],
            "traces": [], "tasks": [], "final": "", "status": "running", "iterations": 0,
            "audit_enabled": include_evidence, "audit": [],
            "allowed_tools": selected_tools,
            "plan": resumed, "last_success": None,
            "superseded_step_ids": superseded_step_ids}
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
                  "prompt_version": PROMPT_VERSION, "steps": outcome["iterations"],
                  "plan": outcome["plan"].snapshot(), "plan_revision": outcome["plan"].revision,
                  "plan_events": list(outcome["plan"].events),
                  "plan_checkpoint": outcome["plan"].checkpoint() if outcome["plan"].revision else None}
        if include_evidence:
            result["audit"] = outcome["audit"]
            result["tasks"] = outcome["tasks"]
        return result
