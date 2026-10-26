"""M2 v3 real-Qwen comparison on known development cases only (never blind).

Explicitly opt-in; zero business side effects, zero actual route authorization.
"""
import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from agent.config import Settings
from agent.route_semantic_v3 import evaluate_semantic_v3
from agent.understanding import HTTPUnderstandingModel
from evals.s25_02_m2_shadow_live import CASES


async def run(args):
    settings = Settings()
    if settings.model_provider != "ollama_proxy":
        raise ValueError("real_qwen_comparison_requires_ollama_proxy")
    provider = HTTPUnderstandingModel(
        settings.model_provider, settings.model_base_url,
        settings.model_api_key, settings.model_timeout_seconds,
        settings.model_max_output_tokens)
    rows = []
    for case in CASES:
        if args.case and case.case_id not in args.case:
            continue
        out = await evaluate_semantic_v3(
            provider, settings.understanding_strong_model,
            case.prompt, case.facts, timeout_s=args.timeout)
        semantic_match = (
            out.get("state") == "valid"
            and out.get("route") == case.expected_route
            and (case.expected_target is None
                 or out.get("route_target") == case.expected_target)
            and (case.expected_effect is None
                 or out.get("effect") == case.expected_effect))
        preview = out.get("host_preview") or {}
        host_match = (
            bool(preview)
            and (case.expected_disposition is None
                 or preview.get("disposition") == case.expected_disposition)
            and (case.expected_admitted_route is None
                 or preview.get("admitted_route") == case.expected_admitted_route))
        row = {
            "case_id": case.case_id,
            "state": out["state"],
            "stage": out.get("stage"),
            "reason": out.get("reason"),
            "error_class": out.get("error_class"),
            "semantic_object": out.get("semantic_object"),
            "semantic_operation": out.get("semantic_operation"),
            "proposed_route": out.get("route"),
            "expected_route": case.expected_route,
            "semantic_match": semantic_match,
            "host_match": host_match,
            "host_disposition": preview.get("disposition"),
            "host_reason": preview.get("reason"),
            "result": "PASS" if semantic_match and host_match else "REVIEW",
        }
        rows.append(row)
        print(case.case_id, row["result"])
    summary = dict(Counter(row["result"] for row in rows))
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model": settings.understanding_strong_model,
        "cohort": "known_development_regression_NOT_BLIND",
        "summary": {"PASS": summary.get("PASS", 0),
                    "REVIEW": summary.get("REVIEW", 0),
                    "total": len(rows)},
        "m2_gate": "HOLD",
        "blind_generalization": "NOT_ASSESSED",
        "host_authorization": False,
        "rows": rows,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print("Report:", output)
    return 0 if rows and summary.get("PASS", 0) == len(rows) else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evals/results/s25-02-m2-semantic-v3-known.json")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--case", action="append", choices=[case.case_id for case in CASES])
    args = parser.parse_args()
    if os.environ.get("RUN_S25_02_SHADOW_LIVE") != "1":
        parser.error("RUN_S25_02_SHADOW_LIVE=1 required")
    if args.timeout <= 0:
        parser.error("timeout must be > 0")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
