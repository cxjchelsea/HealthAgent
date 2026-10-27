"""M2 semantic guidance must express object-operation semantics, not leaked answer strings."""
from agent.route_shadow import PROMPT


def test_semantic_contract_identifies_object_and_operation():
    assert "Identify the intended persistent object first" in PROMPT
    assert "Determine the operation on that object separately" in PROMPT
    assert "A single task record is not an ordered plan" in PROMPT


def test_plan_revision_ambiguity_and_pending_effect_are_explicit():
    assert "plan revision with no identifiable step" in PROMPT
    assert "pending-slot reply is a continuation" in PROMPT
    assert "effect is not none merely because the reply is short" in PROMPT


def test_literal_development_prompts_are_not_embedded():
    assert "周五提交测试报告" not in PROMPT
    assert "请查询 MCP 中 RAG 的定义。" not in PROMPT
    assert "将已保存计划第二步的 8*8 修改为 9*9" not in PROMPT


def test_quote_binding_and_authority_separation_remain_explicit():
    assert "The Host locates unique quotes deterministically" in PROMPT
    assert "Model effect labels never grant permission" in PROMPT
