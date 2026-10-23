"""D05: plan revisions must preserve bindings, reject conflicting step references."""
from agent.plan_intent import PlanRequest, build_plan_checkpoint
from agent.plan_revision_review import RevisionProposal, apply_plan_revision


def checkpoint():
    return build_plan_checkpoint(
        "查询 RAG，然后计算 8*8",
        PlanRequest(intent="plan_only", steps=[
            {"goal": "查询 RAG", "expected_tool": "mcp_reference_lookup",
             "expected_arguments": {"term": "RAG"}},
            {"goal": "计算 8*8", "expected_tool": "calculate",
             "expected_arguments": {"expression": "8*8"}},
        ]))


def test_d05_conflicting_first_step_claim_is_not_applied():
    saved = checkpoint()
    proposal = RevisionProposal(intent="revise", step_number=1,
                                expected_tool="calculate", old_argument="8*8",
                                new_argument="9*9")
    assert apply_plan_revision(saved, "第一步不是计算 8*8，而是计算 9*9", proposal) is None
    assert saved["revision"] == 1
    assert saved["steps"][0]["expected_tool"] == "mcp_reference_lookup"


def test_d05_second_step_revision_preserves_first_step():
    saved = checkpoint()
    proposal = RevisionProposal(intent="revise", step_number=2,
                                expected_tool="calculate", old_argument="8*8",
                                new_argument="9*9")
    updated = apply_plan_revision(saved, "将第二步计算 8*8 改为 9*9，其他不变", proposal)
    assert updated is not None
    cp = updated["checkpoint"]
    assert cp["revision"] == 2
    assert cp["steps"][0] == saved["steps"][0]
    assert cp["steps"][1]["expected_arguments"] == {"expression": "9*9"}
    assert all(s["status"] == "pending" for s in cp["steps"])
    assert updated["event"]["event"] == "plan_step_revised"


def test_d05_disallows_rewriting_completed_step():
    saved = checkpoint()
    saved["steps"][1]["status"] = "completed"
    saved["steps"][1]["evidence_tool"] = "calculate"
    proposal = RevisionProposal(intent="revise", step_number=2,
                                expected_tool="calculate", old_argument="8*8",
                                new_argument="9*9")
    assert apply_plan_revision(saved, "将第二步计算 8*8 改成 9*9", proposal) is None


def test_d05_model_cannot_silently_change_explicit_first_step_to_second():
    saved = checkpoint()
    proposal = RevisionProposal(intent="revise", step_number=2,
                                expected_tool="calculate", old_argument="8*8",
                                new_argument="9*9")
    assert apply_plan_revision(
        saved, "第一步不是计算 8*8 而是 9*9", proposal) is None
