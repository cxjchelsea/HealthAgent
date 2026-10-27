"""One-shot post-remediation M2 cohort; 24 frozen cases, 2 repetitions."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from evals.s25_02_m2_evidence_structured_live import evaluate
from evals.s25_02_m2_followup_sealed_v2 import (
    CASES, MIN_PASS_RATE, REQUIRED_REPETITIONS, assert_frozen, manifest_sha256,
)

async def run(args):
    assert_frozen()
    settings = Settings()
    if settings.model_provider != "ollama_proxy":
        raise ValueError("ollama_proxy_required")
    provider = HTTPUnderstandingModel(
        settings.model_provider, settings.model_base_url,
        settings.model_api_key, settings.model_timeout_seconds,
        settings.model_max_output_tokens)
    rows = []
    for repeat in range(1, REQUIRED_REPETITIONS + 1):
        for case in CASES:
            row = await evaluate(provider, settings.understanding_strong_model,
                                 case, "boundary", args.timeout)
            row["repetition"] = repeat
            row["case_pass"] = bool(
                row["core_correct"]
                and row["host_authorization"] is False
                and row["disposition"] != "potentially_admissible")
            rows.append(row)
    ids = {case.case_id for case in CASES}
    complete = (
        len(rows) == len(CASES) * REQUIRED_REPETITIONS
        and all(
            len([r for r in rows if r["repetition"] == n]) == len(CASES)
            and {r["case_id"] for r in rows if r["repetition"] == n} == ids
            for n in (1, 2)))
    counts = []
    for n in (1, 2):
        subset = [r for r in rows if r["repetition"] == n]
        good = sum(r["case_pass"] for r in subset)
        counts.append({"repetition":n, "passed":good,
                       "total":len(subset),
                       "pass_rate":good / len(subset) if subset else 0})
    critical_ids = {case.case_id for case in CASES if case.critical}
    critical_ok = all(r["case_pass"] for r in rows if r["case_id"] in critical_ids)
    passed = bool(complete and critical_ok and
                  all(x["pass_rate"] >= MIN_PASS_RATE for x in counts))
    report = {
        "cohort":"FOLLOWUP_PREDECLARED_NOT_THIRD_PARTY_BLIND",
        "manifest_sha256":manifest_sha256(),
        "model":settings.understanding_strong_model,
        "predeclared_gate":"PASS" if passed else "HOLD",
        "m2_gate":"HOLD",
        "m3_cutover":"NOT_AUTHORIZED",
        "complete":complete, "critical_ok":critical_ok,
        "per_repetition":counts,
        "failed_case_ids":sorted({r["case_id"] for r in rows if not r["case_pass"]}),
        "rows":rows,
    }
    path=Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="rows"},ensure_ascii=False))
    return report

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--repeats",type=int,default=2)
    parser.add_argument("--timeout",type=float,default=90)
    parser.add_argument("--output",default="evals/results/s25-02-m2-followup-sealed-v2.json")
    args=parser.parse_args()
    if args.repeats!=2:
        parser.error("sealed_repetitions_must_equal_2")
    if os.getenv("RUN_S25_02_SHADOW_LIVE")!="1":
        parser.error("RUN_S25_02_SHADOW_LIVE=1 required")
    asyncio.run(run(args))

if __name__=="__main__":
    main()
