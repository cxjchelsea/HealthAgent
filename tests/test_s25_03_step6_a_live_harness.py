"""Offline guards for optional S25-03 real-provider HTTP acceptance."""
import pytest


def test_real_http_acceptance_rejects_fake_provider(monkeypatch):
    from evals.s25_03_step6_a_live import evaluate
    monkeypatch.delenv("RUN_S25_03_LIVE", raising=False)
    with pytest.raises(RuntimeError, match="RUN_S25_03_LIVE"):
        evaluate()
    monkeypatch.setenv("RUN_S25_03_LIVE", "1")
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    with pytest.raises(RuntimeError, match="real model provider"):
        evaluate()


def test_real_http_acceptance_has_bounded_repeats(monkeypatch):
    from evals.s25_03_step6_a_live import evaluate
    monkeypatch.setenv("RUN_S25_03_LIVE", "1")
    with pytest.raises(ValueError, match="repeats"):
        evaluate(0)
    with pytest.raises(ValueError, match="repeats"):
        evaluate(6)


def test_real_http_plan_prompt_uses_primary_supported_bound_step_types():
    """The original UTC-time probe was invalid: Primary cannot create UTC plans."""
    from agent.primary_route_v1 import ProposedStep, RouteSignal, bind_route_signal

    prompt = (
        "请创建一个只保存、不执行的两步计划："
        "第一步通过 MCP 参考目录查询 RAG 的定义；第二步计算 6*7。"
    )
    proposal = RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "plan_create",
        "quote": "请创建一个只保存、不执行的两步计划",
        "execution_scope": "none",
        "effect_intent": "mutation_requested",
        "steps": [
            {"goal": "查询 RAG 定义", "tool_name": "mcp_reference_lookup",
             "arguments": {"term": "RAG"},
             "quote": "通过 MCP 参考目录查询 RAG 的定义"},
            {"goal": "计算 6*7", "tool_name": "calculate",
             "arguments": {"expression": "6*7"},
             "quote": "计算 6*7"},
        ],
    })
    bound = bind_route_signal(
        proposal, prompt, saved_plan_present=False, pending_action=None)
    assert [step.tool_name for step in bound.steps] == [
        "mcp_reference_lookup", "calculate"]
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ProposedStep.model_validate({
            "goal": "UTC", "tool_name": "utc_time",
            "arguments": {}, "quote": "UTC"})


@pytest.mark.parametrize("payload,shape", [
    ('{"route_kind":"plan_create","steps":[{"tool_name":"mcp_reference_lookup","arguments":{"term":"rag"}},{"tool_name":"calculate","arguments":{"expression":"6*7"}}]}',
     ["exact", "exact"]),
    ('{"route_kind":"plan_create","steps":[{"tool_name":"mcp_reference_lookup","arguments":{"query":"rag"}},{"tool_name":"calculate","arguments":{"expression":"6*7","note":"x"}}]}',
     ["missing_and_extra", "extra_only"]),
    ('{"route_kind":"plan_create","steps":[{"tool_name":"mcp_reference_lookup","arguments":{}}]}',
     ["missing_only"]),
])
def test_safe_primary_step_shape_report_has_no_argument_names_or_values(payload, shape):
    from evals.s25_03_step6_a_live import safe_step_argument_shape
    result = safe_step_argument_shape(payload)
    assert [x["args_shape"] for x in result["steps"]] == shape
    assert "rag" not in str(result)
    assert "6*7" not in str(result)
    assert "query" not in str(result)
    assert "note" not in str(result)


def test_safe_primary_step_shape_reports_untrusted_invalid_json_without_content():
    from evals.s25_03_step6_a_live import safe_step_argument_shape
    assert safe_step_argument_shape('TOP_SECRET') == {"shape": "invalid_json"}
