"""Execution policy reconciliation: model reviews cannot mint write authority."""
import asyncio
import json
import pytest

from agent.execution_policy import (
    ExecutionPolicyDecision, reconcile_execution_policy,
)


class Provider:
    def __init__(self, read=False, write=False):
        self.read, self.write, self.calls = read, write, 0

    async def complete(self, *, model, messages, response_schema=None):
        self.calls += 1
        assert set(response_schema["properties"]) == {
            "explicit_read_only_tool_request", "explicit_mutation_request"}
        return json.dumps({
            "explicit_read_only_tool_request": self.read,
            "explicit_mutation_request": self.write,
        })


@pytest.mark.parametrize("before,read,write,expected,target", [
    ("respond_only", True, False, "read_only", "tool_action"),
    ("respond_only", False, False, "respond_only", "none"),
    ("respond_only", False, True, "respond_only", "none"),
    ("execute", True, False, "read_only", "tool_action"),
    ("execute", False, False, "read_only", "tool_action"),
    ("execute", False, True, "execute", "tool_action"),
    ("read_only", True, True, "read_only", "tool_action"),
])
def test_execution_policy_reconciliation(before, read, write, expected, target):
    provider = Provider(read=read, write=write)
    before_decision = ExecutionPolicyDecision(
        mode=before, target="none" if before == "respond_only" else "tool_action")
    out = asyncio.run(reconcile_execution_policy(
        provider, "strong", "用户的当前请求", {}, before_decision))
    assert (out.mode, out.target) == (expected, target)
    assert provider.calls == (0 if before == "read_only" else 1)


def test_bad_policy_review_cannot_grant_permission():
    class Bad:
        async def complete(self, **kwargs):
            return "{}"
    from agent.understanding import UnderstandingError
    with pytest.raises(UnderstandingError):
        asyncio.run(reconcile_execution_policy(
            Bad(), "strong", "请求", {},
            ExecutionPolicyDecision(mode="execute", target="tool_action")))
