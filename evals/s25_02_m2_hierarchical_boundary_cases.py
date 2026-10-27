"""Holdout-style semantic boundary cohort, independent of 22 development prompts.

Curated before model runs; not a sealed third-party blind benchmark.
All Host facts are synthetic and grant no execution authority.
"""
from dataclasses import dataclass

from agent.route_shadow_host import ShadowHostFacts


@dataclass(frozen=True)
class BoundaryCase:
    case_id: str
    prompt: str
    expected_object: str
    expected_operation: str
    facts: ShadowHostFacts


def host(*, plan=False, pending=False):
    return ShadowHostFacts(
        has_plan=plan,
        plan_states=("completed", "pending") if plan else (),
        pending_action="mcp_reference_lookup" if pending else None,
        pending_field="term" if pending else None,
        pending_original_request_bound=pending,
    )


CASES = (
    BoundaryCase("draft_email", "帮我写一封发给导师的邮件草稿，只在聊天中给我文字。", "none", "answer", host()),
    BoundaryCase("rewrite_text", "把这段说明写得更专业，直接输出修改稿，不要创建任何记录。", "none", "answer", host()),
    BoundaryCase("generate_outline", "Give me a five-point outline in chat, without saving a task or plan.", "none", "answer", host()),
    BoundaryCase("draft_with_plan", "先别管已保存的计划，帮我草拟一段项目介绍，不要保存。", "none", "answer", host(plan=True)),
    BoundaryCase("persist_task", "把周三交材料登记成一条待办事项，后续能查到。", "task", "create", host()),
    BoundaryCase("save_plan", "保存一个两阶段操作计划，今天不运行。", "plan", "create", host()),
    BoundaryCase("modify_step", "把已存计划第二步的查询内容改成新版本，不执行。", "plan_step", "revise", host(plan=True)),
    BoundaryCase("start_first", "这个已存方案从第一阶段开始执行，其他阶段暂缓。", "plan", "execute_first", host(plan=True)),
    BoundaryCase("resume_rest", "前面一项已经完成了，接着做剩下的环节。", "plan", "resume", host(plan=True)),
    BoundaryCase("resume_missing", "把上次计划中未做完的步骤继续运行。", "plan", "resume", host()),
    BoundaryCase("tool_unavailable", "用尚未接入的终端工具读取当前工作目录。", "tool", "invoke", host()),
    BoundaryCase("new_tool_with_plan", "不用现有计划，单独帮我用计算器求 17 乘 29。", "tool", "invoke", host(plan=True)),
    BoundaryCase("pending_supply", "词条是大语言模型。", "pending_slot", "invoke", host(pending=True)),
    BoundaryCase("pending_cancel", "上一个等待填词条的请求作废。", "pending_slot", "cancel", host(pending=True)),
    BoundaryCase("pending_independent", "先放着那个词条查询，现在单独算 43+12。", "tool", "invoke", host(pending=True)),
    BoundaryCase("pending_draft", "查询先放着，给我拟一段自我介绍就好，不保存。", "none", "answer", host(pending=True)),
    BoundaryCase("no_pending_new_lookup", "去知识库里检索一下模型上下文窗口。", "tool", "invoke", host()),
    BoundaryCase("ask_clarification", "我希望继续，但还没有说明到底是哪项事情，先问我具体目标。", "none", "clarify", host()),
)
