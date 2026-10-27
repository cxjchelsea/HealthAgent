"""M2 real-model semantic shadow matrix with trusted, read-only Host fixtures.

This never calls the Conversation API, tools, or stores. The fixtures describe
Host facts that a later cutover must verify; they grant no production authority.
Reports contain stable case/request identities and no prompt text.
"""
import argparse
import asyncio
import json
import os
import statistics
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent.config import Settings
from agent.route_shadow import evaluate_shadow
from agent.route_shadow_host import ShadowHostFacts
from agent.understanding import HTTPUnderstandingModel


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


def facts(**kwargs) -> ShadowHostFacts:
    return ShadowHostFacts(has_plan=False, **kwargs)


CASES = (
    ShadowCase("unsaved_draft", "拟定三项工作任务草案，不要保存也不要创建计划。",
               "respond", None, facts(), "respond"),
    ShadowCase("explicit_task", "请正式创建任务：周五提交测试报告。",
               "agent_loop", "tool_action", facts(
                   current_write_authorized=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="mutation_requested"),
    ShadowCase("standalone_math", "请使用计算工具计算 (19+23)*4。",
               "agent_loop", "tool_action", facts(
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only"),
    ShadowCase("standalone_reference", "请查询 MCP 中 RAG 的定义。",
               "agent_loop", "tool_action", facts(
                   current_read_bound=True, tool_registered=True,
                   tool_schema_valid=True, source_provenance_valid=True),
               "ordinary_loop", expected_effect="read_only"),
    ShadowCase("missing_reference_term", "请在 MCP 资料中查一个词条，但还没说词条。",
               "clarify", None, facts(), "clarify"),
    ShadowCase("pending_reference_reply", "RAG", "agent_loop",
               "pending_clarification", facts(
                   pending_action="mcp_reference_lookup", pending_field="term",
                   pending_original_request_bound=True, current_read_bound=True,
                   tool_registered=True, tool_schema_valid=True,
                   source_provenance_valid=True), "ordinary_loop",
               expected_effect="read_only"),
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
               "revise_pending_plan", expected_effect="mutation_requested"),
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
)


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
    settings = Settings()
    if settings.model_provider != "ollama_proxy":
        raise ValueError("live_shadow_requires_ollama_proxy")
    provider = HTTPUnderstandingModel(
        settings.model_provider, settings.model_base_url,
        settings.model_api_key, settings.model_timeout_seconds,
        settings.model_max_output_tokens)
    run_id = str(uuid.uuid4())
    rows = []
    for index, case in enumerate(CASES):
        if args.case and case.case_id not in args.case:
            continue
        outcome = await evaluate_shadow(
            provider, settings.understanding_strong_model, case.prompt,
            has_plan=case.facts.has_plan,
            pending_action=case.facts.pending_action,
            pending_field=case.facts.pending_field,
            timeout_s=args.timeout, host_facts=case.facts)
        semantic, host, comparison = score(case, outcome)
        typed = (outcome if outcome.get("state") == "valid"
                 else outcome.get("semantic_proposal") or {})
        semantic_schema_valid = bool(
            outcome.get("state") == "valid"
            or outcome.get("semantic_schema_valid"))
        semantic_route_correct = (typed.get("route") == case.expected_route
                                  and (case.expected_target is None
                                       or typed.get("route_target") == case.expected_target)
                                  and (case.expected_effect is None
                                       or typed.get("effect") == case.expected_effect))
        preview = outcome.get("host_preview") or {}
        row = {
            "case_id": case.case_id,
            "fixture_id": f"m2-v2:{case.case_id}",
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
            "semantic_schema_valid": semantic_schema_valid,
            "semantic_route_correct": semantic_route_correct,
            "provenance_status": ("PASS" if outcome.get("state") == "valid"
                                  else outcome.get("provenance_status", "UNAVAILABLE")),
            "host_preview_result": host,
            "legacy_oracle_comparison": comparison,
            "host_disposition": preview.get("disposition"),
            "host_reason": preview.get("reason"),
            "host_admitted_route": preview.get("admitted_route"),
            "host_evidence_complete": preview.get("evidence_complete", False),
            "pending_field_state": outcome.get("pending_field_state"),
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
    report = {
        "run_id": run_id,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "shadow_with_trusted_readonly_fixtures_no_business_effects",
        "model": settings.understanding_strong_model,
        "summary": {"PASS": passes, "REVIEW": len(rows) - passes,
                    "total": len(rows)},
        "latency_ms": {
            "p50": statistics.median(elapsed) if elapsed else 0,
            "p95": percentile(elapsed, .95),
            "max": max(elapsed) if elapsed else 0,
        },
        "model_calls_attempted": len(rows),
        "diagnostic_dimensions": {
            "schema_valid": sum(x["semantic_schema_valid"] for x in rows),
            "semantic_correct_after_schema": sum(
                x["semantic_route_correct"] for x in rows),
            "provenance_pass": sum(
                x["provenance_status"] == "PASS" for x in rows),
            "host_preview_available": sum(
                x["host_disposition"] is not None for x in rows),
        },
        "route_confusion": confusion(rows, "expected_route", "proposed_route"),
        "effect_confusion": confusion(rows, "expected_effect", "proposed_effect"),
        "unsafe_hypothetical_admissions": sum(
            x["host_preview_result"] == "PASS"
            and x["semantic_result"] != "PASS" for x in rows),
        "unnecessary_clarifications": sum(
            x.get("proposed_route") == "clarify"
            and x.get("expected_route") != "clarify" for x in rows),
        "m2_gate": "PASS" if rows and passes == len(rows) else "HOLD",
        "rows": rows,
        "limitations": [
            "Host results are deterministic previews over explicit read-only fixtures, not production authorization.",
            "Legacy comparison is a frozen semantic oracle; actual same-turn API evidence is a separate gate.",
            "No user text, raw model output, tool result, or credential is written to this report.",
        ],
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Report:", out)
    return 0 if report["m2_gate"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evals/results/s25-02-m2-shadow-live.json")
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--case", action="append",
                        choices=[item.case_id for item in CASES],
                        help="Only evaluate named case; may be specified multiple times")
    args = parser.parse_args()
    if os.environ.get("RUN_S25_02_SHADOW_LIVE") != "1":
        parser.error("RUN_S25_02_SHADOW_LIVE=1 required")
    if args.timeout <= 0:
        parser.error("timeout must be > 0")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
