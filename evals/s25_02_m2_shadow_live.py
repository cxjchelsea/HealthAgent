"""M2 live semantic shadow matrix. No Conversation API, tools, or database writes.

Requires local Ollama and RUN_S25_02_SHADOW_LIVE=1. Stores bounded diagnostics
only; case texts are defined here, but not emitted to output reports.
"""
import argparse
import asyncio
import json
import os
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent.config import Settings
from agent.route_shadow import evaluate_shadow
from agent.understanding import HTTPUnderstandingModel

# Saved plan and pending-action states are frozen hypothetical inputs, not writes.
CASES = (
    ("unsaved_draft", "拟定三项工作任务草案，不要保存也不要创建计划。", False, None, "respond"),
    ("explicit_task", "请正式创建任务：周五提交测试报告。", False, None, "agent_loop"),
    ("standalone_math", "请使用计算工具计算 (19+23)*4。", False, None, "agent_loop"),
    ("standalone_reference", "请查询 MCP 中 RAG 的定义。", False, None, "agent_loop"),
    ("missing_reference_term", "请在 MCP 资料中查一个词条，但还没说词条。", False, None, "clarify"),
    ("pending_reference_reply", "RAG", False, "mcp_reference_lookup", "agent_loop"),
    ("formal_plan", "请建立一个暂不执行的两步正式计划：先查询 RAG，再计算 8*8。", False, None, "plan_create"),
    ("plan_revision", "将已保存计划第二步的 8*8 修改为 9*9，不执行。", True, None, "plan_revise"),
    ("execute_first", "现在只执行已保存计划的第一步，其余步骤先不执行。", True, None, "plan_execute_first"),
    ("resume_plan", "继续执行已保存计划剩余的所有步骤。", True, None, "plan_resume"),
    ("independent_math_with_plan", "请单独计算 19+23，不要执行刚才保存的计划。", True, None, "agent_loop"),
    ("unsupported_shell", "请使用 shell 执行 echo hello。", False, None, "agent_loop"),
    ("no_plan_resume", "继续执行刚才保存计划的剩余步骤。", False, None, "plan_resume"),
    ("cancel_pending", "取消刚才待补充的操作，不要继续执行。", False, "mcp_reference_lookup", "cancel"),
)


async def run(args):
    settings = Settings()
    if settings.model_provider != "ollama_proxy":
        raise ValueError("live_shadow_requires_ollama_proxy")
    provider = HTTPUnderstandingModel(
        settings.model_provider, settings.model_base_url,
        settings.model_api_key, settings.model_timeout_seconds,
        settings.model_max_output_tokens)
    rows = []
    for cid, prompt, has_plan, pending, expected in CASES:
        if args.case and cid not in args.case:
            continue
        outcome = await evaluate_shadow(
            provider, settings.understanding_strong_model, prompt,
            has_plan=has_plan, pending_action=pending, timeout_s=args.timeout)
        # A correct primary route with the wrong binding must not be PASS.
        expected_target = ("pending_clarification" if cid == "pending_reference_reply"
                           else "tool_action" if cid in {
                               "explicit_task", "standalone_math",
                               "standalone_reference", "independent_math_with_plan",
                               "unsupported_shell"} else None)
        observed_target = outcome.get("route_target")
        target_ok = expected_target is None or observed_target == expected_target
        if cid == "independent_math_with_plan":
            target_ok = observed_target == "tool_action"
        row = {"case_id":cid,"expected_route":expected,
               "expected_target":expected_target,
               "proposed_target":observed_target,
               "state":outcome.get("state"),
               "proposed_route":outcome.get("route"),
               "host_would_deny":outcome.get("host_would_deny"),
               "elapsed_ms":outcome["elapsed_ms"],
               "result":("PASS" if outcome.get("state")=="valid"
                         and outcome.get("route")==expected and target_ok else "REVIEW")}
        if outcome.get("error_class"):
            row["error_class"] = outcome["error_class"]
        if outcome.get("validation_errors"):
            row["validation_errors"] = outcome["validation_errors"]
        if outcome.get("failure_stage"):
            row["failure_stage"] = outcome["failure_stage"]
        if outcome.get("validation_code"):
            row["validation_code"] = outcome["validation_code"]
        # Semantic PASS only. The shadow has no authoritative Host snapshot,
        # so it cannot establish any execution permission or final admission.
        row["semantic_result"] = row["result"]
        row["host_admission_result"] = "NOT_VERIFIED"
        row["m2_gate_result"] = "HOLD"
        if "source_group" in outcome:
            row["source_group"] = outcome["source_group"]
            row["source_index"] = outcome["source_index"]
        rows.append(row)
        print(cid,row["semantic_result"])
    elapsed = [x["elapsed_ms"] for x in rows]
    report = {
        "run_id":str(uuid.uuid4()),"started_utc":datetime.now(timezone.utc).isoformat(),
        "mode":"shadow_semantic_only_no_api_or_side_effects",
        "model":settings.understanding_strong_model,
        "summary":{"PASS":sum(x["result"]=="PASS" for x in rows),
                   "REVIEW":sum(x["result"]=="REVIEW" for x in rows),
                   "total":len(rows)},
        "latency_ms":{"median":statistics.median(elapsed),
                      "max":max(elapsed)},
        "model_calls_attempted":len(rows),
        "host_admission_summary":{"verified":0,"not_verified":len(rows)},
        "m2_gate":"HOLD",
        "result_semantics":"PASS means semantic route/target match ONLY; never execution or Host admission",
        "rows":rows,
        "limitations":[
            "Semantic proposals are neither execution authority nor verified tool results.",
            "This suite has expected semantic routes; it does not compare full legacy execution.",
            "Local model calls may fail or differ between runs.",
            "M2 Host matrix requires separately supplied trusted state and tool evidence."
        ]}
    out = Path(args.output)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Report:",out)
    return 0 if report["summary"]["REVIEW"] == 0 else 1


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",default="evals/results/s25-02-m2-shadow-live.json")
    parser.add_argument("--timeout",type=float,default=45)
    parser.add_argument("--case",action="append",choices=[item[0] for item in CASES],
                        help="Only evaluate named case; may be specified multiple times")
    args=parser.parse_args()
    if os.environ.get("RUN_S25_02_SHADOW_LIVE")!="1":
        parser.error("RUN_S25_02_SHADOW_LIVE=1 required")
    if args.timeout <= 0:
        parser.error("timeout must be > 0")
    raise SystemExit(asyncio.run(run(args)))


if __name__=="__main__":
    main()
