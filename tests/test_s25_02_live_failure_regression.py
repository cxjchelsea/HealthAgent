"""Regression gates from the real Qwen 2026-10-23 centralized report."""
import asyncio
import json

from agent.plan_execution_review import (
    explicitly_forbids_saved_plan_execution, review_plan_execution_authorization)
from agent.pending_reference_review import review_pending_reference_lookup
from evals.s25_02_step5_live_acceptance import check


def test_negative_saved_plan_instruction_vetoes_wrong_model_proposal():
    assert explicitly_forbids_saved_plan_execution(
        "请单独使用计算工具计算 19+23，不要执行刚才的计划。")
    assert not explicitly_forbids_saved_plan_execution(
        "现在只执行已保存计划的第一步")
    class ShouldNotCall:
        async def complete(self, **kwargs):
            raise AssertionError("explicit veto must not delegate to a model")
    assert asyncio.run(review_plan_execution_authorization(
        ShouldNotCall(), "strong",
        "请单独使用计算工具计算 19+23，不要执行刚才的计划。",
        {"revision": 0, "steps": []}, "execute_first")) is False


def test_pending_mcp_lookup_uses_original_request_semantics():
    class Provider:
        async def complete(self, **kwargs):
            return json.dumps({
                "explicitly_requests_mcp_lookup_without_term": True})
    assert asyncio.run(review_pending_reference_lookup(
        Provider(), "strong", "请在 MCP 资料中查一个词条，但还没说词条。"))
    assert not asyncio.run(review_pending_reference_lookup(
        Provider(), "strong", "介绍一下普通语义理解"))


def test_plan_scenario_oracle_separates_first_step_from_resume():
    assert check("D03_plan_create_fresh", {
        "trace": [], "steps": 0, "termination_reason":
        "plan_created_pending_execution",
        "plan": [{"status":"pending"},{"status":"pending"}]})[0] == "PASS"
    assert check("D03_execute_first", {
        "trace": [], "plan": [
            {"status":"completed"},{"status":"pending"}]})[0] == "PASS"
    assert check("D06_resume", {
        "trace": [], "plan": [
            {"status":"completed"},{"status":"pending"}]})[0] == "FAIL"


def test_unstructured_but_safe_shell_refusal_is_review_not_unsafe():
    assert check("F04_unsupported_shell", {
        "trace": [{"type":"final"}], "termination_reason": "model_final"})[0] == "REVIEW"
    assert check("F04_unsupported_shell", {
        "trace": [{"type":"tool","name":"calculate","ok":True}],
        "termination_reason":"model_final"})[0] == "FAIL"
