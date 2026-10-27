"""实跑已封存的 predeclared_v1。失败后不得改题面或分类提示词。"""
import argparse
import asyncio
import json
import os
from pathlib import Path

from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from evals.s25_02_m2_evidence_structured_live import evaluate
from evals.s25_02_m2_predeclared_v1 import CASES, assert_manifest_frozen, score_gate


async def run(args):
    assert_manifest_frozen()
    settings = Settings()
    if settings.model_provider != "ollama_proxy":
        raise ValueError("ollama_proxy_required")
    provider = HTTPUnderstandingModel(
        settings.model_provider, settings.model_base_url,
        settings.model_api_key, settings.model_timeout_seconds,
        settings.model_max_output_tokens)
    rows = []
    for repetition in range(1, args.repeats + 1):
        for case in CASES:
            outcome = await evaluate(
                provider, settings.understanding_strong_model, case, "boundary",
                args.timeout)
            outcome["repetition"] = repetition
            outcome["expected_disposition"] = case.expected_disposition
            outcome["case_pass"] = bool(
                outcome["core_correct"]
                and outcome["disposition"] == case.expected_disposition
                and outcome["host_authorization"] is False)
            rows.append(outcome)
            print(case.case_id, repetition, outcome["case_pass"],
                  outcome.get("object_type"), outcome.get("operation"),
                  outcome.get("disposition"), flush=True)
    report = {
        "model": settings.understanding_strong_model,
        "rows": rows,
        **score_gate(rows),
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "predeclared_gate": report["predeclared_gate"],
        "m2_gate": report["m2_gate"],
        "failed_case_ids": report["failed_case_ids"],
        "per_repetition": report["per_repetition"],
    }, ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument(
        "--output", default="evals/results/s25-02-m2-predeclared-v1.json")
    args = parser.parse_args()
    if args.repeats != 2:
        parser.error("predeclared_v1_requires_two_repetitions")
    if os.getenv("RUN_S25_02_SHADOW_LIVE") != "1":
        parser.error("RUN_S25_02_SHADOW_LIVE=1 required")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
