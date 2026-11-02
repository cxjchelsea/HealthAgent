"""Explicit operation selection is only a narrow M5 proposal admission scope."""
import pytest
from agent.s25_04_explicit_ordinary import admit_explicit_ordinary_calculation as admit


def test_canonical_calculation_is_eligible_for_host_policy_review():
    assert admit(selection="calculate", message="请计算 (19+23)*4",
                 saved_plan_present=False, pending_action_present=False,
                 clarification_present=False)


@pytest.mark.parametrize("changes", [
    {"selection": None},
    {"selection": "plan_create"},
    {"saved_plan_present": True},
    {"pending_action_present": True},
    {"clarification_present": True},
    {"message": "先保存执行计划，再请计算 (19+23)*4"},
    {"message": "请计算 (19+23)*4，并保存计划"},
    {"message": "请计算 (19+23)*4; 保存计划"},
    {"message": "请计算 (19+23)*4\n继续执行"},
    {"message": "请计算 10**100000"},
    {"message": "请计算 __import__('os')"},
    {"message": "请创建一个待办"},
])
def test_untrusted_or_mixed_request_does_not_bypass_m5(changes):
    base = dict(selection="calculate", message="请计算 (19+23)*4",
                saved_plan_present=False, pending_action_present=False,
                clarification_present=False)
    assert not admit(**(base | changes))
