"""封存题的期望必须和确定性编译一致，且题面不得出现在分类提示词里。"""
import asyncio
import json

from agent.route_semantic_hierarchical import OBJECT_PROMPT, OP_PROMPT, PLAN_SCOPE_PROMPT
from evals.s25_02_m2_evidence_structured_live import evaluate
from evals.s25_02_m2_hierarchical_boundary_cases import CASES as BOUNDARY_CASES
from evals.s25_02_m2_predeclared_v1 import (
    CASES, FROZEN_MANIFEST_SHA256, assert_manifest_frozen, manifest_sha256, score_gate,
)
from evals.s25_02_m2_semantic_evidence_live import QUOTE_PROMPT


class ExpectedCoreProvider:
    def __init__(self, case):
        self.replies = iter([
            json.dumps({"object_type": case.expected_object}),
            json.dumps({"operation": case.expected_operation}),
            json.dumps({"current_turn_quote": None, "revision_step_quote": None}),
        ])

    async def complete(self, **kwargs):
        return next(self.replies)


def test_expected_labels_match_deterministic_pipeline_before_live_model():
    for case in CASES:
        row = asyncio.run(evaluate(
            ExpectedCoreProvider(case), "stub", case, "boundary", 5))
        assert row["core_correct"] is True
        assert row["disposition"] == case.expected_disposition
        assert row["host_authorization"] is False


def test_predeclared_prompts_do_not_repeat_classifier_or_curated_text():
    combined = "\n".join((OBJECT_PROMPT, OP_PROMPT, PLAN_SCOPE_PROMPT, QUOTE_PROMPT))
    curated = [case.prompt for case in BOUNDARY_CASES]
    seen = set()
    for case in CASES:
        assert case.prompt not in combined
        assert case.prompt not in curated
        assert case.prompt not in seen
        seen.add(case.prompt)
        for fragment in ("从第一阶段开始", "只执行第一步", "先运行第一项"):
            assert fragment not in case.prompt


def test_manifest_hash_is_frozen_before_scoring():
    assert manifest_sha256() == FROZEN_MANIFEST_SHA256
    assert_manifest_frozen()


def test_gate_holds_when_a_critical_case_fails_and_passes_only_above_threshold():
    def row(case_id, repetition, case_pass, disposition="needs_evidence"):
        return {
            "case_id": case_id, "repetition": repetition, "case_pass": case_pass,
            "disposition": disposition, "host_authorization": False,
        }
    ids = [case.case_id for case in CASES]
    held = []
    for repetition in (1, 2):
        for case_id in ids:
            held.append(row(case_id, repetition, case_id != "pd_task_cn"))
    assert score_gate(held)["predeclared_gate"] == "HOLD"
    assert score_gate(held)["m2_gate"] == "HOLD"
    clean = [row(case_id, repetition, True, "safe_non_effect")
             for repetition in (1, 2) for case_id in ids]
    assert score_gate(clean)["predeclared_gate"] == "PASS"
    assert score_gate(clean)["m3_cutover"] == "NOT_AUTHORIZED"
