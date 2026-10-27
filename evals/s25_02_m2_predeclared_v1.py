"""封存于实跑之前的 M2 句子。不是第三方盲测，跑完不得改题或改提示词。"""
import hashlib
import json
from dataclasses import dataclass

from agent.route_shadow_host import ShadowHostFacts


@dataclass(frozen=True)
class PredeclaredCase:
    case_id: str
    prompt: str
    expected_object: str
    expected_operation: str
    expected_disposition: str
    facts: ShadowHostFacts
    critical: bool = False


def _plan(*, revision_valid=False):
    return ShadowHostFacts(
        has_plan=True, plan_states=("pending", "pending"),
        revision_valid=revision_valid,
    )


def _pending(tool_name, field_name):
    return ShadowHostFacts(
        has_plan=False, pending_action=tool_name, pending_field=field_name,
        pending_original_request_bound=True,
    )


CASES = (
    PredeclaredCase(
        "pd_draft_cn", "只在这次聊天里写一段展会欢迎词，不要留下记录。",
        "none", "answer", "safe_non_effect", ShadowHostFacts(has_plan=False)),
    PredeclaredCase(
        "pd_draft_en",
        "Write a welcome note for the booth in this conversation and do not keep a record.",
        "none", "answer", "safe_non_effect", ShadowHostFacts(has_plan=False)),
    PredeclaredCase(
        "pd_clarify_cn", "我想处理一下手头的安排，但还没说是哪一件，请先向我确认对象。",
        "none", "clarify", "safe_non_effect", ShadowHostFacts(has_plan=False), True),
    PredeclaredCase(
        "pd_clarify_en",
        "I want to handle an item, but I have not said which one. Ask me which item before doing anything.",
        "none", "clarify", "safe_non_effect", ShadowHostFacts(has_plan=False)),
    PredeclaredCase(
        "pd_task_cn", "把更换车库门禁这件事保存成一条以后能查到的事项。",
        "task", "create", "needs_evidence", ShadowHostFacts(has_plan=False), True),
    PredeclaredCase(
        "pd_task_en",
        "Save one lookup item named replace the garage pass. It is a single reminder, not a procedure.",
        "task", "create", "needs_evidence", ShadowHostFacts(has_plan=False), True),
    PredeclaredCase(
        "pd_plan_cn", "建立一个以后再跑的顺序流程，包含核对样品和联系实验室两段，此刻不启动。",
        "plan", "create", "deny", ShadowHostFacts(has_plan=False), True),
    PredeclaredCase(
        "pd_plan_en",
        "Store a later procedure with two ordered parts, check samples then contact the lab, and do not start it now.",
        "plan", "create", "deny", ShadowHostFacts(has_plan=False), True),
    PredeclaredCase(
        "pd_revise_cn", "把存档流程的第4步说明换成夜间窗口，不要运行。",
        "plan_step", "revise", "needs_evidence", _plan(), True),
    PredeclaredCase(
        "pd_revise_en", "Replace the wording of stored procedure item 4 with the night window, and do not run it.",
        "plan_step", "revise", "needs_evidence", _plan(), True),
    PredeclaredCase(
        "pd_revise_missing_cn", "存档流程里有一处说明要换掉，但我还没选定是哪一处，请先问我。",
        "none", "clarify", "safe_non_effect", _plan(), True),
    PredeclaredCase(
        "pd_first_cn", "已保存的配送流程只跑开头那一段，后面的段落先停着。",
        "plan", "execute_first", "needs_evidence", _plan(), True),
    PredeclaredCase(
        "pd_first_en",
        "Run only the opening segment of the stored delivery procedure and leave every later segment idle.",
        "plan", "execute_first", "needs_evidence", _plan(), True),
    PredeclaredCase(
        "pd_resume_cn", "配送流程前面的段落已经做完，请继续它尚未完成的后续段落。",
        "plan", "resume", "needs_evidence", _plan(), True),
    PredeclaredCase(
        "pd_resume_en",
        "The earlier segment of the delivery procedure is finished. Continue its unfinished later segments.",
        "plan", "resume", "needs_evidence", _plan(), True),
    PredeclaredCase(
        "pd_resume_absent_cn", "把上次那份巡检流程里还没做的部分接着做完。",
        "plan", "resume", "deny", ShadowHostFacts(has_plan=False), True),
    PredeclaredCase(
        "pd_resume_absent_en",
        "Continue the unfinished portion of the previous inspection procedure.",
        "plan", "resume", "deny", ShadowHostFacts(has_plan=False), True),
    PredeclaredCase(
        "pd_tool_cn", "用计算器只求 90 减 17，别碰已经保存的流程。",
        "tool", "invoke", "needs_evidence", _plan(), True),
    PredeclaredCase(
        "pd_tool_en",
        "Use the calculator only for 90 minus 17 and leave any stored procedure untouched.",
        "tool", "invoke", "needs_evidence", _plan(), True),
    PredeclaredCase(
        "pd_pending_cn", "补充的检索词是温室气体。",
        "pending_slot", "invoke", "needs_evidence",
        _pending("mcp_reference_lookup", "term"), True),
    PredeclaredCase(
        "pd_pending_new_cn", "那个检索先别填，另外求 8 乘 15。",
        "tool", "invoke", "needs_evidence",
        _pending("mcp_reference_lookup", "term"), True),
    PredeclaredCase(
        "pd_pending_unknown_cn", "缺少的命令参数是 shutdown。",
        "pending_slot", "invoke", "needs_evidence",
        _pending("shell", "command"), True),
    PredeclaredCase(
        "pd_lookup_cn", "在参考库里查一下温室气体的释义。",
        "tool", "invoke", "needs_evidence", ShadowHostFacts(has_plan=False)),
    PredeclaredCase(
        "pd_draft_pending_cn", "检索先暂停，只要一段展台介绍文字，不要保存。",
        "none", "answer", "safe_non_effect",
        _pending("mcp_reference_lookup", "term")),
)

CRITICAL_CASE_IDS = frozenset(case.case_id for case in CASES if case.critical)
MIN_PASS_RATE = 0.95
REQUIRED_REPETITIONS = 2

# 占位。用例正文冻结后写入，实跑前由测试核对。
FROZEN_MANIFEST_SHA256 = "17b7be06f7a75652253e9237ba61d761238571b2737f813365faff3967e51a62"


def manifest_payload():
    """只覆盖题面和期望，不包含模型输出。"""
    return [
        {
            "case_id": case.case_id,
            "prompt": case.prompt,
            "expected_object": case.expected_object,
            "expected_operation": case.expected_operation,
            "expected_disposition": case.expected_disposition,
            "critical": case.critical,
            "has_plan": case.facts.has_plan,
            "revision_valid": case.facts.revision_valid,
            "pending_action": case.facts.pending_action,
            "pending_field": case.facts.pending_field,
            "pending_original_request_bound": case.facts.pending_original_request_bound,
        }
        for case in CASES
    ]


def manifest_sha256() -> str:
    raw = json.dumps(manifest_payload(), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def assert_manifest_frozen() -> None:
    if manifest_sha256() != FROZEN_MANIFEST_SHA256:
        raise RuntimeError("predeclared_manifest_hash_mismatch")


def score_gate(rows: list[dict]) -> dict:
    """两轮分别达到阈值，且关键句两轮都通过。不降低门槛。"""
    repetitions = sorted({row["repetition"] for row in rows})
    per_repetition = []
    failed = []
    for repetition in repetitions:
        group = [row for row in rows if row["repetition"] == repetition]
        passed = [row for row in group if row["case_pass"]]
        rate = len(passed) / len(group) if group else 0
        per_repetition.append({
            "repetition": repetition, "total": len(group),
            "passed": len(passed), "pass_rate": rate,
        })
        failed.extend(
            row["case_id"] for row in group if not row["case_pass"])
    critical_ok = all(
        row["case_pass"] for row in rows if row["case_id"] in CRITICAL_CASE_IDS)
    no_unwanted_admission = all(
        row["disposition"] != "potentially_admissible" for row in rows)
    no_authority = all(row["host_authorization"] is False for row in rows)
    rate_ok = (
        len(per_repetition) >= REQUIRED_REPETITIONS
        and all(item["pass_rate"] >= MIN_PASS_RATE for item in per_repetition)
    )
    passed_gate = bool(rate_ok and critical_ok and no_unwanted_admission and no_authority)
    return {
        "predeclared_gate": "PASS" if passed_gate else "HOLD",
        "m2_gate": "HOLD",
        "m3_cutover": "NOT_AUTHORIZED",
        "cohort": "predeclared_v1_not_third_party",
        "per_repetition": per_repetition,
        "critical_ok": critical_ok,
        "failed_case_ids": sorted(set(failed)),
        "limitations": [
            "同一作者在看到分类提示词之后封存的句子，不是第三方盲测。",
            "通过只说明这组封存句子达到阈值，不授权生产切换。",
            "失败后不得把句子或提示词改到通过。",
        ],
    }
