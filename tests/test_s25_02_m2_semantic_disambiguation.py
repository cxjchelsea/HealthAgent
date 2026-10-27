"""Model-driven M2 semantic boundary tests; no phrase-specific routing."""

import asyncio
import json
from types import SimpleNamespace

from agent.route_semantic_hierarchical import (
    classify_hierarchical_diagnostic, disambiguate_semantic_object, OBJECT_PROMPT,
)
from agent.route_shadow_host import ShadowHostFacts


class Provider:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        next_reply = next(self.replies)
        if isinstance(next_reply, Exception):
            raise next_reply
        return json.dumps(next_reply)


def facts(**updates):
    from dataclasses import replace
    return replace(ShadowHostFacts(has_plan=False), **updates)


def run(provider, message, host):
    return asyncio.run(classify_hierarchical_diagnostic(
        provider, "synthetic-test-model", message, host, timeout=2))


def test_task_vs_plan_second_model_then_operation():
    p = Provider([
        {"object_type": "task"},
        {"target": "plan"},
        {"action": "create_plan"},
    ])
    out = run(p, "存档一个分阶段巡检流程，今后按顺序执行", facts())
    assert (out.object_type, out.operation) == ("plan", "create")
    assert len(p.calls) == 3
    assert set(p.calls[2]["response_schema"]["properties"]["action"]["enum"]) == {
        "create_plan", "execute_first", "resume_remaining",
        "edit_step_definition", "cancel_plan", "uncertain"}


def test_true_task_remains_task():
    p = Provider([{"object_type": "task"}, {"target": "task"},
                  {"operation": "create"}])
    out = run(p, "创建一个下周检查的待办事项", facts())
    assert (out.object_type, out.operation) == ("task", "create")


def test_pending_reply_uses_bound_context_but_not_authority():
    host = facts(pending_action="mcp_reference_lookup",
                 pending_field="term", pending_original_request_bound=True)
    p = Provider([{"object_type": "tool"},
                  {"relation": "supply_bound_argument"},
                  {"operation": "invoke"}])
    out = run(p, "此前查询缺的是关于海冰的主题", host)
    assert (out.object_type, out.operation) == ("pending_slot", "invoke")
    assert p.calls[1]["messages"][1]["content"].find('"pending_tool_request_bound": true') >= 0


def test_pending_new_tool_not_captured_by_pending():
    host = facts(pending_action="mcp_reference_lookup",
                 pending_field="term", pending_original_request_bound=True)
    p = Provider([{"object_type": "tool"},
                  {"relation": "new_independent_tool_request"},
                  {"operation": "invoke"}])
    out = run(p, "另外使用计算器求值", host)
    assert (out.object_type, out.operation) == ("tool", "invoke")


def test_unbound_pending_never_reclassified():
    p = Provider([{"object_type": "tool"}, {"operation": "invoke"}])
    out = run(p, "检索一下资料", facts())
    assert (out.object_type, out.operation) == ("tool", "invoke")
    assert len(p.calls) == 2


def test_disambiguator_failure_retains_first_stage_without_authority():
    p = Provider([{"object_type": "task"}, RuntimeError("offline"),
                  {"operation": "create"}])
    out = run(p, "创建一个待办事项", facts())
    assert (out.object_type, out.operation) == ("task", "create")


def test_no_change_to_original_object_prompt_or_plan_execution():
    assert "The ordinal \"first\" denotes an execution SCOPE" in OBJECT_PROMPT
    p = Provider([{"object_type": "plan"}, {"action": "execute_first"}])
    out = run(p, "先只执行保存计划的第一步", facts(has_plan=True))
    assert (out.object_type, out.operation) == ("plan", "execute_first")
    assert len(p.calls) == 2
