"""D evaluator scores object and operation separately."""
from types import SimpleNamespace
from evals.s25_02_m2_hierarchical_live import expected_core
from evals.s25_02_m2_shadow_live import CASES

def test_plan_execution_object_is_plan_not_step():
    for name,op in [("execute_first","execute_first"),
                    ("execute_first_en","execute_first"),
                    ("resume_plan","resume")]:
        case=next(x for x in CASES if x.case_id==name)
        assert expected_core(case)==("plan",op)

def test_cancel_and_pending_expectations():
    case=next(x for x in CASES if x.case_id=="cancel_pending")
    assert expected_core(case)==("pending_slot","cancel")
    case=next(x for x in CASES if x.case_id=="pending_reference_reply")
    assert expected_core(case)==("pending_slot","invoke")

def test_object_and_operation_can_disagree_independently():
    obj,op=expected_core(SimpleNamespace(expected_route="plan_execute_first"))
    assert obj=="plan"
    assert op=="execute_first"
    assert (obj=="plan_step") is False
