"""M4 safety negatives: no tool dispatch for untrusted or stale admission facts."""
from agent.m4_guarded_execution import admit_guarded_plan_execution
from agent.m4_guarded_plan import admit_guarded_plan_create
from agent.m4_guarded_cancel import admit_guarded_plan_cancel


def _plan():
    return {"revision": 1, "steps": [
        {"id": 1, "goal": "计算 4*7", "status": "pending",
         "expected_tool": "calculate", "expected_arguments": {"expression": "4*7"}},
        {"id": 2, "goal": "计算 9+3", "status": "pending",
         "expected_tool": "calculate", "expected_arguments": {"expression": "9+3"}},
    ]}


def test_guarded_execution_denies_absent_or_untrusted_authority():
    base = dict(conversation_id="c", request_id="r", checkpoint=_plan(),
                scope="execute_first", turn_created=True, semantic_confirmed=True)
    assert admit_guarded_plan_execution(**base)
    for changed in (
        {"turn_created": False},
        {"semantic_confirmed": False},
        {"checkpoint": None},
        {"checkpoint": {**_plan(), "revision": 0}},
        {"checkpoint": {**_plan(), "steps": [
            {**_plan()["steps"][0], "status": "completed"}, _plan()["steps"][1]]}},
        {"checkpoint": {**_plan(), "steps": [
            {**_plan()["steps"][0], "expected_tool": "unregistered"}, _plan()["steps"][1]]}},
        {"checkpoint": {**_plan(), "steps": [
            {**_plan()["steps"][0], "expected_arguments": {"expression": 9}}, _plan()["steps"][1]]}},
    ):
        assert not admit_guarded_plan_execution(**(base | changed)), changed


def test_guarded_create_and_cancel_require_current_turn_authority():
    plan = _plan()
    create = dict(message="创建计划第一步计算 4*7，第二步计算 9+3，只保存不执行。",
                  conversation_id="c", request_id="r", checkpoint=plan,
                  turn_created=True, semantic_confirmed=True)
    assert admit_guarded_plan_create(**create)
    assert not admit_guarded_plan_create(**(create | {"turn_created": False}))
    assert not admit_guarded_plan_create(**(create | {"semantic_confirmed": False}))
    assert not admit_guarded_plan_create(**(create | {"message": "先建计划"}))
    cancel = dict(message="取消刚才保存的计划，不要执行其中的步骤。",
                  conversation_id="c", request_id="r", checkpoint=plan,
                  turn_created=True, semantic_confirmed=True)
    assert admit_guarded_plan_cancel(**cancel)
    assert not admit_guarded_plan_cancel(**(cancel | {"semantic_confirmed": False}))
    assert not admit_guarded_plan_cancel(**(cancel | {"turn_created": False}))
    assert not admit_guarded_plan_cancel(**(cancel | {"message": "如何取消计划？"}))
