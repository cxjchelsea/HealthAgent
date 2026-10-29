"""Real M5 REVISE phrase: preserve clauses are not alternative edit targets."""
from agent.plan_revision_review import RevisionProposal, apply_plan_revision


def checkpoint():
    return {
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "计算 3*4", "status": "pending",
             "expected_tool": "calculate",
             "expected_arguments": {"expression": "3*4"}},
            {"id": 2, "goal": "计算 5*6", "status": "pending",
             "expected_tool": "calculate",
             "expected_arguments": {"expression": "5*6"}},
        ],
    }


def proposal(step_number=2, old="5*6", new="7*8"):
    return RevisionProposal(intent="revise", edit_requested=True,
                            step_number=step_number, expected_tool="calculate",
                            old_argument=old, new_argument=new)


def test_real_qwen_revise_phrase_is_admitted_without_tool_execution():
    old = checkpoint()
    message = "将已保存计划的第二步由计算 5*6 改为计算 7*8，第一步不变，不要执行。"
    revised = apply_plan_revision(old, message, proposal())
    assert revised is not None
    new = revised["checkpoint"]
    assert new["revision"] == 2
    assert new["steps"][0] == old["steps"][0]
    assert new["steps"][1]["expected_arguments"] == {"expression": "7*8"}
    assert new["steps"][1]["status"] == "pending"
    assert old["revision"] == 1
    assert old["steps"][1]["expected_arguments"] == {"expression": "5*6"}


def test_preserved_first_step_does_not_authorize_editing_it():
    message = "将已保存计划的第二步由计算 5*6 改为计算 7*8，第一步不变，不要执行。"
    assert apply_plan_revision(checkpoint(), message,
                               proposal(step_number=1)) is None


def test_second_explicit_edit_target_stays_conflict():
    message = "第二步计算 5*6 改为 7*8，第一步也改为 9*9。"
    assert apply_plan_revision(checkpoint(), message, proposal()) is None


def test_other_step_without_unchanged_qualifier_stays_conflict():
    message = "第二步计算 5*6 改为 7*8，第一步也需要调整。"
    assert apply_plan_revision(checkpoint(), message, proposal()) is None


def test_wrong_old_argument_remains_denied():
    message = "第二步计算 4*4 改为 7*8，第一步不变。"
    assert apply_plan_revision(checkpoint(), message,
                               proposal(old="4*4")) is None


def test_preserve_phrasing_without_target_does_not_grant_edit():
    message = "第一步不变，计算 5*6 改为 7*8。"
    assert apply_plan_revision(checkpoint(), message, proposal()) is None


def test_completed_target_cannot_be_revised():
    saved = checkpoint()
    saved["steps"][1]["status"] = "completed"
    saved["steps"][1]["evidence_tool"] = "calculate"
    message = "第二步由计算 5*6 改为计算 7*8，第一步不变。"
    assert apply_plan_revision(saved, message, proposal()) is None
