"""Admission shadow cannot authorize side effects or route changes."""
import pytest

from agent.s25_04_admission_shadow import evaluate_admission_shadow

BASE = dict(
    classification="ordinary_tool", classifier_reason="bound",
    request="在任务清单里记下月底检查设备",
    evidence_quote="在任务清单里记下月底检查设备",
    preturn_plan_present=True, checkpoint_trusted=True,
    mixed_or_plan_intent_excluded=True,
)

@pytest.mark.parametrize("override,reason", [
    ({"classification": "plan_control"}, "not_confident_ordinary"),
    ({"classification": "ambiguous"}, "not_confident_ordinary"),
    ({"classifier_reason": "invalid"}, "not_confident_ordinary"),
    ({"classifier_reason": "unbound"}, "not_confident_ordinary"),
    ({"checkpoint_trusted": False}, "untrusted_checkpoint"),
    ({"preturn_plan_present": None}, "untrusted_checkpoint"),
    ({"evidence_quote": "不存在的原文"}, "unbound_quote"),
    ({"evidence_quote": "任务", "request": "任务和任务"}, "unbound_quote"),
    ({"mixed_or_plan_intent_excluded": False}, "independent_exclusion_missing"),
])
def test_fail_closed(override, reason):
    result = evaluate_admission_shadow(**{**BASE, **override})
    assert result.advisory == "not_eligible"
    assert result.reason == reason
    assert result.execution_authorized is False


def test_review_candidate_is_not_permission():
    result = evaluate_admission_shadow(**BASE)
    assert result.advisory == "review_candidate"
    assert result.execution_authorized is False


def test_unknown_label_cannot_expand_permission():
    result = evaluate_admission_shadow(**{**BASE, "classification": "execute_all"})
    assert result.advisory == "not_eligible"


@pytest.mark.parametrize("label,user_request", [
    ("plan_control", "请将两步执行计划保存到系统"),
    ("plan_control", "从上次保存计划的下一步恢复执行"),
    ("ambiguous", "先创建计划再给我加一个待办"),
    ("ambiguous", "按刚才说的操作"),
    ("ambiguous", "取消已保存计划，并记住我的偏好"),
])
def test_plan_and_mixed_never_become_review_candidates(label, user_request):
    result = evaluate_admission_shadow(**{
        **BASE, "classification": label, "request": user_request,
        "evidence_quote": user_request,
    })
    assert result.advisory == "not_eligible"
    assert result.execution_authorized is False


def test_model_only_ordinary_with_no_independent_host_proof_is_denied():
    result = evaluate_admission_shadow(**{
        **BASE, "classification": "ordinary_tool",
        "request": "计算 (19+23)*4",
        "evidence_quote": "计算 (19+23)*4",
        "mixed_or_plan_intent_excluded": False,
    })
    assert result.reason == "independent_exclusion_missing"
    assert result.execution_authorized is False
