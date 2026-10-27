"""M2 structured read-only E2E: frozen core owns route; model extracts quotes only.

Positive evidence is explicitly SYNTHETIC; never replay or authorize actions.
"""
import argparse
import asyncio
import json
import os
import re
from dataclasses import asdict, replace
from pathlib import Path

from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from agent.route_semantic_hierarchical import classify_hierarchical_diagnostic
from agent.route_semantic_preview import ROUTE_BY_CORE
from agent.route_semantic_evidence import verify_core_evidence
from agent.route_shadow import ShadowRoute, QuoteEvidence
from evals.s25_02_m2_plan_step_quotes import collect_ordered_step_quotes
from agent.route_shadow_host import ShadowHostFacts, trusted_tool_effect
from evals.s25_02_m2_hierarchical_boundary_cases import CASES
from evals.s25_02_m2_semantic_evidence_live import (
    SourceQuotes, QUOTE_PROMPT,
)

SCENARIOS = ("start_first", "modify_step", "pending_supply",
             "pending_independent", "resume_missing", "tool_unavailable")
POSITIVE = {"start_first", "modify_step", "pending_supply", "pending_independent"}


def facts_for(case, mode):
    """Only synthetic positive fixture differs from original negative facts."""
    base = case.facts
    if mode == "boundary":
        return base
    if case.case_id == "start_first":
        return replace(base, plan_states=("pending", "pending"),
                       revision_valid=True, bound_step_valid=True,
                       current_read_bound=True, source_provenance_valid=True,
                       tool_registered=True, tool_schema_valid=True)
    if case.case_id == "modify_step":
        return replace(base, revision_valid=True, bound_step_valid=True,
                       current_write_authorized=True, source_provenance_valid=True)
    if case.case_id == "pending_supply":
        return replace(base, source_provenance_valid=True,
                       tool_registered=True, tool_schema_valid=True)
    if case.case_id == "pending_independent":
        return replace(base, current_read_bound=True,
                       source_provenance_valid=True,
                       tool_registered=True, tool_schema_valid=True)
    return base


def compiled_effect(core, route, facts):
    """计划对象的持久效果可确定。工具效果只跟随宿主已绑定的工具名。"""
    if route in {"plan_create", "plan_revise"} or core.object_type == "task":
        return "mutation_requested"
    if route in {"plan_execute_first", "plan_resume"}:
        return "read_only"
    if core.object_type == "pending_slot" and core.operation == "invoke":
        tool_name = None if facts is None else facts.pending_action
        return trusted_tool_effect(tool_name) or "none"
    return "none"


def proposal_from_core(core, message, quote, step_quote, facts=None, plan_steps=None):
    """Construct one consistent route mechanically; neither quote changes it."""
    pair = (core.object_type, core.operation)
    route = ROUTE_BY_CORE.get(pair)
    if route is None:
        raise ValueError("invalid_core_pair")
    targets = {
        "respond": "none", "clarify": "none",
        "plan_create": "none",
        "plan_execute_first": "saved_plan",
        "plan_resume": "saved_plan", "plan_revise": "saved_plan",
    }
    target = targets.get(route, "none")
    if route == "agent_loop":
        target = "pending_clarification" if core.object_type == "pending_slot" else "tool_action"
    if route == "cancel":
        target = "pending_clarification" if core.object_type == "pending_slot" else "saved_plan"
    scope = {
        "plan_execute_first": "first_step", "plan_resume": "remaining_steps",
        "agent_loop": "single_tool",
    }.get(route, "none")
    effect = compiled_effect(core, route, facts)
    quote_items = [QuoteEvidence(exact_text=quote)] if quote else []
    step_items = [QuoteEvidence(exact_text=step_quote)] if step_quote else []
    clarification = {
        "required": True,
        "reason": "missing_referent",
        "question": "请说明要处理的具体对象。",
    } if route == "clarify" else {
        "required": False, "reason": "none", "question": None,
    }
    return ShadowRoute(
        schema_version="s25-route-v2", primary_route=route,
        route_target=target, execution_scope=scope, effect_intent=effect,
        ambiguity="clear", clarification=clarification,
        source_quote_evidence=quote_items,
        plan_step_quote_evidence=plan_steps if plan_steps is not None else step_items,
    )


def extract_unique_ordinal(message):
    """Literal source-binding only; no route inference or permission inference."""
    occurrences = re.findall(r"第[一二三四五六七八1-8]步", message)
    if len(occurrences) != 1 or message.count(occurrences[0]) != 1:
        return None
    return occurrences[0]


async def evaluate(provider, model, case, mode, timeout):
    facts = facts_for(case, mode)
    core = await classify_hierarchical_diagnostic(
        provider, model, case.prompt, facts, timeout)
    row = {
        "case_id": case.case_id, "fixture_mode": mode,
        "core_correct": core.failure_stage is None and
            (core.object_type, core.operation) == (
                case.expected_object, case.expected_operation),
        "object_type": core.object_type, "operation": core.operation,
        "failure_stage": core.failure_stage, "quote_state": "not_attempted",
        "disposition": "deny", "reason": "semantic_stage_failure",
        "host_authorization": False,
    }
    if core.failure_stage:
        return row
    quote = None
    if core.object_type is not None and core.operation is not None:
        try:
            raw = await asyncio.wait_for(provider.complete(
                model=model, response_schema=SourceQuotes.model_json_schema(),
                messages=[{"role": "system", "content": QUOTE_PROMPT},
                          {"role": "user", "content": json.dumps({
                              "message": case.prompt,
                              "is_plan_step_revision": core.object_type == "plan_step",
                          }, ensure_ascii=False)}]), timeout=timeout)
            extracted = SourceQuotes.model_validate_json(raw)
            candidate = extracted.current_turn_quote
            if candidate and case.prompt.count(candidate) == 1:
                quote = candidate
                row["quote_state"] = "unique_exact"
            elif candidate is None:
                row["quote_state"] = "model_abstained"
            elif candidate == "":
                row["quote_state"] = "empty_quote"
            elif candidate not in case.prompt:
                row["quote_state"] = "quote_not_found"
            else:
                row["quote_state"] = "quote_ambiguous"
        except Exception as exc:
            row["quote_state"] = "model_" + type(exc).__name__
    # Step ordinal is verified deterministically from a UNIQUE literal occurrence.
    # It is not reconstructed from model output or from checkpoint status.
    step_quote = (extract_unique_ordinal(case.prompt)
                  if core.object_type == "plan_step" else None)
    row["step_reference_quote_present"] = bool(step_quote)
    # Provenance-only fallback: the entire trusted current-turn message is
    # literal source evidence, NOT inferred permission or a fabricated snippet.
    # Limited to an unambiguous numbered revision already selected by frozen M2.
    # Checkpoint revision, step binding and write authority remain Host-owned.
    if (quote is None and core.object_type == "plan_step"
            and core.operation == "revise" and step_quote is not None
            and 0 < len(case.prompt) <= 400):
        quote = case.prompt
        row["quote_state"] = "trusted_full_message_revision"
    plan_steps = None
    if (core.object_type, core.operation) == ("plan", "create"):
        try:
            plan_steps = await collect_ordered_step_quotes(
                provider, model, case.prompt, timeout)
        except Exception:
            plan_steps = []
        row["plan_step_quotes"] = len(plan_steps)
    try:
        proposal = proposal_from_core(core, case.prompt, quote, step_quote,
                                      facts, plan_steps=plan_steps)
    except ValueError:
        row["reason"] = "invalid_core_pair"
        return row
    result = verify_core_evidence(core, proposal, case.prompt, facts)
    row.update(candidate_route=result.candidate_route,
               disposition=result.disposition, reason=result.reason)
    return row


def expected_disposition(case_id, mode):
    """正向夹具仅在效果已由计划对象或已绑定工具确定时才期望准入。"""
    if case_id == "resume_missing":
        return "deny"
    # 独立新工具没有可信工具名，不能因为合成 facts 打开了只读开关就期望准入。
    if (mode == "positive" and case_id in POSITIVE
            and case_id != "pending_independent"):
        return "potentially_admissible"
    return "needs_evidence"


async def run(args, provider=None):
    case_map = {c.case_id: c for c in CASES}
    chosen = args.case or SCENARIOS
    if any(cid not in case_map for cid in chosen):
        raise ValueError("unknown_case_id")
    settings = Settings()
    if settings.model_provider != "ollama_proxy":
        raise ValueError("ollama_proxy_required")
    if provider is None:
        provider = HTTPUnderstandingModel(
            settings.model_provider, settings.model_base_url,
            settings.model_api_key, settings.model_timeout_seconds,
            settings.model_max_output_tokens)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    def save(complete):
        report = {
            "variant": "D_structured_quotes_deterministic_route_read_only",
            "model": settings.understanding_strong_model,
            "fixture_mode": args.mode,
            "cohort": "CURATED_SYNTHETIC_NOT_BLIND",
            "m2_gate": "HOLD", "host_authorization": False,
            "complete": complete, "expected_rows": len(chosen) * args.repeats,
            "rows": rows,
            "summary": {
                "total": len(rows),
                "core_correct": sum(r["core_correct"] for r in rows),
                "quote_exact": sum(r["quote_state"] == "unique_exact" for r in rows),
                "revision_source_fallback": sum(r["quote_state"] == "trusted_full_message_revision" for r in rows),
                "expected_disposition_correct": sum(
                    r["disposition"] == r["expected_disposition"] for r in rows),
                "potentially_admissible": sum(
                    r["disposition"] == "potentially_admissible" for r in rows),
                "denied": sum(r["disposition"] == "deny" for r in rows),
                "needs_evidence": sum(r["disposition"] == "needs_evidence" for r in rows),
            },
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return report
    for repeat in range(args.repeats):
        for cid in chosen:
            row = await evaluate(provider, settings.understanding_strong_model,
                                 case_map[cid], args.mode, args.timeout)
            row["repeat"] = repeat
            row["expected_disposition"] = expected_disposition(cid, args.mode)
            rows.append(row)
            save(False)
    report = save(True)
    print(json.dumps(report["summary"], ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("boundary", "positive"), default="positive")
    parser.add_argument("--case", action="append")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--output", default="evals/results/s25-02-m2-structured-v1.json")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 3:
        parser.error("repeats must be 1..3")
    if os.getenv("RUN_S25_02_SHADOW_LIVE") != "1":
        parser.error("RUN_S25_02_SHADOW_LIVE=1 required")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
