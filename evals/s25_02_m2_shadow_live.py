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
ACCEPTANCE_COHORT = "heldout_v3"
COHORTS = {"development_leaked", "tuned_regression", "heldout_v1",
           "heldout_v2", ACCEPTANCE_COHORT}


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
    # first model run. It replaces v2 as the sole acceptance cohort.
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
)


def prompt_fingerprint() -> str:
    return hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()


def heldout_manifest_fingerprint(cohort: str = ACCEPTANCE_COHORT) -> str:
    payload = json.dumps(
        [asdict(case) for case in CASES if case.cohort == cohort],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_frozen_acceptance() -> None:
    if prompt_fingerprint() != FROZEN_PROMPT_V3_SHA256:
        raise ValueError("m2_v3_prompt_changed_after_heldout_freeze")
    if heldout_manifest_fingerprint("heldout_v1") != FROZEN_HELDOUT_V1_SHA256:
        raise ValueError("m2_heldout_v1_manifest_changed_after_freeze")
    if heldout_manifest_fingerprint("heldout_v2") != FROZEN_HELDOUT_V2_SHA256:
        raise ValueError("m2_heldout_v2_manifest_changed_after_freeze")
    if heldout_manifest_fingerprint("heldout_v3") != FROZEN_HELDOUT_V3_SHA256:
        raise ValueError("m2_heldout_v3_manifest_changed_after_freeze")
    leaked = [case.case_id for case in CASES
              if case.cohort in {"heldout_v1", "heldout_v2", "heldout_v3"}
              and case.prompt in PROMPT]
    if leaked:
        raise ValueError("m2_heldout_literal_prompt_leakage")


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
    validate_frozen_acceptance()
    settings = Settings()
    if settings.model_provider != "ollama_proxy":
        raise ValueError("live_shadow_requires_ollama_proxy")
    provider = HTTPUnderstandingModel(
        settings.model_provider, settings.model_base_url,
        settings.model_api_key, settings.model_timeout_seconds,
        settings.model_max_output_tokens)
    run_id = str(uuid.uuid4())
    rows = []
    selected_cohorts = set(args.cohort or [ACCEPTANCE_COHORT])
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
            "request_id": f"m2-{run_id[:8]}-{index:02d}",
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
        row["m2_gate_result"] = ("PASS" if semantic == host == "PASS"
                                 and comparison == "MATCH" else "HOLD")
        rows.append(row)
        print(case.case_id, row["m2_gate_result"])

    elapsed = [x["elapsed_ms"] for x in rows]
    passes = sum(x["m2_gate_result"] == "PASS" for x in rows)
    heldout_rows = [row for row in rows
                    if row["cohort"] == ACCEPTANCE_COHORT]
    heldout_passes = sum(row["m2_gate_result"] == "PASS"
                         for row in heldout_rows)
    cohort_summary = {
        cohort: {
            "PASS": sum(row["m2_gate_result"] == "PASS" for row in rows
                        if row["cohort"] == cohort),
            "REVIEW": sum(row["m2_gate_result"] != "PASS" for row in rows
                          if row["cohort"] == cohort),
            "total": sum(row["cohort"] == cohort for row in rows),
            "acceptance_eligible": cohort == ACCEPTANCE_COHORT,
        }
        for cohort in sorted({row["cohort"] for row in rows})
    }
    gate = ("PASS" if heldout_rows and heldout_passes == len(heldout_rows)
            else "HOLD" if heldout_rows else "NOT_APPLICABLE")
    report = {
        "run_id": run_id,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "shadow_with_trusted_readonly_fixtures_no_business_effects",
        "model": settings.understanding_strong_model,
        "prompt_sha256": prompt_fingerprint(),
        "heldout_manifest_sha256": heldout_manifest_fingerprint(
            ACCEPTANCE_COHORT),
        "heldout_v1_manifest_sha256": heldout_manifest_fingerprint(
            "heldout_v1"),
        "acceptance_cohort": ACCEPTANCE_COHORT,
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
        "m2_gate": gate,
        "rows": rows,
        "limitations": [
            "Host results are deterministic previews over explicit read-only fixtures, not production authorization.",
            "Legacy comparison is a frozen semantic oracle; actual same-turn API evidence is a separate gate.",
            "No user text, raw model output, tool result, or credential is written to this report.",
            "Only heldout_v3 rows count toward the M2 gate; earlier cohorts remain historical or regression evidence.",
        ],
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Report:", out)
    return 0 if rows and passes == len(rows) and gate != "HOLD" else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evals/results/s25-02-m2-shadow-live.json")
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--case", action="append",
                        choices=[item.case_id for item in CASES],
                        help="Only evaluate named case; may be specified multiple times")
    parser.add_argument("--cohort", action="append", choices=sorted(COHORTS),
                        help="Cohort to run; default is heldout_v3 only")
    args = parser.parse_args()
    if os.environ.get("RUN_S25_02_SHADOW_LIVE") != "1":
        parser.error("RUN_S25_02_SHADOW_LIVE=1 required")
    if args.timeout <= 0:
        parser.error("timeout must be > 0")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
