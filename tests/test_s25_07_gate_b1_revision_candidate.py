"""S25-07 Gate B1: revision proposal is never dispatch authority."""
import copy
import hashlib
import json

import pytest

from agent.dependency_plan_host import _step_for_read
from agent.dependency_plan_schema import DependencyPlanError
from agent.dependency_revision_candidate import (
    REVISED_EXECUTOR_VERSION, compile_revision_candidate,
)


def _parent(*, completed=True):
    first = {
        "id": 1, "goal": "查询 RAG", "depends_on": [],
        "status": "completed" if completed else "pending",
        "expected_tool": "mcp_reference_lookup", "tool_version": "1.0.0",
        "argument_bindings": {"term": {
            "kind": "literal", "value": "RAG", "source_quote": "RAG"}},
    }
    second = {
        "id": 2, "goal": "统计定义", "depends_on": [1],
        "status": "pending", "expected_tool": "text_stats",
        "tool_version": "1.0.0",
        "argument_bindings": {"text": {
            "kind": "observation_ref", "source_step_id": 1,
            "field": "definition"}},
    }
    record = {
        "dispatch_id": "verified-d1", "step_id": 1,
        "plan_revision": 1, "tool_name": "mcp_reference_lookup",
        "tool_version": "1.0.0", "resolved_arguments_digest": "b" * 64,
        "source_evidence_refs": [],
    }
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [first, second],
        "executions": {"1": record} if completed else {},
    }


def _compile(parent=None, edit=None, message="请将第二步改为统计定义的字符数"):
    parent = _parent() if parent is None else parent
    pending = [copy.deepcopy(s) for s in parent["steps"]
               if s["status"] == "pending"]
    if edit is None:
        pending[-1]["goal"] = "统计定义的字符数"
    else:
        edit(pending)
    return compile_revision_candidate(
        expected_raw=json.dumps(parent, ensure_ascii=False),
        replacement_pending=pending, current_user_message=message)


def _reject(code, parent=None, edit=None, message="请修改计划"):
    with pytest.raises(DependencyPlanError) as exc:
        _compile(parent, edit=edit, message=message)
    assert exc.value.code == code


def test_revision_candidate_preserves_completed_prefix_and_origin():
    original = _parent()
    raw = json.dumps(original, ensure_ascii=False)
    outcome = _compile(original)
    candidate = outcome.candidate
    assert candidate["revision"] == 2
    assert candidate["plan_executor_version"] == REVISED_EXECUTOR_VERSION
    assert candidate["steps"][0] == original["steps"][0]
    assert candidate["executions"] == original["executions"]
    assert candidate["lineage"]["parent_snapshot_sha256"] == hashlib.sha256(
        raw.encode("utf-8")).hexdigest()
    assert candidate["lineage"]["preserved_step_ids"] == [1]
    assert candidate["lineage"]["origin_refs"][0]["dispatch_id"] == "verified-d1"
    assert candidate["steps"][1]["goal"] == "统计定义的字符数"
    assert original["steps"][1]["goal"] == "统计定义"
    # Old S25-06 executor must refuse the uncommitted revision snapshot.
    with pytest.raises(DependencyPlanError):
        _step_for_read(candidate, 2)


def test_new_literal_is_bound_to_current_user_message():
    def edit(steps):
        steps[0]["argument_bindings"]["text"] = {
            "kind": "literal", "value": "新文本",
            "source_quote": "新文本",
        }
    result = _compile(edit=edit, message="请改为统计新文本")
    assert result.candidate["steps"][1]["argument_bindings"]["text"]["value"] == "新文本"
    _reject("dependency_source_unbound", edit=edit, message="请替换参数")


def test_new_literal_cannot_quote_partial_or_unrelated_source():
    def edit(steps):
        steps[0]["argument_bindings"]["text"] = {
            "kind": "literal", "value": "隐藏内容", "source_quote": "新文本",
        }
    _reject("dependency_source_unbound", edit=edit, message="请改为新文本")


def test_reject_removing_step_or_updating_completed_step_implicitly():
    _reject("dependency_revision_suffix_mismatch",
            edit=lambda steps: steps.clear())
    _reject("dependency_revision_suffix_mismatch",
            edit=lambda steps: steps.__setitem__(0, {
                **steps[0], "id": 3,
            }))


def test_reject_dependency_or_tool_escalation():
    _reject("dependency_revision_suffix_mismatch",
            edit=lambda steps: steps[0].update(depends_on=[]))
    _reject("dependency_invalid_schema",
            edit=lambda steps: steps[0].update(expected_tool="create_task"))
    _reject("dependency_invalid_schema",
            edit=lambda steps: steps[0].update(tool_version="2.0.0"))


def test_reject_reference_to_untrusted_or_unrelated_source():
    def edit(steps):
        steps[0]["argument_bindings"]["text"] = {
            "kind": "observation_ref", "source_step_id": 99,
            "field": "definition",
        }
    _reject("dependency_source_not_dependency", edit=edit)


def test_no_change_and_untrusted_parent_fail_closed():
    _reject("dependency_revision_no_change",
            edit=lambda steps: None)
    parent = _parent()
    parent["revision"] = 2
    _reject("dependency_revision_unsupported_parent", parent=parent)
    parent = _parent()
    parent["plan_executor_version"] = "unknown"
    _reject("dependency_revision_unsupported_parent", parent=parent)
    parent = _parent()
    parent["executions"]["1"]["dispatch_id"] = None
    _reject("dependency_untrusted_observation", parent=parent)


def test_all_pending_revision_is_still_only_a_candidate():
    parent = _parent(completed=False)
    raw = json.dumps(parent, ensure_ascii=False)
    pending = copy.deepcopy(parent["steps"])
    pending[1]["goal"] = "计算字符数"
    result = compile_revision_candidate(
        expected_raw=raw, replacement_pending=pending,
        current_user_message="第二步改为计算字符数",
    )
    assert result.candidate["executions"] == {}
    assert result.candidate["lineage"]["preserved_step_ids"] == []
    assert result.candidate["revision"] == 2
    assert result.candidate["steps"][0] == parent["steps"][0]


def test_revised_candidate_never_rewrites_original_source_input():
    parent = _parent()
    outcome = _compile(parent)
    assert outcome.candidate["steps"][0]["argument_bindings"] == (
        parent["steps"][0]["argument_bindings"])
    assert outcome.candidate["executions"]["1"]["plan_revision"] == 1


def test_goal_only_change_keeps_previously_grounded_literal():
    parent = _parent(completed=False)
    pending = copy.deepcopy(parent["steps"])
    pending[0]["goal"] = "仅修改第一步显示名称"
    candidate = compile_revision_candidate(
        expected_raw=json.dumps(parent, ensure_ascii=False),
        replacement_pending=pending,
        current_user_message="只修改第一步名称，不修改查询参数",
    ).candidate
    assert candidate["steps"][0]["goal"] == "仅修改第一步显示名称"
    assert candidate["steps"][0]["argument_bindings"] == (
        parent["steps"][0]["argument_bindings"])


def test_existing_read_tool_substitution_is_not_implicit_equivalence():
    # Both tools are present in the Registry, but this says nothing about
    # whether a replacement preserves user intent or source semantics.
    parent = _parent(completed=False)
    parent["steps"][1]["depends_on"] = []
    parent["steps"][1]["expected_tool"] = "mcp_reference_lookup"
    parent["steps"][1]["argument_bindings"] = {
        "term": {"kind": "literal", "value": "RAG", "source_quote": "RAG"}}
    pending = copy.deepcopy(parent["steps"])
    pending[1]["expected_tool"] = "text_stats"
    pending[1]["argument_bindings"] = {
        "text": {"kind": "literal", "value": "新文本",
                 "source_quote": "新文本"}}
    with pytest.raises(DependencyPlanError) as exc:
        compile_revision_candidate(
            expected_raw=json.dumps(parent, ensure_ascii=False),
            replacement_pending=pending,
            current_user_message="把第二步改成统计新文本")
    assert exc.value.code == "dependency_revision_tool_substitution_denied"


def test_output_does_not_alias_parent_or_replacement_input():
    parent = _parent()
    replacement = [copy.deepcopy(parent["steps"][1])]
    replacement[0]["goal"] = "更新统计目标"
    snapshot = json.dumps(parent, ensure_ascii=False)
    result = compile_revision_candidate(
        expected_raw=snapshot, replacement_pending=replacement,
        current_user_message="更新统计目标")
    result.candidate["steps"][1]["goal"] = "later mutation"
    result.candidate["executions"]["1"]["dispatch_id"] = "tampered"
    assert parent["steps"][1]["goal"] == "统计定义"
    assert replacement[0]["goal"] == "更新统计目标"
    assert parent["executions"]["1"]["dispatch_id"] == "verified-d1"
    # Candidate objects are deliberately not authorization certificates:
    # B2 MUST recheck the exact raw parent plus every DB evidence record.
