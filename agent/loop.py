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
from agent.arithmetic_recovery import arithmetic_equivalent
from agent.tools import SCHEMAS
from agent.argument_provenance import REQUIRED_USER_VALUES, user_supplied_value, clarification_question
from pydantic import ValidationError

PROMPT_VERSION = "s2a-core-v1"
PROMPT = (Path(__file__).parent / "prompts" / "s2a_core_v1.md").read_text(encoding="utf-8")

class AgentState(TypedDict):
    messages: list[dict]
    traces: list[dict]
    tasks: list[str]
    final: str
    status: str
    termination_reason: str | None
    pending_action: str | None
    rejected_action_keys: list[str]
    unresolved_tool_failures: list[str]
    unresolved_calculation_errors: dict[str, str]
    iterations: int
    audit_enabled: bool
    audit: list[dict]
    allowed_tools: list[dict]
    plan: PlanLedger
    last_success: dict | None
    superseded_step_ids: list[int]
    superseded_expression: str | None
    memory_recall_required: bool
    memory_recall_observed: bool
    memory_recall_retry: bool
    recall_retry_pending: bool
    stop_after_first_verified_step: bool
    last_tool_decision: str | None
    repeated_tool_decisions: int
    structured_mode: bool
    failed_plan_attempts: int
    user_request: str

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
        flow.add_conditional_edges("decide", self._next, {"tools": "tools", "done": END, "retry": "decide"})
        flow.add_conditional_edges("tools", self._after_tools, {"done": END, "decide": "decide"})
        self.graph = flow.compile()

    async def _decide(self, state: AgentState) -> dict:
        decision = await self.decider.decide(state["messages"], state["allowed_tools"])
        iteration = state["iterations"] + 1
        if not decision.tool_calls:
            if state["memory_recall_required"] and not state["memory_recall_observed"]:
                if not state["memory_recall_retry"] and iteration < self.max_steps:
                    return {"iterations": iteration, "memory_recall_retry": True,
                            "recall_retry_pending": True,
                            "messages": state["messages"] + [{
                                "role": "system", "content":
                                "The user asks about stored long-term memory, but no "
                                "memory lookup occurred. Call recall_memory with a real "
                                "stored key. Do not assert the memory is absent without "
                                "a successful lookup. If the key is unknown, say it "
                                "could not be verified."}],
                            "traces": state["traces"] + [{
                                "step": iteration, "type": "memory_recall_retry"}]}
                return {"iterations": iteration,
                        "final": "尚未核实长期记忆：模型未调用 recall_memory，"
                                 "不能据此断言已保存的信息不存在。请在记忆管理面板核对 Key。",
                        "status": "partial", "termination_reason": "memory_recall_unverified",
                        "traces": state["traces"] + [{
                            "step": iteration, "type": "memory_recall_unverified"}]}
            pending = [s["id"] for s in state["plan"].snapshot()
                       if s["status"] != "completed"]
            superseded = [step_id for step_id in state["superseded_step_ids"]
                          if any(s["id"] == step_id and s["status"] == "completed"
                                 and s.get("expected_tool") == "calculate"
                                 and s.get("expected_arguments") == {"expression": state["superseded_expression"]}
                                 for s in state["plan"].snapshot())]
            unresolved = state["unresolved_tool_failures"]
            verified_replan = (state["plan"].revision > 1
                               and bool(state["plan"].snapshot())
                               and all(step["status"] == "completed" for step in state["plan"].snapshot())
                               and any(event.get("event") == "plan_revised" for event in state["plan"].events))
            if verified_replan:
                unresolved = []
            is_partial = bool(pending or superseded or unresolved)
            final = decision.content
            if superseded:
                final = ("目标更正尚未完成：旧步骤 " + ", ".join(map(str, superseded)) +
                         " 的完成证据仍有效，不能作为新目标的完成证明。\n" + final)
            if unresolved:
                final = ("工具仍有 " + str(len(unresolved)) + " 项未解决的失败；模型回复不是执行成功证明。\n" + final)
            if pending:
                final = ("计划尚未完成（未验证步骤：" + ", ".join(map(str, pending)) +
                         "）。以下是模型当前回复，不代表全部完成。\n" + final)
            return {"iterations": iteration, "final": final,
                    "status": "partial" if is_partial else "completed",
                    "termination_reason": ("superseded_evidence" if superseded else
                                           "pending_plan_steps" if pending else
                                           "unresolved_tool_failure" if unresolved else "model_final"),
                    "traces": state["traces"] + [
                        {"step": iteration, "type": "final", "chars": len(decision.content)}]}
        # Stop a non-progressing model that repeats the same tool invocation.
        # This is a *partial* result, never evidence of task completion.
        signature = (json.dumps([{"name": call.get("name"),
                                "arguments": call.get("arguments")}
                               for call in decision.tool_calls],
                               sort_keys=True, ensure_ascii=False, default=str)
                     if decision.tool_calls else None)
        repeats = (state["repeated_tool_decisions"] + 1
                   if signature and signature == state["last_tool_decision"] else 0)
        if state.get("structured_mode", False) and repeats >= 2:
            return {"iterations": iteration, "status": "partial",
                    "termination_reason": "repeated_tool_decision",
                    "final": "连续多次收到相同工具请求，已停止重复执行；任务尚未验证完成。",
                    "traces": state["traces"] + [
                        {"step": iteration, "type": "repeat_tool_stop"}],
                    "last_tool_decision": signature,
                    "repeated_tool_decisions": repeats}
        if iteration >= self.max_steps:
            return {"iterations": iteration, "final": "Agent reached its decision step limit.",
                    "status": "step_limit", "termination_reason": "decision_budget_exhausted",
                    "traces": state["traces"] + [
                        {"step": iteration, "type": "step_limit"}]}
        if len(decision.tool_calls) > 4:
            return {"iterations": iteration, "final": "Model requested too many tools.",
                    "status": "tool_limit", "termination_reason": "tool_fanout_exceeded",
                    "traces": state["traces"] + [
                        {"step": iteration, "type": "tool_limit"}]}
        # OpenAI and Ollama support tool_call syntax but differ on tool result metadata.
        assistant_calls = [{"id": c["id"], "type": "function", "function": {
            "name": c["name"], "arguments": c["arguments"]}} for c in decision.tool_calls]
        return {"iterations": iteration, "messages": state["messages"] + [
            {"role": "assistant", "content": decision.content, "tool_calls": assistant_calls}],
            "recall_retry_pending": False,
            "last_tool_decision": signature, "repeated_tool_decisions": repeats,
            "traces": state["traces"] + [{"step": iteration, "type": "decision",
                                         "tools": [c["name"] for c in decision.tool_calls]}]}

    def _next(self, state: AgentState) -> str:
        if state["status"] != "running":
            return "done"
        return "retry" if state["recall_retry_pending"] else "tools"

    def _after_tools(self, state: AgentState) -> str:
        return "done" if state["status"] != "running" else "decide"

    async def _tools(self, state: AgentState) -> dict:
        last = state["messages"][-1]
        tasks = list(state["tasks"])
        observations = []
        traces = list(state["traces"])
        audit = list(state["audit"])
        rejected_keys = list(state["rejected_action_keys"])
        unresolved_failures = list(state["unresolved_tool_failures"])
        calculation_errors = dict(state["unresolved_calculation_errors"])
        calls = last.get("tool_calls", [])[:4]
        if any(c["function"].get("name") == "request_clarification" for c in calls):
            allowed_names = {tool["function"]["name"] for tool in state["allowed_tools"]}
            if "request_clarification" not in allowed_names:
                issue = {"ok": False, "error": "tool_not_exposed"}
            elif len(calls) != 1:
                issue = {"ok": False, "error": "control_action_must_be_alone"}
            else:
                try:
                    args = SCHEMAS["request_clarification"].model_validate(calls[0]["function"].get("arguments"))
                except ValidationError:
                    issue = {"ok": False, "error": "invalid_arguments"}
                else:
                    return {"status": "partial", "termination_reason": "needs_input",
                            "final": args.question, "traces": traces + [
                                {"step": state["iterations"], "type": "clarification_requested"}]}
            observations.append({"role": "tool", "tool_name": "request_clarification",
                                 "tool_call_id": calls[0]["id"], "content": json.dumps(issue)})
            return {"messages": state["messages"] + observations, "traces": traces + [
                {"step": state["iterations"], "type": "tool", "name": "request_clarification", "ok": False}]}
        # Preflight the whole batch before any persistent side effect. A
        # synthetic model argument is not evidence the user supplied it.
        if state["structured_mode"]:
            for call in calls:
                fn = call["function"]
                tool = fn.get("name")
                if (tool in REQUIRED_USER_VALUES
                        and tool in {t["function"]["name"] for t in state["allowed_tools"]}
                        and not user_supplied_value(
                            tool, fn.get("arguments"), state["user_request"])):
                    return {"status": "partial", "termination_reason": "needs_input",
                            "pending_action": tool,
                            "final": clarification_question(tool),
                            "traces": traces + [{"step": state["iterations"],
                                                 "type": "argument_provenance_missing",
                                                 "name": tool}]}
        # Limit fanout, never allow an arbitrary number of model tool calls.
        for index, call in enumerate(last.get("tool_calls", [])[:4]):
            function = call["function"]
            name = function.get("name")
            arguments = function.get("arguments")
            action_key = json.dumps([name, arguments], sort_keys=True, ensure_ascii=False, default=str)
            if (action_key in rejected_keys and name not in
                    {"propose_plan", "revise_plan", "complete_plan_step"}):
                result = {"ok": False, "error": "repeated_nonretryable_action"}
            elif name not in {tool["function"]["name"] for tool in state["allowed_tools"]}:
                result = {"ok": False, "error": "tool_not_exposed"}
            elif name in {"propose_plan", "revise_plan", "complete_plan_step"}:
                try:
                    args = SCHEMAS[name].model_validate(function.get("arguments")).model_dump()
                    ledger = state["plan"]
                    if name == "propose_plan":
                        result = ledger.propose(args["steps"])
                    elif name == "revise_plan":
                        required = set(state["superseded_step_ids"])
                        supplied = set(args["invalidate_step_ids"])
                        if required and not required.issubset(supplied):
                            result = {"ok": False, "error": "missing_required_invalidation",
                                      "required_step_ids": sorted(required - supplied),
                                      "plan_revision": ledger.revision}
                        else:
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
            if (not result["ok"] and result.get("error") in
                    {"invalid_arguments", "division_by_zero", "unsupported_expression",
                     "calculation_limit_exceeded", "reference_not_found", "tool_not_exposed",
                     "mcp_tool_not_allowlisted", "unknown_tool"}
                    and name not in {"propose_plan", "revise_plan", "complete_plan_step"}
                    and action_key not in rejected_keys):
                rejected_keys.append(action_key)
            if name not in {"propose_plan", "revise_plan", "complete_plan_step"}:
                if result["ok"]:
                    unresolved_failures = [a for a in unresolved_failures if a != action_key]
                    calculation_errors.pop(action_key, None)
                    if name == "calculate" and isinstance(arguments, dict):
                        recovered = []
                        for old_key in unresolved_failures:
                            if calculation_errors.get(old_key) != "unsupported_expression":
                                continue
                            try:
                                old_name, old_args = json.loads(old_key)
                            except (ValueError, TypeError):
                                continue
                            if (old_name == "calculate" and isinstance(old_args, dict)
                                    and arithmetic_equivalent(
                                        old_args.get("expression"),
                                        arguments.get("expression"),
                                        result.get("value"))):
                                recovered.append(old_key)
                        if recovered:
                            unresolved_failures = [a for a in unresolved_failures if a not in recovered]
                            for key in recovered:
                                calculation_errors.pop(key, None)
                            traces.append({"step": state["iterations"],
                                           "type": "arithmetic_failure_recovered",
                                           "recovered_count": len(recovered)})
                elif action_key not in unresolved_failures:
                    unresolved_failures.append(action_key)
                    if name == "calculate":
                        calculation_errors[action_key] = str(result.get("error", ""))
            if name == "recall_memory":
                state["memory_recall_observed"] = True
            if name not in {"propose_plan", "revise_plan", "complete_plan_step"}:
                state["plan"].observe(name, result, function.get("arguments"))
                if result.get("ok"):
                    state["last_success"] = {"name": name, **result}
                else:
                    state["last_success"] = None
            if name == "propose_plan" and result.get("ok"):
                result["guidance"] = "Plan revision is 1; use bound steps with expected_tool/expected_arguments for verified automatic progress."
            if name == "revise_plan" and not result.get("ok"):
                if result.get("error") == "missing_required_invalidation":
                    result["guidance"] = ("User replaced an already completed goal. "
                                          "Retry revise_plan with required_step_ids included "
                                          "in invalidate_step_ids; preserve other completed steps.")
                else:
                    result["guidance"] = "Retry with current revision or omit expected_revision, preserving verified steps."
            if name in {"propose_plan", "revise_plan", "complete_plan_step"}:
                state["failed_plan_attempts"] = (state["failed_plan_attempts"] + 1
                    if not result.get("ok") else 0)
            observations.append({"role": "tool", "tool_name": name,
                                 "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
            traces.append({"step": state["iterations"], "type": "tool",
                           "name": name, "ok": result["ok"],
                           "error_code": result.get("error") if not result["ok"] else None})
            if state["audit_enabled"]:
                audit.append({"step": state["iterations"], "name": name,
                              "arguments": function.get("arguments"), "observation": result})
        update = {"messages": state["messages"] + observations,
                  "tasks": tasks, "traces": traces, "audit": audit,
                  "plan": state["plan"], "last_success": state["last_success"],
                  "rejected_action_keys": rejected_keys,
                  "unresolved_tool_failures": unresolved_failures,
                  "unresolved_calculation_errors": calculation_errors,
                  "memory_recall_observed": state["memory_recall_observed"]}
        # A rejected plan call is not progress. Permit one correction, then stop
        # safely instead of consuming the whole decision budget.
        if state.get("structured_mode", False) and state["failed_plan_attempts"] >= 2:
            update.update({"status": "partial",
                           "final": "计划工具连续失败，已停止重试；未生成有效执行计划。",
                           "traces": traces + [{"step": state["iterations"],
                                                 "type": "plan_tool_failure_stop"}]})
            return update
        # Stop only for an explicitly limited first step with verified evidence.
        steps = state["plan"].snapshot()
        if (state["stop_after_first_verified_step"] and len(steps) >= 2
                and steps[0]["status"] == "completed"
                and any(step["status"] == "pending" for step in steps[1:])):
            remaining = [step["id"] for step in steps if step["status"] == "pending"]
            update.update({
                "status": "partial",
                "termination_reason": "bounded_partial_execution",
                "final": ("已验证完成计划的第一步；其余步骤 "
                          + "、".join(str(i) for i in remaining)
                          + " 尚未完成，已保留在计划中。"),
                "traces": traces + [{"step": state["iterations"],
                                     "type": "partial_first_step_verified"}]})
        return update

    async def run(self, goal: str, *, include_evidence: bool = False,
                  history: list[dict] | None = None,
                  plan_checkpoint: dict | None = None,
                  understanding_result: dict | None = None,
                  execution_mode: str | None = None,
                  plan_control: str | None = None) -> dict:
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
        superseded_expression = None
        correction = (re.search(r"不是\s*([0-9]+\s*[*+/-]\s*[0-9]+)\s*[,，；;。]?\s*而是", goal)
                      if understanding_result is None else None)
        if correction:
            old_expression = re.sub(r"\s+", "", correction.group(1))
            superseded_expression = old_expression
            superseded_step_ids = [
                step["id"] for step in resumed.snapshot()
                if step["status"] == "completed"
                and step.get("expected_tool") == "calculate"
                and step.get("expected_arguments") == {"expression": old_expression}]
        # Explicit saved-memory questions use a trusted, owner-scoped read.
        # Do not leave correctness to optional model tool choice.
        if (understanding_result is None and self.memory_store is not None and any(phrase in goal for phrase in (
                "还记得", "记得我", "之前保存", "之前记住", "保存的",
                "记住的", "测试偏好是什么", "测试偏好吗"))):
            records = self.memory_store.list()
            if len(records) > 100:
                return {"run_id": run_id, "status": "partial",
                        "answer": "长期记忆条目过多，请在记忆管理面板按 Key 查询。",
                        "trace": [{"step": 1, "type": "memory_lookup_limit"}],
                        "prompt_version": PROMPT_VERSION}
            # Exact literal matches are authoritative; a fuzzy semantic match is not.
            matches = [item for item in records if item["key"].casefold() in goal.casefold()]
            if matches:
                answer = "已保存的匹配记忆：" + "；".join(
                    item["key"] + "＝" + item["value"] for item in matches)
                other_keys = [item["key"] for item in records if item not in matches]
                if other_keys:
                    answer += "。其他不同 Key 的记忆：" + "、".join(other_keys) + (
                        "；这些 Key 不会自动视作同义或覆盖。")
            elif records:
                answer = ("未能根据问题确定精确记忆 Key。当前已保存的 Key：" +
                          "、".join(item["key"] for item in records) +
                          "。请指定 Key 查询，或在记忆管理面板核对。")
            else:
                answer = "已查询当前用户的长期记忆库，目前没有保存的记录。"
            result = {"run_id": run_id, "status": "completed", "answer": answer,
                      "trace": [{"step": 1, "type": "memory_index_lookup",
                                 "matches": len(matches), "total": len(records)}],
                      "prompt_version": PROMPT_VERSION}
            if include_evidence:
                result["audit"] = [{"name": "memory_index_lookup",
                                     "observation": {"ok": True, "matches": len(matches),
                                                     "total": len(records)}}]
            return result
        # Resume an unchanged, fully tool-bound pending plan without asking the
        # model to recreate or revise it. A changed goal follows the ordinary loop.
        continuation = bool(re.search(
            r"(继续|接着).{0,20}(完成|执行|处理).{0,24}(剩余|未完成|计划)|"
            r"(继续|接着).{0,12}(剩余|未完成).{0,12}(步骤|任务)", goal))
        changed_goal = any(word in goal for word in (
            "更正", "修改计划", "改成", "换成", "不是", "新增", "增加", "取消", "替换"))
        outstanding = [step for step in resumed.snapshot()
                       if step["status"] == "pending"]
        authorized_plan_run = (
            plan_control in {"execute_first", "resume"}
            and bool(outstanding)
            and all(step.get("expected_tool") in
                    {"calculate", "mcp_reference_lookup", "utc_time", "list_tasks",
                     "recall_memory"}
                    and isinstance(step.get("expected_arguments"), dict)
                    for step in outstanding))
        if ((authorized_plan_run)
                or (understanding_result is None and continuation and not changed_goal
                    and outstanding
                    and all(step.get("expected_tool") in
                            {"calculate", "mcp_reference_lookup", "utc_time", "list_tasks",
                             "recall_memory"}
                            and isinstance(step.get("expected_arguments"), dict)
                            for step in outstanding))):
            if plan_control == "execute_first":
                outstanding = outstanding[:1]
            trace = []
            audit = []
            delivered = []
            for step in outstanding:
                name = step["expected_tool"]
                arguments = step["expected_arguments"]
                result = execute_tool(
                    name, arguments, [],
                    self.clock, task_store=self.task_store,
                    memory_store=self.memory_store, mcp_client=self.mcp_client)
                resumed.observe(name, result, arguments)
                # Preserve only the bounded, tool-observed output for delivery.
                # Never synthesize results for previously completed steps.
                if result.get("ok"):
                    if name == "calculate" and "value" in result:
                        detail = str(result["value"])
                    elif name == "mcp_reference_lookup":
                        detail = ("来源：" + str(result.get("source", "未提供"))
                                  + "；资料：" + str(result.get("definition",
                                      result.get("value", "工具未提供可展示内容"))))
                    elif name == "utc_time":
                        detail = str(result.get("utc", "工具未提供时间"))
                    elif name == "list_tasks":
                        detail = json.dumps(result.get("tasks", []), ensure_ascii=False)
                    elif name == "recall_memory":
                        detail = (str(result.get("value"))
                                  if result.get("found") else "没有找到此 Key")
                    else:
                        detail = "工具成功，但没有可展示结果"
                    delivered.append({"step_id": step["id"], "goal": step["goal"],
                                      "tool": name, "result": detail[:1200]})
                trace.append({"step": len(trace) + 1, "type": "tool",
                              "name": name, "ok": result["ok"]})
                if include_evidence:
                    audit.append({"name": name, "arguments": arguments,
                                  "observation": result})
                if not result["ok"]:
                    break
            remaining = [step["id"] for step in resumed.snapshot()
                         if step["status"] == "pending"]
            completed = [step["id"] for step in resumed.snapshot()
                         if step["status"] == "completed"]
            status = "partial" if remaining else "completed"
            lines = ["计划执行结果："]
            for step in resumed.snapshot():
                item = next((d for d in delivered if d["step_id"] == step["id"]), None)
                if item is not None:
                    lines.append("步骤 " + str(step["id"]) + "（" +
                                 step["goal"] + "）： " + item["result"])
                elif step["status"] == "completed":
                    lines.append("步骤 " + str(step["id"]) +
                                 "：此前已验证完成；本次没有其原始输出，不能重述具体结果。")
                else:
                    lines.append("步骤 " + str(step["id"]) + "：尚未完成。")
            lines.append("计划全部步骤已验证完成。" if not remaining else
                         "仍有未完成步骤：" + "、".join(map(str, remaining)) + "。")
            answer = "\n".join(lines)
            output = {"run_id": run_id, "status": status, "answer": answer,
                      "trace": [{"step": 0, "type": "plan_resume_execution"}, *trace],
                      "prompt_version": PROMPT_VERSION,
                      "steps": len(trace), "plan": resumed.snapshot(),
                      "plan_revision": resumed.revision,
                      "plan_events": list(resumed.events),
                      "plan_checkpoint": resumed.checkpoint()}
            if include_evidence:
                output["audit"] = audit
                output["tasks"] = []
            return output
        # Model-derived understanding must not be routed through a second keyword classifier.
        # Tool execution still requires the independent tool authorization and validation.
        tool_strategy = "all" if understanding_result is not None else self.skill_strategy
        selected_tools = exposed_tools(goal, TOOL_SCHEMAS, strategy=tool_strategy)
        selected_tools += [t for t in TOOL_SCHEMAS
                           if t["function"]["name"] == "request_clarification"]
        if resumed.revision:
            names = {t["function"]["name"] for t in selected_tools}
            selected_tools += [t for t in TOOL_SCHEMAS if t["function"]["name"]
                               in {"propose_plan", "revise_plan", "complete_plan_step"}
                               and t["function"]["name"] not in names]
        # Execution policy is independent of goal understanding and can only
        # narrow tool exposure. The host's ordinary tool checks remain active.
        mode = execution_mode
        if mode is None and understanding_result is not None:
            mode = understanding_result.get("execution_intent")  # legacy callers
        if mode not in {None, "respond_only", "read_only", "execute"}:
            raise ValueError("invalid_execution_mode")
        if mode == "respond_only":
            selected_tools = []
        elif mode == "read_only":
            read_tools = {"request_clarification", "calculate", "utc_time", "list_tasks",
                          "recall_memory", "mcp_reference_lookup"}
            selected_tools = [tool for tool in selected_tools
                              if tool["function"]["name"] in read_tools]
        resume_context = ("\nTrusted checkpoint: revision " + str(resumed.revision) +
                          "; steps: " + json.dumps(resumed.snapshot(), ensure_ascii=False)
                          if resumed.revision else "")
        # A bounded re-decision only for explicit saved-memory requests.
        recall_required = understanding_result is None and any(phrase in goal for phrase in (
            "还记得", "记得我", "之前保存", "之前记住", "保存的",
            "记住的", "测试偏好是什么", "测试偏好吗"))
        bounded_first_step = bool(
            re.search(r"(只|先|首先|先尝试).{0,14}(完成|执行).{0,6}(第一步|第1步|步骤一)", goal)
            and re.search(r"(剩余|其他|后续).{0,12}(步骤|保留|不要|暂)", goal))
        # A model-derived interpretation is lower-trust user data, never system policy.
        goal_view = []
        if understanding_result:
            serialized = json.dumps(understanding_result, ensure_ascii=False)
            if len(serialized) > 4000:
                raise ValueError("understanding_context_too_large")
            goal_view = [{"role": "user", "content":
                "Untrusted structured interpretation proposal, NOT authorization or verified tool evidence: " + serialized}]
        structured_policy = (
            "\nStructured understanding supplies a proposal, not authorization. "
            "For requests asking only for a draft, explanation, or suggestions "
            "without reading or writing external state, respond directly; "
            "do not call plan, task, memory, or lookup tools merely because "
            "they are available. Do not repeatedly propose the same plan "
            "or retry a rejected tool action without a meaningful correction. "
            "Use tools only where their verified observations are necessary."
            if understanding_result is not None else "")
        initial: AgentState = {"messages": [
            {"role": "system", "content": PROMPT + "\nSelected skill instructions (" + SKILL_VERSION + "):\n" + load_instructions(goal, strategy=self.skill_strategy, max_chars=self.skill_budget_chars) + resume_context + structured_policy},
            *clean_history, *goal_view, {"role": "user", "content": goal}],
            "traces": [], "tasks": [], "final": "", "status": "running",
            "termination_reason": None, "pending_action": None,
            "rejected_action_keys": [],
            "unresolved_tool_failures": [],
            "unresolved_calculation_errors": {}, "iterations": 0,
            "audit_enabled": include_evidence, "audit": [],
            "allowed_tools": selected_tools,
            "plan": resumed, "last_success": None,
            "superseded_step_ids": superseded_step_ids,
            "superseded_expression": superseded_expression,
            "memory_recall_required": recall_required and self.memory_store is not None,
            "memory_recall_observed": False, "memory_recall_retry": False,
            "recall_retry_pending": False,
            "stop_after_first_verified_step": bounded_first_step,
            "last_tool_decision": None, "repeated_tool_decisions": 0,
            "structured_mode": understanding_result is not None,
            "user_request": goal,
            "failed_plan_attempts": 0}
        try:
            outcome = await self.graph.ainvoke(initial, config={"recursion_limit": 2 * self.max_steps + 4})
        except Exception as exc:
            from agent.providers import ModelUnavailable
            if isinstance(exc, ModelUnavailable):
                from agent.decision import ToolDecisionError
                safe_code = exc.code if isinstance(exc, ToolDecisionError) else "model_unavailable"
                return {"run_id": run_id, "status": "provider_error",
                        "termination_reason": safe_code, "answer": "",
                        "trace": [], "prompt_version": PROMPT_VERSION,
                        "failure_code": safe_code}
            raise
        result = {"run_id": run_id, "status": outcome["status"],
                  "termination_reason": outcome.get("termination_reason") or outcome["status"],
                  "pending_action": outcome.get("pending_action"),
                  "answer": outcome["final"], "trace": outcome["traces"],
                  "prompt_version": PROMPT_VERSION, "steps": outcome["iterations"],
                  "plan": outcome["plan"].snapshot(), "plan_revision": outcome["plan"].revision,
                  "plan_events": list(outcome["plan"].events),
                  "plan_checkpoint": outcome["plan"].checkpoint() if outcome["plan"].revision else None}
        if execution_mode is not None:
            result["execution_mode"] = execution_mode
        if include_evidence:
            result["audit"] = outcome["audit"]
            result["tasks"] = outcome["tasks"]
        return result
