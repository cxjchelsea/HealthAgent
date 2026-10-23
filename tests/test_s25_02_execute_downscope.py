"""Execute classification must not grant writes to read-only arithmetic."""
import asyncio
import pytest
from agent.execution_policy import ExecutionPolicyDecision, verify_execute_policy
from agent.understanding import UnderstandingError


class Model:
    def __init__(self, raw):
        self.raw=raw
    async def complete(self, *, model, messages, response_schema):
        return self.raw


def evaluate(raw, message="请使用计算工具计算 2**10"):
    return asyncio.run(verify_execute_policy(
        Model(raw), "strong", message, {},
        ExecutionPolicyDecision(mode="execute", target="tool_action")))


def test_readonly_calculation_is_downscoped():
    result=evaluate('{"requests_mutation":false}')
    assert result.mode == "read_only"
    assert result.target == "tool_action"


def test_real_task_creation_remains_write_scoped():
    result=evaluate('{"requests_mutation":true}', "创建名为做项目文档的待办任务")
    assert result.mode == "execute"


def test_invalid_mutation_review_does_not_grant_writes():
    with pytest.raises(UnderstandingError):
        evaluate('{"requests_mutation":"unknown"}')
