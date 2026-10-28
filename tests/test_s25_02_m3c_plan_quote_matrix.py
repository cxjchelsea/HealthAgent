"""M3-C: exact plan-step evidence matrix. Stubbed model; NOT live Qwen."""
import asyncio
import json
import pytest

from agent.route_shadow import evaluate_shadow


class FixedProposal:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        return json.dumps({
            "schema_version": "s25-route-v2",
            "primary_route": "plan_create",
            "route_target": "saved_plan",
            "execution_scope": "none",
            "effect_intent": "mutation_requested",
            "ambiguity": "clear",
            "source_quote_evidence": [{"exact_text": "计划"}],
            "plan_step_quote_evidence": [
                {"exact_text": x} for x in self.evidence
            ],
        }, ensure_ascii=False)


@pytest.mark.parametrize("message,step1,step2", [
    ("创建计划：第一步计算 123*456，第二步计算 789*12；只保存不执行。",
     "第一步计算 123*456", "第二步计算 789*12"),
    ("请保存计划：先查询 RAG 的定义，然后计算 8*8。暂不执行。",
     "先查询 RAG 的定义", "然后计算 8*8"),
    ("制作一个待执行计划：step 1 calculate 12*13; step 2 calculate 7*9.",
     "step 1 calculate 12*13", "step 2 calculate 7*9"),
])
def test_two_unique_ordered_quotes_pass_source_validation(message, step1, step2):
    provider = FixedProposal([step1, step2])
    result = asyncio.run(evaluate_shadow(
        provider, "fake", message, has_plan=False, pending_action=None))
    assert provider.calls == 1
    assert result["state"] == "valid", result
    assert result["route"] == "plan_create"
    assert result["evidence_spans"] >= 1
    assert result["host_admission_complete"] is False
    assert result["host_would_deny"] == "write_provenance_unverified"


@pytest.mark.parametrize("evidence,expected", [
    ([], "shadow_plan_steps_ungrounded"),
    (["第一步计算 123*456"], "shadow_plan_steps_ungrounded"),
    (["第一步计算 123*456", "第一步计算 123*456"], "shadow_quote_overlapping"),
    (["计算 123*456", "计算 123*456"], "shadow_quote_overlapping"),
    (["第一步计算 123*456", "模型虚构的步骤"], "shadow_quote_not_found"),
])
def test_missing_duplicate_and_forged_quotes_fail_closed(evidence, expected):
    message = "创建计划：第一步计算 123*456，第二步计算 789*12；只保存不执行。"
    outcome = asyncio.run(evaluate_shadow(
        FixedProposal(evidence), "fake", message,
        has_plan=False, pending_action=None))
    assert outcome["state"] == "unavailable"
    assert outcome["validation_code"] == expected
    assert outcome["evidence_admissible"] is False
    assert outcome["semantic_route_comparison_only"] == "plan_create"
    assert outcome["host_admission_complete"] is False


def test_provenance_failure_reports_bounded_model_evidence_counts():
    msg = "请保存计划：第一步计算 2*3，第二步计算 4*5。"
    result = asyncio.run(evaluate_shadow(
        FixedProposal(["第一步计算 2*3"]), "fake", msg,
        has_plan=False, pending_action=None))
    assert result["state"] == "unavailable"
    assert result["validation_code"] == "shadow_plan_steps_ungrounded"
    assert result["model_step_quotes"] == 1
    assert result["model_step_spans"] == 0
    assert result["model_source_quotes"] == 1
    assert result["evidence_admissible"] is False
    assert result["host_admission_complete"] is False


def test_plan_create_prompt_names_exact_quote_fields_not_legacy_offsets():
    from agent.route_shadow import PROMPT
    assert "plan_step_quote_evidence (one per ordered step)" in PROMPT
    assert "source_quote_evidence" in PROMPT
    assert "Do NOT use\nplan_step_evidence numeric offsets" in PROMPT


def test_plan_create_missing_quotes_remains_fail_closed():
    message = "请创建计划：第一步计算 123*456，第二步计算 789*12。"
    result = asyncio.run(evaluate_shadow(
        FixedProposal([]), "fake", message,
        has_plan=False, pending_action=None))
    assert result["validation_code"] == "shadow_plan_steps_ungrounded"
    assert result["model_step_quotes"] == 0
    assert result["evidence_admissible"] is False


class TwoStageProposal(FixedProposal):
    def __init__(self, recovery):
        super().__init__([])
        self.recovery = recovery

    async def complete(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return json.dumps({
                "schema_version": "s25-route-v2",
                "primary_route": "plan_create",
                "route_target": "saved_plan",
                "execution_scope": "none",
                "effect_intent": "mutation_requested",
                "ambiguity": "clear",
            })
        return json.dumps(self.recovery, ensure_ascii=False)


def test_missing_quotes_can_recover_only_literal_evidence():
    message = "请创建两步计划：第一步计算 123*456，第二步计算 789*12。只保存，不执行。"
    provider = TwoStageProposal({
        "creation_quote": "请创建两步计划",
        "step_quotes": ["第一步计算 123*456", "第二步计算 789*12"],
    })
    result = asyncio.run(evaluate_shadow(
        provider, "fake", message, has_plan=False, pending_action=None))
    assert provider.calls == 2
    assert result["state"] == "valid"
    assert result["route"] == "plan_create"
    assert result["recovery_model_calls"] == 1
    assert result["host_admission_complete"] is False
    assert result["host_would_deny"] == "write_provenance_unverified"


def test_recovery_fabrication_cannot_bypass_source_verification():
    message = "请创建两步计划：第一步计算 123*456，第二步计算 789*12。"
    provider = TwoStageProposal({
        "creation_quote": "请创建两步计划",
        "step_quotes": ["第一步计算 123*456", "第三步虚构 1*1"],
    })
    result = asyncio.run(evaluate_shadow(
        provider, "fake", message, has_plan=False, pending_action=None))
    assert result["state"] == "unavailable"
    assert result["validation_code"] == "shadow_quote_not_found"
    assert result["host_admission_complete"] is False
    assert result["evidence_admissible"] is False
