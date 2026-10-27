"""Hierarchical D known-case diagnostic: stages, confusion, stability, no execution."""
import argparse
import asyncio
import json
import os
from collections import Counter
from pathlib import Path

from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from agent.route_semantic_hierarchical import classify_hierarchical_diagnostic
from evals.s25_02_m2_ab_comparison import CASES as DEVELOPMENT_CASES, core_match
from evals.s25_02_m2_hierarchical_boundary_cases import CASES as BOUNDARY_CASES


def expected_core(case):
    route = case.expected_route
    if route == "agent_loop":
        if case.expected_target == "pending_clarification":
            return "pending_slot", "invoke"
        if case.expected_effect == "mutation_requested":
            return "task", "create"
        return "tool", "invoke"
    mapping = {
        "respond": ("none", "answer"),
        "clarify": ("none", "clarify"),
        "plan_create": ("plan", "create"),
        "plan_revise": ("plan_step", "revise"),
        "plan_execute_first": ("plan", "execute_first"),
        "plan_resume": ("plan", "resume"),
    }
    if route == "cancel":
        return ("pending_slot" if case.facts.pending_action else "plan"), "cancel"
    return mapping[route]


def summarize(rows, case_ids):
    total = len(rows)
    counts = {
        "object": Counter(),
        "operation": Counter(),
    }
    for row in rows:
        if row["object_type"] is not None:
            counts["object"][(row["expected_object"], row["object_type"])] += 1
        if row["operation"] is not None:
            counts["operation"][(row["expected_operation"], row["operation"])] += 1
    by_case = {case_id: [r for r in rows if r["case_id"] == case_id]
               for case_id in case_ids}
    def stable(field):
        return sum(
            len({tuple(r.get(field)) if isinstance(r.get(field), list)
                 else r.get(field) for r in items}) == 1
            for items in by_case.values()
        )
    return {
        "total": total,
        "correct": sum(r["core_correct"] for r in rows),
        "object_correct": sum(r["object_correct"] for r in rows),
        "operation_correct": sum(r["operation_correct"] for r in rows),
        "object_failures": sum(r["failure_stage"] == "object" for r in rows),
        "operation_failures": sum(r["failure_stage"] == "operation" for r in rows),
        "object_accuracy": sum(r["object_correct"] for r in rows) / total if total else 0,
        "operation_accuracy": sum(r["operation_correct"] for r in rows) / total if total else 0,
        "core_accuracy": sum(r["core_correct"] for r in rows) / total if total else 0,
        "stable_cases": stable("core_pair"),
        "object_stable_cases": stable("object_type"),
        "operation_stable_cases": stable("operation"),
        "cases_observed": len(by_case),
        "object_confusion": [
            {"expected": expected, "actual": actual, "count": n}
            for (expected, actual), n in sorted(counts["object"].items())
        ],
        "operation_confusion": [
            {"expected": expected, "actual": actual, "count": n}
            for (expected, actual), n in sorted(counts["operation"].items())
        ],
    }


async def run(args):
    settings = Settings()
    if settings.model_provider != "ollama_proxy":
        raise ValueError("ollama_proxy_required")
    cases = BOUNDARY_CASES if args.cohort == 'boundary' else DEVELOPMENT_CASES
    if args.case:
        available = {case.case_id for case in cases}
        unknown = sorted(set(args.case) - available)
        if unknown:
            raise ValueError(f"unknown_case_ids: {unknown}")
        cases = [case for case in cases if case.case_id in set(args.case)]
    provider = HTTPUnderstandingModel(
        settings.model_provider, settings.model_base_url,
        settings.model_api_key, settings.model_timeout_seconds,
        settings.model_max_output_tokens)
    rows = []
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    case_ids = [case.case_id for case in cases]
    def save_report(completed):
        report = {
            "model": settings.understanding_strong_model,
            "cohort": ("CURATED_BOUNDARY_NOT_SEALED_BLIND" if args.cohort == "boundary"
                       else "KNOWN_DEVELOPMENT_NOT_BLIND"),
            "m2_gate": "HOLD", "host_authorization": False,
            "variant": "D_hierarchical_core_only",
            "selected_case_ids": case_ids,
            "complete": completed,
            "expected_rows": args.repeats * len(cases),
            "summary": summarize(rows, case_ids),
            "rows": rows,
        }
        temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(output_path)
        return report
    for repeat in range(args.repeats):
        for case in cases:
            wanted_object, wanted_operation = (
                (case.expected_object, case.expected_operation)
                if args.cohort == 'boundary' else expected_core(case)
            )
            diagnostic = await classify_hierarchical_diagnostic(
                provider, settings.understanding_strong_model,
                case.prompt, case.facts, args.timeout)
            obj, op = diagnostic.object_type, diagnostic.operation
            rows.append({
                "case_id": case.case_id, "repeat": repeat,
                "state": "classified" if diagnostic.failure_stage is None else "unavailable",
                "failure_stage": diagnostic.failure_stage,
                "error_class": diagnostic.error_class,
                "object_type": obj, "operation": op,
                "expected_object": wanted_object,
                "expected_operation": wanted_operation,
                "object_correct": obj == wanted_object,
                "operation_correct": op == wanted_operation,
                "core_correct": diagnostic.failure_stage is None
                and obj == wanted_object and op == wanted_operation,
                "core_pair": [obj, op],
                "host_authorization": False,
            })
            save_report(False)
    report = save_report(True)
    summary = report["summary"]
    print(json.dumps(summary, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", choices=["development", "boundary"],
                        default="development")
    parser.add_argument("--case", action="append", default=[],
                        help="Run only this case ID; repeat to select multiple")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument(
        "--output", default="evals/results/s25-02-m2-hierarchical-d.json")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 5:
        parser.error("repeats must be 1..5")
    if os.getenv("RUN_S25_02_SHADOW_LIVE") != "1":
        parser.error("RUN_S25_02_SHADOW_LIVE=1 required")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
