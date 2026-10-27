"""M2 plan actions: evidence spans and independent semantic arbitration."""
import asyncio
import json
from types import SimpleNamespace

from agent.route_semantic_hierarchical import classify_hierarchical_diagnostic
from evals.s25_02_m2_plan_step_quotes import collect_ordered_step_quotes


class Model:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(next(self.outputs), ensure_ascii=False)


def test_grounded_three_actions_are_all_preserved():
    message = "按顺序检查电池、登记电压、交接设备。"
    p = Model([{"steps": ["检查电池", "登记电压", "交接设备"]}])
    result = asyncio.run(collect_ordered_step_quotes(p, "fixture", message, 2))
    assert [x.exact_text for x in result] == ["检查电池", "登记电压", "交接设备"]


def test_nonverbatim_or_overlapping_actions_are_rejected():
    message = "先检查设备，然后登记设备"
    for spans in (["检查设备", "不存在"], ["检查设备", "设备"],
                  ["检查设备", "检查设备"]):
        p = Model([{"steps": spans}])
        assert asyncio.run(collect_ordered_step_quotes(
            p, "fixture", message, 2)) == []


def test_two_legacy_quote_fields_remain_compatible():
    p = Model([{"first": "核对账目", "second": "发送报告"}])
    result = asyncio.run(collect_ordered_step_quotes(
        p, "fixture", "核对账目，然后发送报告", 2))
    assert len(result) == 2


def test_execute_saved_step_is_plan_scope_not_step_revision():
    facts = SimpleNamespace(has_plan=True, plan_states=("pending", "pending"),
                            pending_action=None, pending_field=None,
                            pending_original_request_bound=False)
    p = Model([{"object_type": "plan_step"},
               {"intent": "execute_existing_step"},
               {"operation": "execute_first"}])
    result = asyncio.run(classify_hierarchical_diagnostic(
        p, "fixture", "启动已保存流程的开头环节，不改流程内容", facts, 2))
    assert (result.object_type, result.operation) == ("plan", "execute_first")


def test_edit_saved_step_remains_revision():
    facts = SimpleNamespace(has_plan=True, plan_states=("pending",),
                            pending_action=None, pending_field=None,
                            pending_original_request_bound=False)
    p = Model([{"object_type": "plan_step"},
               {"intent": "edit_saved_step"}, {"operation": "revise"}])
    result = asyncio.run(classify_hierarchical_diagnostic(
        p, "fixture", "把流程第一步的说明换成夜间版本", facts, 2))
    assert (result.object_type, result.operation) == ("plan_step", "revise")


def test_uncertain_step_action_does_not_gain_execute_route():
    facts = SimpleNamespace(has_plan=True, plan_states=("pending",),
                            pending_action=None, pending_field=None,
                            pending_original_request_bound=False)
    p = Model([{"object_type": "plan_step"},
               {"intent": "uncertain"}, {"operation": "revise"}])
    result = asyncio.run(classify_hierarchical_diagnostic(
        p, "fixture", "处理已存档流程的第一步", facts, 2))
    assert (result.object_type, result.operation) == ("plan_step", "revise")
