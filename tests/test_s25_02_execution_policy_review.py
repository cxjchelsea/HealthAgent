"""C01 regression: no tool-by-keyword router, independent constrained policy review."""
import asyncio
import json
import pytest

from agent.execution_policy import ExecutionPolicyDecision, verify_text_only_policy
from agent.understanding import UnderstandingError


class FakeProvider:
    def __init__(self, value):
        self.value = value
        self.calls = []

    async def complete(self, *, model, messages, response_schema):
        self.calls.append((model, messages, response_schema))
        return self.value


def run(review_json, decision=None, message="请使用计算工具计算 10/0"):
    provider = FakeProvider(review_json)
    result = asyncio.run(verify_text_only_policy(
        provider, "qwen3:14b", message, {},
        decision or ExecutionPolicyDecision(mode="respond_only", target="none")))
    return result, provider


def test_explicit_tool_request_corrects_text_only_policy():
    result, provider = run('{"requires_real_tool":true}')
    assert result.mode == "read_only"
    assert result.target == "tool_action"
    assert len(provider.calls) == 1
    assert "10/0" in provider.calls[0][1][1]["content"]


def test_unsaved_draft_or_explanation_keeps_respond_only():
    for request in ("请解释计算工具是什么，不要使用工具", "拟三项学习草案，不保存"):
        result, _ = run('{"requires_real_tool":false}', message=request)
        assert result.mode == "respond_only"


def test_policy_review_can_never_grant_write_permission():
    result, _ = run('{"requires_real_tool":true}', message="请实际调用一个只读工具")
    assert result.mode != "execute"


def test_other_modes_are_not_replaced_or_rechecked():
    result, provider = run("invalid", ExecutionPolicyDecision(mode="execute", target="tool_action"))
    assert result.mode == "execute"
    assert provider.calls == []


def test_malformed_review_fails_closed():
    with pytest.raises(UnderstandingError) as exc:
        run('{"requires_real_tool":"maybe"}')
    assert exc.value.code == "execution_policy_review_invalid"
