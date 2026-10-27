"""Isolated live Qwen core -> ShadowRoute evidence -> Host preview evaluation.

Three model calls per case; no application Host, tool execution, or persistence.
Synthetic Host facts are test fixtures, never authorization.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from agent.route_shadow import QuoteEvidence

from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from agent.route_semantic_hierarchical import classify_hierarchical_diagnostic
from agent.route_semantic_evidence import verify_core_evidence
from agent.route_shadow import ShadowRoute, PROMPT as SHADOW_PROMPT
from evals.s25_02_m2_hierarchical_boundary_cases import CASES

EVIDENCE_GUIDANCE = """SECOND-STAGE EVIDENCE proposal, not a new semantic decision.
Preserve frozen_core object_type and operation. Quote a unique exact substring
from the current user message in source_quote_evidence; do not paraphrase.
For revisions, quote the explicit numbered step in plan_step_quote_evidence.
If evidence is unavailable, abstain instead of inventing Host state or authority.
"""

class SourceQuotes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_turn_quote: str | None = Field(default=None, max_length=400)
    revision_step_quote: str | None = Field(default=None, max_length=80)


QUOTE_PROMPT = """Extract exact, contiguous, unique text spans from CURRENT user message.
Return JSON matching schema. A quote must occur EXACTLY ONCE in the message.
current_turn_quote supports the current user's request. For a plan-step revision,
revision_step_quote is the exact ordinal step expression in the message.
If uncertain, output null. Never invent text, references, checkpoint data or
Host permissions. Never decide or change the route or operation."""


async def probe_missing_quotes(provider, model, case, proposal, timeout):
    """Diagnostic-only quote probe; never repairs route conflicts or explicit evidence."""
    if proposal.source_evidence or proposal.source_quote_evidence:
        return proposal, "not_needed"
    raw = await asyncio.wait_for(provider.complete(
        model=model, response_schema=SourceQuotes.model_json_schema(),
        messages=[{"role": "system", "content": QUOTE_PROMPT},
                  {"role": "user", "content": json.dumps({
                      "message": case.prompt,
                      "is_plan_step_revision": proposal.primary_route == "plan_revise",
                  }, ensure_ascii=False)}]), timeout=timeout)
    extracted = SourceQuotes.model_validate_json(raw)
    quote = extracted.current_turn_quote
    if quote is None or case.prompt.count(quote) != 1:
        return proposal, "unavailable"
    updates = {"source_quote_evidence": [QuoteEvidence(exact_text=quote)]}
    if proposal.primary_route == "plan_revise" and not (
        proposal.plan_step_evidence or proposal.plan_step_quote_evidence
    ):
        step = extracted.revision_step_quote
        if step is not None and case.prompt.count(step) == 1:
            updates["plan_step_quote_evidence"] = [QuoteEvidence(exact_text=step)]
    return proposal.model_copy(update=updates), "quoted_exactly"


DEFAULT_CASE_IDS = (
    "start_first", "modify_step", "pending_supply",
    "pending_independent", "resume_missing", "tool_unavailable",
)

async def evaluate_case(provider, model, case, timeout):
    core = await classify_hierarchical_diagnostic(
        provider, model, case.prompt, case.facts, timeout)
    row = {
        "case_id": case.case_id, "object_type": core.object_type,
        "operation": core.operation, "failure_stage": core.failure_stage,
        "core_correct": (
            core.failure_stage is None and
            (core.object_type, core.operation) ==
            (case.expected_object, case.expected_operation)
        ),
        "evidence_state": "not_attempted",
        "candidate_route": None, "disposition": "deny",
        "reason": "semantic_stage_failure" if core.failure_stage else "not_evaluated",
        "host_authorization": False,
    }
    if core.failure_stage:
        return row
    try:
        raw = await asyncio.wait_for(provider.complete(
            model=model, response_schema=ShadowRoute.model_json_schema(),
            messages=[
                {"role": "system", "content": SHADOW_PROMPT + EVIDENCE_GUIDANCE},
                {"role": "user", "content": json.dumps({
                    "message": case.prompt,
                    "has_saved_plan": case.facts.has_plan,
                    "pending_action_tool": case.facts.pending_action,
                    "pending_action_field": case.facts.pending_field,
                    "frozen_core": {
                        "object_type": core.object_type,
                        "operation": core.operation,
                    },
                    "instruction": (
                        "Supply only source-grounded route evidence consistent with "
                        "the frozen core; abstain from unsupported references. "
                        "Never invent Host authority."
                    ),
                }, ensure_ascii=False)},
            ]), timeout=timeout)
        proposal = ShadowRoute.model_validate_json(raw)
        row["evidence_state"] = "schema_valid"
        row["model_route"] = proposal.primary_route
        row["model_target"] = proposal.route_target
        row["quote_evidence_count"] = len(proposal.source_quote_evidence)
        row["step_quote_evidence_count"] = len(proposal.plan_step_quote_evidence)
        row["trusted_revision_valid"] = bool(case.facts.revision_valid)
    except Exception as exc:
        row["evidence_state"] = "unavailable"
        row["reason"] = "evidence_model_" + type(exc).__name__
        return row
    # A route conflict cannot be repaired with quote extraction.
    from agent.route_semantic_preview import ROUTE_BY_CORE
    expected_route = ROUTE_BY_CORE.get((core.object_type, core.operation))
    row["quote_probe_state"] = "not_attempted"
    if (proposal.primary_route == expected_route and
            not proposal.source_evidence and not proposal.source_quote_evidence):
        try:
            proposal, row["quote_probe_state"] = await probe_missing_quotes(
                provider, model, case, proposal, timeout)
        except Exception as exc:
            row["quote_probe_state"] = "unavailable_" + type(exc).__name__
        row["quote_evidence_count_after_probe"] = len(proposal.source_quote_evidence)
        row["step_quote_evidence_count_after_probe"] = len(
            proposal.plan_step_quote_evidence)
    result = verify_core_evidence(core, proposal, case.prompt, case.facts)
    row.update(
        candidate_route=result.candidate_route,
        disposition=result.disposition, reason=result.reason,
        evidence_state="reviewed",
        host_authorization=False,
    )
    return row


async def run(args, provider=None):
    cases_by_id = {c.case_id: c for c in CASES}
    selected = args.case or list(DEFAULT_CASE_IDS)
    unknown = sorted(set(selected) - cases_by_id.keys())
    if unknown:
        raise ValueError("unknown_case_ids: " + ", ".join(unknown))
    settings = Settings()
    if settings.model_provider != "ollama_proxy":
        raise ValueError("ollama_proxy_required")
    model = settings.understanding_strong_model
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
            "model": model, "variant": "D_core_evidence_host_preview_read_only",
            "cohort": "CURATED_BOUNDARY_NOT_SEALED_BLIND",
            "m2_gate": "HOLD", "host_authorization": False,
            "complete": complete, "expected_rows": len(selected) * args.repeats,
            "rows": rows,
            "summary": {
                "total": len(rows),
                "core_correct": sum(r["core_correct"] for r in rows),
                "evidence_schema_valid": sum(r["evidence_state"] == "reviewed" for r in rows),
                "quote_supplied": sum(r.get("quote_evidence_count", 0) > 0 for r in rows),
                "quote_probe_exact": sum(r.get("quote_probe_state") == "quoted_exactly" for r in rows),
                "potentially_admissible": sum(
                    r["disposition"] == "potentially_admissible" for r in rows),
                "denied": sum(r["disposition"] == "deny" for r in rows),
                "needs_evidence": sum(r["disposition"] == "needs_evidence" for r in rows),
                "safe_non_effect": sum(r["disposition"] == "safe_non_effect" for r in rows),
            },
        }
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        return report
    for repeat in range(args.repeats):
        for case_id in selected:
            row = await evaluate_case(provider, model, cases_by_id[case_id], args.timeout)
            row["repeat"] = repeat
            rows.append(row)
            save(False)
    report = save(True)
    print(json.dumps(report["summary"], ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--output", default="evals/results/s25-02-m2-evidence-live-v1.json")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 3:
        parser.error("repeats must be 1..3")
    if os.getenv("RUN_S25_02_SHADOW_LIVE") != "1":
        parser.error("RUN_S25_02_SHADOW_LIVE=1 required")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
