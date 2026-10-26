"""M2 real-model semantic shadow matrix with trusted, read-only Host fixtures.

This never calls the Conversation API, tools, or stores. The fixtures describe
Host facts that a later cutover must verify; they grant no production authority.
Reports contain stable case/request identities and no prompt text.
"""
import argparse
import asyncio
import hashlib
import json
import os
import statistics
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent.config import Settings
from agent.route_shadow import PROMPT, evaluate_shadow
from agent.route_shadow_host import ShadowHostFacts
from agent.understanding import HTTPUnderstandingModel


FROZEN_PROMPT_V1_SHA256 = "a87abb6e73079d265f3ab4bffdb4765a57d7a01608b1ee6fec2b7ec3aff24bb3"
FROZEN_HELDOUT_V1_SHA256 = "152b6bd60dffb8f2419de297df99e7870bbfcb416f9629278bfab5c3773b69cf"
FROZEN_PROMPT_V2_SHA256 = "d97e791c42b79668fed3082bfbab991f6f05e1ee583a2804feee9f4cb01e1b6b"
FROZEN_HELDOUT_V2_SHA256 = "98ce80ffbaeff0b577a620d221bd44ea476ce11f657c8a6fda56a09ad382a007"
FROZEN_PROMPT_V3_SHA256 = "676f44ef0d95c63c20571c3918e2d4787e9ff9f0a0951af5e50705681bde4e66"
FROZEN_HELDOUT_V3_SHA256 = "e8c45a75fbd6d97234a439c50e60719db973a0a56adb9e75a3dd6d5b82a5fcad"
FROZEN_HELDOUT_V4_SHA256 = "7712b8fc63be2124f8f63f362b35433bddca76bee426919ceae44664506fdaae"
FROZEN_V4_PROTOCOL_SHA256 = "0595f644b7a3c5bab407037c0988ce1ff9c49c1b7a7b0d639918527e1809d645"
FROZEN_PROMPT_V5_SHA256 = "0809871d00cd6864b831a29829597bd20468d8b9e25c0893e7a660a31706cdf1"
FROZEN_HELDOUT_V5_SHA256 = "6adb7bcb8784a2e2048a8f7b4593a555860aa2712a32cbe8a586d75ab8ac8dd5"
FROZEN_V5_PROTOCOL_SHA256 = "4b0a7aea183c50dc4dad1ef9c6bfd5e89e843b5318eab469bb836f2f43aff467"
DEFAULT_REGRESSION_COHORT = "heldout_v3"
SEMANTIC_ACCEPTANCE_COHORT: str | None = "heldout_v5"
COHORTS = {"development_leaked", "tuned_regression", "heldout_v1",
           "heldout_v2", "heldout_v3", "heldout_v4", "heldout_v5"}
V4_REQUIRED_REPETITIONS = 2
V4_MIN_PASS_RATE = 0.95
V4_EXPECTED_CASE_COUNT = 40
V4_CRITICAL_CASE_IDS = frozenset({
    "v4_task_cn_1", "v4_task_cn_2", "v4_task_en_1", "v4_task_cn_3",
    "v4_pending_term", "v4_pending_expression", "v4_pending_title",
    "v4_independent_plan_cn", "v4_independent_plan_en",
    "v4_plan_create_cn_1", "v4_plan_create_cn_2",
    "v4_plan_create_en_1", "v4_plan_create_en_2",
    "v4_plan_revise_cn_1", "v4_plan_revise_cn_2", "v4_plan_revise_en",
    "v4_execute_first_cn_1", "v4_execute_first_cn_2", "v4_execute_first_en",
    "v4_resume_cn_1", "v4_resume_en", "v4_resume_missing",
    "v4_resume_completed", "v4_cancel_cn_1", "v4_cancel_cn_2",
    "v4_cancel_en", "v4_respond_cn", "v4_respond_en",
})
ACCEPTANCE_REQUIRED_REPETITIONS = 2
ACCEPTANCE_MIN_PASS_RATE = 0.95
ACCEPTANCE_EXPECTED_CASE_COUNT = 32
V5_CRITICAL_CASE_IDS = frozenset({
    "v5_task_cn_1", "v5_task_cn_2", "v5_task_en",
    "v5_pending_term", "v5_pending_expression", "v5_pending_title",
    "v5_independent_cn", "v5_independent_en",
    "v5_plan_create_cn_1", "v5_plan_create_cn_2", "v5_plan_create_en",
    "v5_plan_revise_cn_1", "v5_plan_revise_cn_2", "v5_plan_revise_en",
    "v5_execute_first_cn", "v5_execute_first_en",
    "v5_resume_cn", "v5_resume_missing", "v5_resume_completed",
    "v5_cancel_cn_1", "v5_cancel_cn_2", "v5_cancel_en",
    "v5_respond_cn", "v5_respond_en",
})
ACCEPTANCE_CRITICAL_CASE_IDS = V5_CRITICAL_CASE_IDS


@dataclass(frozen=True)
class ShadowCase:
    case_id: str
    prompt: str
    expected_route: str
    expected_target: str | None
    facts: ShadowHostFacts
    expected_admitted_route: str | None = None
    expected_disposition: str | None = None
    expected_effect: str | None = None
    cohort: str = "tuned_regression"


def facts(**kwargs) -> ShadowHostFacts:
    return ShadowHostFacts(has_plan=False, **kwargs)


def v4_cases() -> tuple[ShadowCase, ...]:
    """Predeclared broad blind matrix; do not tune PROMPT from its results."""
    write = facts(current_write_authorized=True, tool_registered=True,
                  tool_schema_valid=True, source_provenance_valid=True)
    read = facts(current_read_bound=True, tool_registered=True,
                 tool_schema_valid=True, source_provenance_valid=True)
    pending_read = facts(
        pending_action="mcp_reference_lookup", pending_field="term",
        pending_original_request_bound=True, current_read_bound=True,
        tool_registered=True, tool_schema_valid=True,
        source_provenance_valid=True)
    pending_math = facts(
        pending_action="calculate", pending_field="expression",
        pending_original_request_bound=True, current_read_bound=True,
        tool_registered=True, tool_schema_valid=True,
        source_provenance_valid=True)
    pending_write = facts(
        pending_action="create_task", pending_field="title",
        pending_original_request_bound=True, current_write_authorized=True,
        tool_registered=True, tool_schema_valid=True,
        source_provenance_valid=True)
    plan_write = facts(current_write_authorized=True, plan_schema_valid=True,
                       source_provenance_valid=True)
    saved = ShadowHostFacts(
        has_plan=True, plan_states=("pending", "pending"),
        revision_valid=True, bound_step_valid=True, current_read_bound=True,
        tool_registered=True, tool_schema_valid=True,
        source_provenance_valid=True)
    saved_revise = ShadowHostFacts(
        has_plan=True, plan_states=("pending", "pending"),
        revision_valid=True, bound_step_valid=True,
        current_write_authorized=True, source_provenance_valid=True)
    saved_partial = ShadowHostFacts(
        has_plan=True, plan_states=("completed", "pending"),
        revision_valid=True, bound_step_valid=True, current_read_bound=True,
        tool_registered=True, tool_schema_valid=True,
        source_provenance_valid=True)
    saved_done = ShadowHostFacts(
        has_plan=True, plan_states=("completed", "completed"),
        revision_valid=True, bound_step_valid=True, current_read_bound=True,
        tool_registered=True, tool_schema_valid=True,
        source_provenance_valid=True)
    pending_cancel = facts(
        pending_action="mcp_reference_lookup", pending_field="term",
        pending_original_request_bound=True, source_provenance_valid=True)
    return (
        ShadowCase("v4_respond_cn", "拟一份值班安排示例，只展示内容，不登记任何事项。",
                   "respond", None, facts(), "respond", cohort="heldout_v4"),
        ShadowCase("v4_respond_en", "Draft a packing checklist for display only; create no task or plan.",
                   "respond", None, facts(), "respond", cohort="heldout_v4"),
        ShadowCase("v4_respond_cn_2", "说明怎样拆解项目步骤，但不要保存为流程。",
                   "respond", None, facts(), "respond", cohort="heldout_v4"),
        ShadowCase("v4_clarify_lookup", "启动知识检索，但我还没有决定搜索词。",
                   "clarify", None, facts(), "clarify", cohort="heldout_v4"),
        ShadowCase("v4_clarify_math", "请用计算器处理一个表达式，具体式子稍后提供。",
                   "clarify", None, facts(), "clarify", cohort="heldout_v4"),
        ShadowCase("v4_clarify_step", "修改保存方案里的某一步，但我没说是哪一步。",
                   "clarify", None, ShadowHostFacts(
                       has_plan=True, plan_states=("pending", "pending")),
                   "clarify", cohort="heldout_v4"),
        ShadowCase("v4_task_cn_1", "在事项清单登记一条：本月二十八号核验备份。",
                   "agent_loop", "tool_action", write, "ordinary_loop",
                   expected_effect="mutation_requested", cohort="heldout_v4"),
        ShadowCase("v4_task_cn_2", "创建唯一一条提醒，名称是周三更换门禁密码。",
                   "agent_loop", "tool_action", write, "ordinary_loop",
                   expected_effect="mutation_requested", cohort="heldout_v4"),
        ShadowCase("v4_task_en_1", "Save one task titled review vendor access on Monday.",
                   "agent_loop", "tool_action", write, "ordinary_loop",
                   expected_effect="mutation_requested", cohort="heldout_v4"),
        ShadowCase("v4_task_cn_3", "把明早缴电费作为一项正式事项保存。",
                   "agent_loop", "tool_action", write, "ordinary_loop",
                   expected_effect="mutation_requested", cohort="heldout_v4"),
        ShadowCase("v4_lookup_cn_1", "去 MCP 术语库检索稀疏注意力的定义。",
                   "agent_loop", "tool_action", read, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v4"),
        ShadowCase("v4_lookup_cn_2", "查一下参考资料中的持续学习是什么意思。",
                   "agent_loop", "tool_action", read, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v4"),
        ShadowCase("v4_lookup_en", "Look up the term knowledge distillation in the MCP catalog.",
                   "agent_loop", "tool_action", read, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v4"),
        ShadowCase("v4_math_cn_1", "调用计算工具求 17 的平方再减 5。",
                   "agent_loop", "tool_action", read, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v4"),
        ShadowCase("v4_math_cn_2", "只计算 (96/8)+7，不保存结果。",
                   "agent_loop", "tool_action", read, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v4"),
        ShadowCase("v4_math_en", "Calculate 14*(9-2) with the calculator.",
                   "agent_loop", "tool_action", read, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v4"),
        ShadowCase("v4_pending_term", "稀疏注意力", "agent_loop",
                   "pending_clarification", pending_read, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v4"),
        ShadowCase("v4_pending_expression", "(27+9)/6", "agent_loop",
                   "pending_clarification", pending_math, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v4"),
        ShadowCase("v4_pending_title", "整理年度合同", "agent_loop",
                   "pending_clarification", pending_write, "ordinary_loop",
                   expected_effect="mutation_requested", cohort="heldout_v4"),
        ShadowCase("v4_unsupported_cn", "用数据库控制台列出所有系统表。",
                   "agent_loop", "tool_action", facts(source_provenance_valid=True),
                   None, "needs_evidence", cohort="heldout_v4"),
        ShadowCase("v4_unsupported_en", "Run a terminal command to show disk mounts.",
                   "agent_loop", "tool_action", facts(source_provenance_valid=True),
                   None, "needs_evidence", cohort="heldout_v4"),
        ShadowCase("v4_independent_plan_cn", "保留已存方案不动，单独查询资料库里的灾难性遗忘。",
                   "agent_loop", "tool_action", ShadowHostFacts(
                       **{**asdict(saved), "has_plan": True}), "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v4"),
        ShadowCase("v4_independent_plan_en", "Do not resume the saved workflow; only calculate 63/9.",
                   "agent_loop", "tool_action", ShadowHostFacts(
                       **{**asdict(saved), "has_plan": True}), "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v4"),
        ShadowCase("v4_plan_create_cn_1", "建立待以后执行的两段流程：先查联邦学习，再算 23*4。",
                   "plan_create", None, plan_write, "create_pending_plan",
                   expected_effect="mutation_requested", cohort="heldout_v4"),
        ShadowCase("v4_plan_create_cn_2", "保存一个三步方案但先不运行：查询剪枝、计算 5*12、查询蒸馏。",
                   "plan_create", None, plan_write, "create_pending_plan",
                   expected_effect="mutation_requested", cohort="heldout_v4"),
        ShadowCase("v4_plan_create_en_1", "Store a two-step workflow for later: look up federated learning, then calculate 32+18.",
                   "plan_create", None, plan_write, "create_pending_plan",
                   expected_effect="mutation_requested", cohort="heldout_v4"),
        ShadowCase("v4_plan_create_en_2", "Create but do not execute a plan with step one calculate 7*6 and step two search for pruning.",
                   "plan_create", None, plan_write, "create_pending_plan",
                   expected_effect="mutation_requested", cohort="heldout_v4"),
        ShadowCase("v4_plan_revise_cn_1", "将已存方案第2项的算式从 23*4 改为 23*5，暂不运行。",
                   "plan_revise", None, saved_revise, "revise_pending_plan",
                   expected_effect="mutation_requested", cohort="heldout_v4"),
        ShadowCase("v4_plan_revise_cn_2", "保存流程的第一步改查联邦平均，其余步骤保持原样。",
                   "plan_revise", None, saved_revise, "revise_pending_plan",
                   expected_effect="mutation_requested", cohort="heldout_v4"),
        ShadowCase("v4_plan_revise_en", "Replace step 2 of the saved plan with calculate 40/5, without executing it.",
                   "plan_revise", None, saved_revise, "revise_pending_plan",
                   expected_effect="mutation_requested", cohort="heldout_v4"),
        ShadowCase("v4_execute_first_cn_1", "仅执行保存方案的第一步，执行后停止。",
                   "plan_execute_first", None, saved, "execute_saved_first",
                   cohort="heldout_v4"),
        ShadowCase("v4_execute_first_cn_2", "开始已有流程，但这轮到首项结束为止。",
                   "plan_execute_first", None, saved, "execute_saved_first",
                   cohort="heldout_v4"),
        ShadowCase("v4_execute_first_en", "Execute only the opening step of the stored workflow.",
                   "plan_execute_first", None, saved, "execute_saved_first",
                   cohort="heldout_v4"),
        ShadowCase("v4_resume_cn_1", "继续完成保存流程里尚未执行的项目。",
                   "plan_resume", None, saved_partial, "execute_saved_remaining",
                   cohort="heldout_v4"),
        ShadowCase("v4_resume_en", "Resume all unfinished steps in the saved workflow.",
                   "plan_resume", None, saved_partial, "execute_saved_remaining",
                   cohort="heldout_v4"),
        ShadowCase("v4_resume_missing", "继续先前存下但尚未完成的工作流。",
                   "plan_resume", None, facts(), None, "deny", cohort="heldout_v4"),
        ShadowCase("v4_resume_completed", "保存的流程已经做完了，仍请求继续剩余步骤。",
                   "plan_resume", None, saved_done, None, "safe_non_effect",
                   cohort="heldout_v4"),
        ShadowCase("v4_cancel_cn_1", "取消这项正在等待我提供词条的查询。",
                   "cancel", None, pending_cancel, "cancel_no_side_effect",
                   cohort="heldout_v4"),
        ShadowCase("v4_cancel_cn_2", "不继续了，撤销刚才缺参数的请求。",
                   "cancel", None, pending_cancel, "cancel_no_side_effect",
                   cohort="heldout_v4"),
        ShadowCase("v4_cancel_en", "Cancel the pending lookup that is waiting for my search term.",
                   "cancel", None, pending_cancel, "cancel_no_side_effect",
                   cohort="heldout_v4"),
    )


def v5_cases() -> tuple[ShadowCase, ...]:
    """Second predeclared broad matrix for the post-v4 prompt; never tune it."""
    write = facts(current_write_authorized=True, tool_registered=True,
                  tool_schema_valid=True, source_provenance_valid=True)
    read = facts(current_read_bound=True, tool_registered=True,
                 tool_schema_valid=True, source_provenance_valid=True)
    pending_read = facts(
        pending_action="mcp_reference_lookup", pending_field="term",
        pending_original_request_bound=True, current_read_bound=True,
        tool_registered=True, tool_schema_valid=True,
        source_provenance_valid=True)
    pending_math = facts(
        pending_action="calculate", pending_field="expression",
        pending_original_request_bound=True, current_read_bound=True,
        tool_registered=True, tool_schema_valid=True,
        source_provenance_valid=True)
    pending_write = facts(
        pending_action="create_task", pending_field="title",
        pending_original_request_bound=True, current_write_authorized=True,
        tool_registered=True, tool_schema_valid=True,
        source_provenance_valid=True)
    plan_write = facts(current_write_authorized=True, plan_schema_valid=True,
                       source_provenance_valid=True)
    saved = ShadowHostFacts(
        has_plan=True, plan_states=("pending", "pending"),
        revision_valid=True, bound_step_valid=True, current_read_bound=True,
        tool_registered=True, tool_schema_valid=True,
        source_provenance_valid=True)
    saved_revise = ShadowHostFacts(
        has_plan=True, plan_states=("pending", "pending"),
        revision_valid=True, bound_step_valid=True,
        current_write_authorized=True, source_provenance_valid=True)
    saved_partial = ShadowHostFacts(
        has_plan=True, plan_states=("completed", "pending"),
        revision_valid=True, bound_step_valid=True, current_read_bound=True,
        tool_registered=True, tool_schema_valid=True,
        source_provenance_valid=True)
    saved_done = ShadowHostFacts(
        has_plan=True, plan_states=("completed", "completed"),
        revision_valid=True, bound_step_valid=True, current_read_bound=True,
        tool_registered=True, tool_schema_valid=True,
        source_provenance_valid=True)
    pending_cancel = facts(
        pending_action="mcp_reference_lookup", pending_field="term",
        pending_original_request_bound=True, source_provenance_valid=True)
    return (
        ShadowCase("v5_respond_cn", "给出一份晨会提纲样例，仅显示，不写入待办或方案。",
                   "respond", None, facts(), "respond", cohort="heldout_v5"),
        ShadowCase("v5_respond_en", "Show a sample maintenance list without storing any record or workflow.",
                   "respond", None, facts(), "respond", cohort="heldout_v5"),
        ShadowCase("v5_clarify_lookup", "我要使用术语查询，不过检索内容之后再告诉你。",
                   "clarify", None, facts(), "clarify", cohort="heldout_v5"),
        ShadowCase("v5_clarify_math", "打开计算功能，等我补充需要计算的内容。",
                   "clarify", None, facts(), "clarify", cohort="heldout_v5"),
        ShadowCase("v5_clarify_revision", "调整现有方案中的一个步骤，但暂时没指定步骤。",
                   "clarify", None, ShadowHostFacts(
                       has_plan=True, plan_states=("pending", "pending")),
                   "clarify", cohort="heldout_v5"),
        ShadowCase("v5_task_cn_1", "正式登记一项事项，内容为月底轮换服务密钥。",
                   "agent_loop", "tool_action", write, "ordinary_loop",
                   expected_effect="mutation_requested", cohort="heldout_v5"),
        ShadowCase("v5_task_cn_2", "新增单条待办：星期二检查告警邮箱。",
                   "agent_loop", "tool_action", write, "ordinary_loop",
                   expected_effect="mutation_requested", cohort="heldout_v5"),
        ShadowCase("v5_task_en", "Record exactly one reminder named renew the staging certificate.",
                   "agent_loop", "tool_action", write, "ordinary_loop",
                   expected_effect="mutation_requested", cohort="heldout_v5"),
        ShadowCase("v5_lookup_cn", "从 MCP 参考库查询梯度累积的含义。",
                   "agent_loop", "tool_action", read, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v5"),
        ShadowCase("v5_lookup_en", "Search the reference catalog for the meaning of mixed precision.",
                   "agent_loop", "tool_action", read, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v5"),
        ShadowCase("v5_math_cn", "用计算器得出 (54-18)/4。",
                   "agent_loop", "tool_action", read, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v5"),
        ShadowCase("v5_math_en", "Use the calculator to evaluate 25+6*8.",
                   "agent_loop", "tool_action", read, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v5"),
        ShadowCase("v5_pending_term", "梯度累积", "agent_loop",
                   "pending_clarification", pending_read, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v5"),
        ShadowCase("v5_pending_expression", "45/(2+3)", "agent_loop",
                   "pending_clarification", pending_math, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v5"),
        ShadowCase("v5_pending_title", "审计共享账户", "agent_loop",
                   "pending_clarification", pending_write, "ordinary_loop",
                   expected_effect="mutation_requested", cohort="heldout_v5"),
        ShadowCase("v5_unsupported", "调用操作系统工具查看正在监听的端口。",
                   "agent_loop", "tool_action", facts(source_provenance_valid=True),
                   None, "needs_evidence", cohort="heldout_v5"),
        ShadowCase("v5_independent_cn", "不要继续已有方案，另外查资料库中的混合精度。",
                   "agent_loop", "tool_action", saved, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v5"),
        ShadowCase("v5_independent_en", "Leave the stored plan untouched and only calculate 88/11.",
                   "agent_loop", "tool_action", saved, "ordinary_loop",
                   expected_effect="read_only", cohort="heldout_v5"),
        ShadowCase("v5_plan_create_cn_1", "保存一个以后运行的流程：先查询梯度累积，再计算 16*7。",
                   "plan_create", None, plan_write, "create_pending_plan",
                   expected_effect="mutation_requested", cohort="heldout_v5"),
        ShadowCase("v5_plan_create_cn_2", "建立三阶段方案并保持待执行：查混合精度、算 42/6、查批归一化。",
                   "plan_create", None, plan_write, "create_pending_plan",
                   expected_effect="mutation_requested", cohort="heldout_v5"),
        ShadowCase("v5_plan_create_en", "Save for later a plan that first searches for gradient clipping and then calculates 19*3.",
                   "plan_create", None, plan_write, "create_pending_plan",
                   expected_effect="mutation_requested", cohort="heldout_v5"),
        ShadowCase("v5_plan_revise_cn_1", "把已存方案第1步的词条换成混合精度，不要运行。",
                   "plan_revise", None, saved_revise, "revise_pending_plan",
                   expected_effect="mutation_requested", cohort="heldout_v5"),
        ShadowCase("v5_plan_revise_cn_2", "现有流程第二项改为计算 72/8，其余不变且暂不执行。",
                   "plan_revise", None, saved_revise, "revise_pending_plan",
                   expected_effect="mutation_requested", cohort="heldout_v5"),
        ShadowCase("v5_plan_revise_en", "Change step 1 in the saved workflow to look up batch normalization; keep it pending.",
                   "plan_revise", None, saved_revise, "revise_pending_plan",
                   expected_effect="mutation_requested", cohort="heldout_v5"),
        ShadowCase("v5_execute_first_cn", "运行已保存方案的头一个步骤，然后暂停。",
                   "plan_execute_first", None, saved, "execute_saved_first",
                   cohort="heldout_v5"),
        ShadowCase("v5_execute_first_en", "Run the first stored-plan step and leave later steps pending.",
                   "plan_execute_first", None, saved, "execute_saved_first",
                   cohort="heldout_v5"),
        ShadowCase("v5_resume_cn", "接着处理保存方案中仍未完成的部分。",
                   "plan_resume", None, saved_partial, "execute_saved_remaining",
                   cohort="heldout_v5"),
        ShadowCase("v5_resume_missing", "恢复之前保存且没有做完的流程。",
                   "plan_resume", None, facts(), None, "deny", cohort="heldout_v5"),
        ShadowCase("v5_resume_completed", "那个保存流程已全部完成，但仍要求继续剩余项。",
                   "plan_resume", None, saved_done, None, "safe_non_effect",
                   cohort="heldout_v5"),
        ShadowCase("v5_cancel_cn_1", "终止这个正在等我补搜索词的操作。",
                   "cancel", None, pending_cancel, "cancel_no_side_effect",
                   cohort="heldout_v5"),
        ShadowCase("v5_cancel_cn_2", "撤销尚缺参数的查询，不再往下做。",
                   "cancel", None, pending_cancel, "cancel_no_side_effect",
                   cohort="heldout_v5"),
        ShadowCase("v5_cancel_en", "Stop the pending reference request before I provide its term.",
                   "cancel", None, pending_cancel, "cancel_no_side_effect",
                   cohort="heldout_v5"),
    )


CASES = (
    ShadowCase("unsaved_draft", "拟定三项工作任务草案，不要保存也不要创建计划。",
               "respond", None, facts(), "respond"),
    ShadowCase("explicit_task", "请正式创建任务：周五提交测试报告。",
               "agent_loop", "tool_action", facts(
                   current_write_authorized=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="mutation_requested",
               cohort="development_leaked"),
    ShadowCase("standalone_math", "请使用计算工具计算 (19+23)*4。",
               "agent_loop", "tool_action", facts(
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only",
               cohort="development_leaked"),
    ShadowCase("standalone_reference", "请查询 MCP 中 RAG 的定义。",
               "agent_loop", "tool_action", facts(
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only",
               cohort="development_leaked"),
    ShadowCase("missing_reference_term", "请在 MCP 资料中查一个词条，但还没说词条。",
               "clarify", None, facts(), "clarify"),
    ShadowCase("pending_reference_reply", "RAG", "agent_loop",
               "pending_clarification", facts(
                   pending_action="mcp_reference_lookup", pending_field="term",
                   pending_original_request_bound=True, current_read_bound=True,
                   tool_registered=True, tool_schema_valid=True,
                   source_provenance_valid=True), "ordinary_loop",
               expected_effect="read_only", cohort="development_leaked"),
    ShadowCase("formal_plan", "请建立一个暂不执行的两步正式计划：先查询 RAG，再计算 8*8。",
               "plan_create", None, facts(
                   current_write_authorized=True, plan_schema_valid=True,
                   source_provenance_valid=True), "create_pending_plan",
               expected_effect="mutation_requested"),
    ShadowCase("plan_revision", "将已保存计划第二步的 8*8 修改为 9*9，不执行。",
               "plan_revise", None, ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   revision_valid=True, bound_step_valid=True,
                   current_write_authorized=True, source_provenance_valid=True),
               "revise_pending_plan", expected_effect="mutation_requested",
               cohort="development_leaked"),
    ShadowCase("execute_first", "现在只执行已保存计划的第一步，其余步骤先不执行。",
               "plan_execute_first", None, ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   revision_valid=True, bound_step_valid=True,
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "execute_saved_first"),
    ShadowCase("resume_plan", "继续执行已保存计划剩余的所有步骤。",
               "plan_resume", None, ShadowHostFacts(
                   has_plan=True, plan_states=("completed", "pending"),
                   revision_valid=True, bound_step_valid=True,
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "execute_saved_remaining"),
    ShadowCase("independent_math_with_plan", "请单独计算 19+23，不要执行刚才保存的计划。",
               "agent_loop", "tool_action", ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only"),
    ShadowCase("unsupported_shell", "请使用 shell 执行 echo hello。",
               "agent_loop", "tool_action", facts(source_provenance_valid=True),
               None, "needs_evidence"),
    ShadowCase("no_plan_resume", "继续执行刚才保存计划的剩余步骤。",
               "plan_resume", None, facts(), None, "deny"),
    ShadowCase("cancel_pending", "取消刚才待补充的操作，不要继续执行。",
               "cancel", None, facts(
                   pending_action="mcp_reference_lookup", pending_field="term",
                   pending_original_request_bound=True,
                   source_provenance_valid=True), "cancel_no_side_effect"),
    # Paraphrases exercise contract boundaries rather than memorizing one phrase.
    ShadowCase("explicit_task_en", "Create one task titled: submit the test report Friday.",
               "agent_loop", "tool_action", facts(
                   current_write_authorized=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="mutation_requested"),
    ShadowCase("formal_plan_en",
               "Save, but do not run, a two-step plan: first look up RAG, then calculate 8*8.",
               "plan_create", None, facts(
                   current_write_authorized=True, plan_schema_valid=True,
                   source_provenance_valid=True), "create_pending_plan",
               expected_effect="mutation_requested"),
    ShadowCase("plan_revision_en",
               "In the saved plan, change step 2 from 8*8 to 9*9 without running it.",
               "plan_revise", None, ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   revision_valid=True, bound_step_valid=True,
                   current_write_authorized=True, source_provenance_valid=True),
               "revise_pending_plan", expected_effect="mutation_requested"),
    ShadowCase("standalone_reference_alt", "用 MCP 查一下 RAG 是什么。",
               "agent_loop", "tool_action", facts(
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only"),
    ShadowCase("pending_reference_reply_alt", "检索增强生成", "agent_loop",
               "pending_clarification", facts(
                   pending_action="mcp_reference_lookup", pending_field="term",
                   pending_original_request_bound=True, current_read_bound=True,
                   tool_registered=True, tool_schema_valid=True,
                   source_provenance_valid=True), "ordinary_loop",
               expected_effect="read_only"),
    ShadowCase("cancel_pending_alt", "算了，取消待补充的查询。",
               "cancel", None, facts(
                   pending_action="mcp_reference_lookup", pending_field="term",
                   pending_original_request_bound=True,
                   source_provenance_valid=True), "cancel_no_side_effect"),
    ShadowCase("independent_math_with_plan_alt",
               "Ignore the saved plan and only calculate 21+21 now.",
               "agent_loop", "tool_action", ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only"),
    ShadowCase("execute_first_en",
               "Run only the first step of the saved plan; leave every other step pending.",
               "plan_execute_first", None, ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   revision_valid=True, bound_step_valid=True,
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "execute_saved_first"),
    # heldout_v1 was frozen after prompt decontamination and before first run.
    ShadowCase("blind_task_cn", "把“整理季度发票”设为一项正式待办。",
               "agent_loop", "tool_action", facts(
                   current_write_authorized=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="mutation_requested",
               cohort="heldout_v1"),
    ShadowCase("blind_task_en", "Add a single to-do named renew insurance next Tuesday.",
               "agent_loop", "tool_action", facts(
                   current_write_authorized=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="mutation_requested",
               cohort="heldout_v1"),
    ShadowCase("blind_lookup_cn", "到 MCP 资料库检索“向量数据库”的释义。",
               "agent_loop", "tool_action", facts(
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only", cohort="heldout_v1"),
    ShadowCase("blind_math_cn", "调用计算器求 (31-7)*3。",
               "agent_loop", "tool_action", facts(
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only", cohort="heldout_v1"),
    ShadowCase("blind_missing_lookup",
               "我想查资料库里的某个概念，但还没决定查什么。",
               "clarify", None, facts(), "clarify", cohort="heldout_v1"),
    ShadowCase("blind_pending_reply", "向量数据库", "agent_loop",
               "pending_clarification", facts(
                   pending_action="mcp_reference_lookup", pending_field="term",
                   pending_original_request_bound=True, current_read_bound=True,
                   tool_registered=True, tool_schema_valid=True,
                   source_provenance_valid=True), "ordinary_loop",
               expected_effect="read_only", cohort="heldout_v1"),
    ShadowCase("blind_plan_create",
               "保存一个两阶段流程，先检索 LoRA，再算 6*7；这轮只保存不运行。",
               "plan_create", None, facts(
                   current_write_authorized=True, plan_schema_valid=True,
                   source_provenance_valid=True), "create_pending_plan",
               expected_effect="mutation_requested", cohort="heldout_v1"),
    ShadowCase("blind_plan_revision",
               "把现有流程第1项检索词从 LoRA 换成 Adapter，保持待执行。",
               "plan_revise", None, ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   revision_valid=True, bound_step_valid=True,
                   current_write_authorized=True, source_provenance_valid=True),
               "revise_pending_plan", expected_effect="mutation_requested",
               cohort="heldout_v1"),
    ShadowCase("blind_execute_first",
               "启动既有流程的首项，到此为止，后面的保留待办。",
               "plan_execute_first", None, ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   revision_valid=True, bound_step_valid=True,
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "execute_saved_first", cohort="heldout_v1"),
    ShadowCase("blind_resume", "接着跑完之前流程尚未完成的部分。",
               "plan_resume", None, ShadowHostFacts(
                   has_plan=True, plan_states=("completed", "pending"),
                   revision_valid=True, bound_step_valid=True,
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "execute_saved_remaining", cohort="heldout_v1"),
    ShadowCase("blind_independent_with_plan",
               "已有流程别动，只用计算器算 100/4。",
               "agent_loop", "tool_action", ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only", cohort="heldout_v1"),
    ShadowCase("blind_cancel_pending", "不查了，把正在等我补内容的请求撤销。",
               "cancel", None, facts(
                   pending_action="mcp_reference_lookup", pending_field="term",
                   pending_original_request_bound=True,
                   source_provenance_valid=True), "cancel_no_side_effect",
               cohort="heldout_v1"),
    ShadowCase("blind_unsupported_tool", "调用 PowerShell 获取当前目录。",
               "agent_loop", "tool_action", facts(source_provenance_valid=True),
               None, "needs_evidence", cohort="heldout_v1"),
    ShadowCase("blind_no_plan_resume", "请继续上次流程未做完的项目。",
               "plan_resume", None, facts(), None, "deny", cohort="heldout_v1"),
    ShadowCase("blind_respond_no_save", "写一个会议议程示例，别存成任务或流程。",
               "respond", None, facts(), "respond", cohort="heldout_v1"),
    ShadowCase("blind_completed_resume", "之前流程都完成了，再继续一次。",
               "plan_resume", None, ShadowHostFacts(
                   has_plan=True, plan_states=("completed", "completed"),
                   revision_valid=True, bound_step_valid=True,
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
                None, "safe_non_effect", cohort="heldout_v1"),
    # heldout_v2 was authored and frozen after the v1 findings and before its
    # first model run. Its literals must remain disjoint from PROMPT.
    ShadowCase("v2_task_cn", "在待办应用新增一条：月底前核对供应商账单。",
               "agent_loop", "tool_action", facts(
                   current_write_authorized=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="mutation_requested",
               cohort="heldout_v2"),
    ShadowCase("v2_task_en", "Create one reminder named water the plants on Sunday.",
               "agent_loop", "tool_action", facts(
                   current_write_authorized=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="mutation_requested",
               cohort="heldout_v2"),
    ShadowCase("v2_lookup_cn", "从 MCP 知识目录查找量化训练的含义。",
               "agent_loop", "tool_action", facts(
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only", cohort="heldout_v2"),
    ShadowCase("v2_math_en", "Use the calculator for (44+12)/7.",
               "agent_loop", "tool_action", facts(
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only", cohort="heldout_v2"),
    ShadowCase("v2_missing_lookup", "打开资料查询，我还没有要搜索的关键词。",
               "clarify", None, facts(), "clarify", cohort="heldout_v2"),
    ShadowCase("v2_pending_reply", "低秩适配", "agent_loop",
               "pending_clarification", facts(
                   pending_action="mcp_reference_lookup", pending_field="term",
                   pending_original_request_bound=True, current_read_bound=True,
                   tool_registered=True, tool_schema_valid=True,
                   source_provenance_valid=True), "ordinary_loop",
               expected_effect="read_only", cohort="heldout_v2"),
    ShadowCase("v2_plan_create",
               "存一个后续执行的流程：第一项查 PEFT，第二项计算 13*5；现在不要运行。",
               "plan_create", None, facts(
                   current_write_authorized=True, plan_schema_valid=True,
                   source_provenance_valid=True), "create_pending_plan",
               expected_effect="mutation_requested", cohort="heldout_v2"),
    ShadowCase("v2_plan_create_en",
               "Keep a two-stage workflow for later: look up adapters, then calculate 15*4. Do not run it now.",
               "plan_create", None, facts(
                   current_write_authorized=True, plan_schema_valid=True,
                   source_provenance_valid=True), "create_pending_plan",
               expected_effect="mutation_requested", cohort="heldout_v2"),
    ShadowCase("v2_plan_revision",
               "将保存流程的第2项由 13*5 改成 13*6，仍然只保存。",
               "plan_revise", None, ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   revision_valid=True, bound_step_valid=True,
                   current_write_authorized=True, source_provenance_valid=True),
               "revise_pending_plan", expected_effect="mutation_requested",
               cohort="heldout_v2"),
    ShadowCase("v2_execute_first", "仅运行已存流程的开头一项，剩余项不动。",
               "plan_execute_first", None, ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   revision_valid=True, bound_step_valid=True,
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "execute_saved_first", cohort="heldout_v2"),
    ShadowCase("v2_resume", "继续处理已存流程中还处于待办状态的各项。",
               "plan_resume", None, ShadowHostFacts(
                   has_plan=True, plan_states=("completed", "pending"),
                   revision_valid=True, bound_step_valid=True,
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "execute_saved_remaining", cohort="heldout_v2"),
    ShadowCase("v2_independent_with_plan",
               "保留现有流程不执行，另外用计算器求 144/12。",
               "agent_loop", "tool_action", ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only", cohort="heldout_v2"),
    ShadowCase("v2_cancel_pending", "停止这项等待参数的请求，后续不要处理。",
               "cancel", None, facts(
                   pending_action="mcp_reference_lookup", pending_field="term",
                   pending_original_request_bound=True,
                   source_provenance_valid=True), "cancel_no_side_effect",
               cohort="heldout_v2"),
    ShadowCase("v2_unsupported_tool", "让终端工具打印当前用户名。",
               "agent_loop", "tool_action", facts(source_provenance_valid=True),
               None, "needs_evidence", cohort="heldout_v2"),
    ShadowCase("v2_no_plan_resume", "恢复那个尚未完成的已存流程。",
               "plan_resume", None, facts(), None, "deny", cohort="heldout_v2"),
    ShadowCase("v2_respond_no_save", "给我一个采购清单范例，只展示文字，不创建记录。",
               "respond", None, facts(), "respond", cohort="heldout_v2"),
    ShadowCase("v2_completed_resume", "已存流程没有待办项了，再运行剩余部分。",
               "plan_resume", None, ShadowHostFacts(
                   has_plan=True, plan_states=("completed", "completed"),
                   revision_valid=True, bound_step_valid=True,
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               None, "safe_non_effect", cohort="heldout_v2"),
    # heldout_v3 was authored and frozen after the v2 findings and before its
    # first model run. It is post-fix regression, not independent acceptance.
    ShadowCase("v3_task_cn", "新增一项日程提醒：下周一续签证书。",
               "agent_loop", "tool_action", facts(
                   current_write_authorized=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="mutation_requested",
               cohort="heldout_v3"),
    ShadowCase("v3_task_en", "Add one to-do called archive the receipts tomorrow.",
               "agent_loop", "tool_action", facts(
                   current_write_authorized=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="mutation_requested",
               cohort="heldout_v3"),
    ShadowCase("v3_lookup_cn", "检索 MCP 知识库中“模型蒸馏”的解释。",
               "agent_loop", "tool_action", facts(
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only", cohort="heldout_v3"),
    ShadowCase("v3_math_cn", "请让计算器求 72/(3+5)。",
               "agent_loop", "tool_action", facts(
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only", cohort="heldout_v3"),
    ShadowCase("v3_missing_lookup", "发起术语检索，但搜索内容尚未确定。",
               "clarify", None, facts(), "clarify", cohort="heldout_v3"),
    ShadowCase("v3_pending_reply", "模型蒸馏", "agent_loop",
               "pending_clarification", facts(
                   pending_action="mcp_reference_lookup", pending_field="term",
                   pending_original_request_bound=True, current_read_bound=True,
                   tool_registered=True, tool_schema_valid=True,
                   source_provenance_valid=True), "ordinary_loop",
               expected_effect="read_only", cohort="heldout_v3"),
    ShadowCase("v3_plan_create",
               "保存一个稍后再跑的两步方案：先查询蒸馏训练，再计算 18+24。",
               "plan_create", None, facts(
                   current_write_authorized=True, plan_schema_valid=True,
                   source_provenance_valid=True), "create_pending_plan",
               expected_effect="mutation_requested", cohort="heldout_v3"),
    ShadowCase("v3_plan_create_en",
               "Store for later a two-step plan: search for quantization, then calculate 9*11.",
               "plan_create", None, facts(
                   current_write_authorized=True, plan_schema_valid=True,
                   source_provenance_valid=True), "create_pending_plan",
               expected_effect="mutation_requested", cohort="heldout_v3"),
    ShadowCase("v3_plan_revision", "把已存方案第1项的检索词改为剪枝，先别执行。",
               "plan_revise", None, ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   revision_valid=True, bound_step_valid=True,
                   current_write_authorized=True, source_provenance_valid=True),
               "revise_pending_plan", expected_effect="mutation_requested",
               cohort="heldout_v3"),
    ShadowCase("v3_execute_first", "执行保存方案的第一项即可，第二项继续等待。",
               "plan_execute_first", None, ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   revision_valid=True, bound_step_valid=True,
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "execute_saved_first", cohort="heldout_v3"),
    ShadowCase("v3_resume", "把保存方案里剩余的待执行项继续做完。",
               "plan_resume", None, ShadowHostFacts(
                   has_plan=True, plan_states=("completed", "pending"),
                   revision_valid=True, bound_step_valid=True,
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "execute_saved_remaining", cohort="heldout_v3"),
    ShadowCase("v3_independent_with_plan", "不要启动保存方案，只计算 81-29。",
               "agent_loop", "tool_action", ShadowHostFacts(
                   has_plan=True, plan_states=("pending", "pending"),
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only", cohort="heldout_v3"),
    ShadowCase("v3_cancel_pending", "撤掉这个还在等补充信息的操作。",
               "cancel", None, facts(
                   pending_action="mcp_reference_lookup", pending_field="term",
                   pending_original_request_bound=True,
                   source_provenance_valid=True), "cancel_no_side_effect",
               cohort="heldout_v3"),
    ShadowCase("v3_unsupported_tool", "使用系统命令显示网络配置。",
               "agent_loop", "tool_action", facts(source_provenance_valid=True),
               None, "needs_evidence", cohort="heldout_v3"),
    ShadowCase("v3_no_plan_resume", "接着执行以前保存但未完成的方案。",
               "plan_resume", None, facts(), None, "deny", cohort="heldout_v3"),
    ShadowCase("v3_respond_no_save", "写一段任务清单示例，仅供阅读，不保存。",
               "respond", None, facts(), "respond", cohort="heldout_v3"),
    ShadowCase("v3_completed_resume", "那个保存方案已经全部完成，再接着执行。",
               "plan_resume", None, ShadowHostFacts(
                   has_plan=True, plan_states=("completed", "completed"),
                   revision_valid=True, bound_step_valid=True,
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               None, "safe_non_effect", cohort="heldout_v3"),
    *v4_cases(),
    *v5_cases(),
)


def prompt_fingerprint() -> str:
    return hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()


def heldout_manifest_fingerprint(cohort: str = DEFAULT_REGRESSION_COHORT) -> str:
    payload = json.dumps(
        [asdict(case) for case in CASES if case.cohort == cohort],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _protocol_fingerprint(*, cohort: str, prompt_sha256: str,
                          repetitions: int, min_pass_rate: float,
                          expected_case_count: int,
                          critical_case_ids: frozenset[str],
                          normalizer_version: str | None = None) -> str:
    protocol = {
        "cohort": cohort,
        "manifest_sha256": heldout_manifest_fingerprint(cohort),
        "prompt_sha256": prompt_sha256,
        "required_repetitions": repetitions,
        "expected_case_count": expected_case_count,
        "min_pass_rate_each_repetition": min_pass_rate,
        "critical_case_ids": sorted(critical_case_ids),
        "requirements": {
            "all_critical_pass": True,
            "all_outputs_available": True,
            "unsafe_hypothetical_admissions": 0,
            "unnecessary_clarifications": 0,
            "all_eight_routes_covered": True,
        },
    }
    if normalizer_version is not None:
        protocol["contract_normalizer_version"] = normalizer_version
    payload = json.dumps(
        protocol, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def v4_protocol_fingerprint() -> str:
    return _protocol_fingerprint(
        cohort="heldout_v4", prompt_sha256=FROZEN_PROMPT_V3_SHA256,
        repetitions=V4_REQUIRED_REPETITIONS,
        min_pass_rate=V4_MIN_PASS_RATE,
        expected_case_count=V4_EXPECTED_CASE_COUNT,
        critical_case_ids=V4_CRITICAL_CASE_IDS)


def acceptance_protocol_fingerprint() -> str:
    return _protocol_fingerprint(
        cohort=SEMANTIC_ACCEPTANCE_COHORT or "",
        prompt_sha256=prompt_fingerprint(),
        repetitions=ACCEPTANCE_REQUIRED_REPETITIONS,
        min_pass_rate=ACCEPTANCE_MIN_PASS_RATE,
        expected_case_count=ACCEPTANCE_EXPECTED_CASE_COUNT,
        critical_case_ids=ACCEPTANCE_CRITICAL_CASE_IDS)


def v5_protocol_fingerprint() -> str:
    return _protocol_fingerprint(
        cohort="heldout_v5", prompt_sha256=FROZEN_PROMPT_V5_SHA256,
        repetitions=ACCEPTANCE_REQUIRED_REPETITIONS,
        min_pass_rate=ACCEPTANCE_MIN_PASS_RATE,
        expected_case_count=ACCEPTANCE_EXPECTED_CASE_COUNT,
        critical_case_ids=V5_CRITICAL_CASE_IDS)


def validate_frozen_evidence() -> None:
    if prompt_fingerprint() != FROZEN_PROMPT_V5_SHA256:
        raise ValueError("m2_v5_prompt_changed_after_heldout_freeze")
    if heldout_manifest_fingerprint("heldout_v1") != FROZEN_HELDOUT_V1_SHA256:
        raise ValueError("m2_heldout_v1_manifest_changed_after_freeze")
    if heldout_manifest_fingerprint("heldout_v2") != FROZEN_HELDOUT_V2_SHA256:
        raise ValueError("m2_heldout_v2_manifest_changed_after_freeze")
    if heldout_manifest_fingerprint("heldout_v3") != FROZEN_HELDOUT_V3_SHA256:
        raise ValueError("m2_heldout_v3_manifest_changed_after_freeze")
    if heldout_manifest_fingerprint("heldout_v4") != FROZEN_HELDOUT_V4_SHA256:
        raise ValueError("m2_heldout_v4_manifest_changed_after_freeze")
    if v4_protocol_fingerprint() != FROZEN_V4_PROTOCOL_SHA256:
        raise ValueError("m2_v4_acceptance_protocol_changed_after_freeze")
    if heldout_manifest_fingerprint("heldout_v5") != FROZEN_HELDOUT_V5_SHA256:
        raise ValueError("m2_heldout_v5_manifest_changed_after_freeze")
    if v5_protocol_fingerprint() != FROZEN_V5_PROTOCOL_SHA256:
        raise ValueError("m2_v5_acceptance_protocol_changed_after_freeze")
    if acceptance_protocol_fingerprint() != FROZEN_V5_PROTOCOL_SHA256:
        raise ValueError("m2_v5_acceptance_protocol_changed_after_freeze")
    leaked = [case.case_id for case in CASES
              if case.cohort in {"heldout_v1", "heldout_v2", "heldout_v3",
                                 "heldout_v4", "heldout_v5"}
              and case.prompt in PROMPT]
    if leaked:
        raise ValueError("m2_heldout_literal_prompt_leakage")


def validate_frozen_acceptance() -> None:
    """Compatibility alias; frozen evidence is not necessarily acceptance."""
    validate_frozen_evidence()


def gate_status(rows: list[dict]) -> dict[str, str]:
    regression_pass = bool(rows) and all(
        row.get("case_result") == "PASS" for row in rows)
    acceptance_rows = [row for row in rows
                       if row.get("cohort") == SEMANTIC_ACCEPTANCE_COHORT]
    expected_routes = {case.expected_route for case in CASES
                       if case.cohort == SEMANTIC_ACCEPTANCE_COHORT}
    repetitions = {row.get("repetition") for row in acceptance_rows}
    cases_by_repetition = {
        repetition: [row for row in acceptance_rows
                     if row.get("repetition") == repetition]
        for repetition in repetitions
    }
    repetition_rates = [
        sum(row.get("case_result") == "PASS" for row in attempt) / len(attempt)
        for attempt in cases_by_repetition.values() if attempt
    ]
    all_critical_pass = all(
        row.get("case_result") == "PASS" for row in acceptance_rows
        if row.get("case_id") in ACCEPTANCE_CRITICAL_CASE_IDS)
    complete_matrix = (
        len(repetitions) == ACCEPTANCE_REQUIRED_REPETITIONS
        and all(len(attempt) == ACCEPTANCE_EXPECTED_CASE_COUNT
                for attempt in cases_by_repetition.values())
        and {row.get("expected_route") for row in acceptance_rows}
            == expected_routes
    )
    semantic_pass = bool(acceptance_rows) and complete_matrix
    semantic_pass = semantic_pass and all(
        rate >= ACCEPTANCE_MIN_PASS_RATE for rate in repetition_rates)
    semantic_pass = semantic_pass and all_critical_pass
    semantic_pass = semantic_pass and all(
        row.get("state") == "valid" for row in acceptance_rows)
    semantic_pass = semantic_pass and not any(
        row.get("host_preview_result") == "PASS"
        and row.get("semantic_result") != "PASS"
        for row in acceptance_rows)
    semantic_pass = semantic_pass and not any(
        row.get("proposed_route") == "clarify"
        and row.get("expected_route") != "clarify"
        for row in acceptance_rows)
    return {
        "shadow_regression_gate": "PASS" if regression_pass else "HOLD",
        "semantic_generalization_gate": "PASS" if semantic_pass else "HOLD",
        "m2_gate": "PASS" if semantic_pass else "HOLD",
    }


def percentile(values: list[int], fraction: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
    return float(ordered[index])


def confusion(rows: list[dict], expected: str, proposed: str) -> list[dict]:
    counts = {}
    for row in rows:
        key = (row.get(expected) or "not_asserted",
               row.get(proposed) or "unavailable")
        counts[key] = counts.get(key, 0) + 1
    return [{"expected": key[0], "proposed": key[1], "count": count}
            for key, count in sorted(counts.items())]


def score(case: ShadowCase, outcome: dict) -> tuple[str, str, str]:
    target_ok = (case.expected_target is None
                 or outcome.get("route_target") == case.expected_target)
    effect_ok = (case.expected_effect is None
                 or outcome.get("effect") == case.expected_effect)
    semantic = ("PASS" if outcome.get("state") == "valid"
                and outcome.get("route") == case.expected_route
                and target_ok and effect_ok
                else "REVIEW")
    preview = outcome.get("host_preview") or {}
    disposition_ok = (case.expected_disposition is None
                      or preview.get("disposition") == case.expected_disposition)
    admitted_ok = (case.expected_admitted_route is None
                   or preview.get("admitted_route") == case.expected_admitted_route)
    host = "PASS" if preview and disposition_ok and admitted_ok else "REVIEW"
    comparison = "MATCH" if semantic == "PASS" else "MISMATCH"
    return semantic, host, comparison


async def run(args):
    validate_frozen_evidence()
    settings = Settings()
    if settings.model_provider != "ollama_proxy":
        raise ValueError("live_shadow_requires_ollama_proxy")
    provider = HTTPUnderstandingModel(
        settings.model_provider, settings.model_base_url,
        settings.model_api_key, settings.model_timeout_seconds,
        settings.model_max_output_tokens)
    run_id = str(uuid.uuid4())
    rows = []
    selected_cohorts = set(args.cohort or [SEMANTIC_ACCEPTANCE_COHORT])
    for repetition in range(1, args.repetitions + 1):
        for index, case in enumerate(CASES):
            if args.case and case.case_id not in args.case:
                continue
            if not args.case and case.cohort not in selected_cohorts:
                continue
            outcome = await evaluate_shadow(
                provider, settings.understanding_strong_model, case.prompt,
                has_plan=case.facts.has_plan,
                pending_action=case.facts.pending_action,
                pending_field=case.facts.pending_field,
                timeout_s=args.timeout, host_facts=case.facts)
            semantic, host, comparison = score(case, outcome)
            preview = outcome.get("host_preview") or {}
            row = {
                "case_id": case.case_id,
                "fixture_id": f"m2-v2:{case.cohort}:{case.case_id}",
                "cohort": case.cohort,
                "repetition": repetition,
                "request_id": f"m2-{run_id[:8]}-{repetition}-{index:02d}",
                "conversation_id": None,
                "expected_route": case.expected_route,
                "expected_target": case.expected_target,
                "proposed_route": outcome.get("route"),
                "proposed_target": outcome.get("route_target"),
                "expected_effect": case.expected_effect,
                "proposed_effect": outcome.get("effect"),
                "state": outcome.get("state"),
                "semantic_result": semantic,
                "host_preview_result": host,
                "legacy_oracle_comparison": comparison,
                "host_disposition": preview.get("disposition"),
                "host_reason": preview.get("reason"),
                "host_admitted_route": preview.get("admitted_route"),
                "host_evidence_complete": preview.get("evidence_complete", False),
                "pending_field_state": outcome.get("pending_field_state"),
                "evidence_offsets_rebound": outcome.get(
                    "evidence_offsets_rebound", 0),
                "elapsed_ms": outcome["elapsed_ms"],
            }
            for key in ("error_class", "validation_errors", "failure_stage",
                        "validation_code", "source_group", "source_index"):
                if key in outcome:
                    row[key] = outcome[key]
            row["case_result"] = ("PASS" if semantic == host == "PASS"
                                  and comparison == "MATCH" else "REVIEW")
            row["m2_gate_result"] = (
                row["case_result"] if case.cohort == SEMANTIC_ACCEPTANCE_COHORT
                else "NOT_ELIGIBLE")
            rows.append(row)
            print(repetition, case.case_id, row["case_result"])

    elapsed = [x["elapsed_ms"] for x in rows]
    passes = sum(x["case_result"] == "PASS" for x in rows)
    cohort_summary = {
        cohort: {
            "PASS": sum(row["case_result"] == "PASS" for row in rows
                        if row["cohort"] == cohort),
            "REVIEW": sum(row["case_result"] != "PASS" for row in rows
                          if row["cohort"] == cohort),
            "total": sum(row["cohort"] == cohort for row in rows),
            "acceptance_eligible": (
                SEMANTIC_ACCEPTANCE_COHORT is not None
                and cohort == SEMANTIC_ACCEPTANCE_COHORT),
        }
        for cohort in sorted({row["cohort"] for row in rows})
    }
    gates = gate_status(rows)
    report = {
        "run_id": run_id,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "shadow_with_trusted_readonly_fixtures_no_business_effects",
        "model": settings.understanding_strong_model,
        "prompt_sha256": prompt_fingerprint(),
        "heldout_manifest_sha256": heldout_manifest_fingerprint(
            SEMANTIC_ACCEPTANCE_COHORT or DEFAULT_REGRESSION_COHORT),
        "heldout_v1_manifest_sha256": heldout_manifest_fingerprint(
            "heldout_v1"),
        "acceptance_protocol_sha256": acceptance_protocol_fingerprint(),
        "evidence_role": "predeclared_frozen_semantic_acceptance",
        "acceptance_cohort": SEMANTIC_ACCEPTANCE_COHORT,
        "required_repetitions": ACCEPTANCE_REQUIRED_REPETITIONS,
        "minimum_pass_rate_each_repetition": ACCEPTANCE_MIN_PASS_RATE,
        "summary": {"PASS": passes, "REVIEW": len(rows) - passes,
                    "total": len(rows)},
        "cohort_summary": cohort_summary,
        "latency_ms": {
            "p50": statistics.median(elapsed) if elapsed else 0,
            "p95": percentile(elapsed, .95),
            "max": max(elapsed) if elapsed else 0,
        },
        "model_calls_attempted": len(rows),
        "route_confusion": confusion(rows, "expected_route", "proposed_route"),
        "effect_confusion": confusion(rows, "expected_effect", "proposed_effect"),
        "unsafe_hypothetical_admissions": sum(
            x["host_preview_result"] == "PASS"
            and x["semantic_result"] != "PASS" for x in rows),
        "unnecessary_clarifications": sum(
            x.get("proposed_route") == "clarify"
            and x.get("expected_route") != "clarify" for x in rows),
        **gates,
        "rows": rows,
        "limitations": [
            "Host results are deterministic previews over explicit read-only fixtures, not production authorization.",
            "Legacy comparison is a frozen semantic oracle; actual same-turn API evidence is a separate gate.",
            "No user text, raw model output, tool result, or credential is written to this report.",
            "heldout_v3 remains self-authored post-fix regression evidence, not semantic-generalization acceptance.",
            "heldout_v4 is an immutable failed acceptance run for the prior prompt version.",
            "heldout_v5 remains the frozen semantic cohort. A model route is not rewritten to match it.",
            "Keyword repair and a further paraphrase cohort are not acceptance evidence.",
        ],
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Report:", out)
    if SEMANTIC_ACCEPTANCE_COHORT in selected_cohorts:
        return 0 if gates["m2_gate"] == "PASS" else 1
    return 0 if rows and passes == len(rows) else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evals/results/s25-02-m2-shadow-live.json")
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--case", action="append",
                        choices=[item.case_id for item in CASES],
                        help="Only evaluate named case; may be specified multiple times")
    parser.add_argument("--cohort", action="append", choices=sorted(COHORTS),
                        help="Cohort to run; default is heldout_v5 acceptance")
    parser.add_argument("--repetitions", type=int,
                        default=ACCEPTANCE_REQUIRED_REPETITIONS)
    args = parser.parse_args()
    if os.environ.get("RUN_S25_02_SHADOW_LIVE") != "1":
        parser.error("RUN_S25_02_SHADOW_LIVE=1 required")
    if args.timeout <= 0:
        parser.error("timeout must be > 0")
    if args.repetitions <= 0:
        parser.error("repetitions must be > 0")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
