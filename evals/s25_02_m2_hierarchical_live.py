"""Hierarchical D vs known-case controls, classification-only, no execution."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from agent.route_semantic_hierarchical import classify_hierarchical
from evals.s25_02_m2_ab_comparison import CASES, core_match


def expected_core(case):
    route=case.expected_route
    if route=="agent_loop":
        if case.expected_target=="pending_clarification":
            return "pending_slot", "invoke"
        if case.expected_effect=="mutation_requested":
            return "task", "create"
        return "tool", "invoke"
    mapping={"respond":("none","answer"),"clarify":("none","clarify"),
             "plan_create":("plan","create"),
             "plan_revise":("plan_step","revise"),
             "plan_execute_first":("plan","execute_first"),
             "plan_resume":("plan","resume")}
    if route=="cancel":
        return ("pending_slot" if case.facts.pending_action else "plan"), "cancel"
    return mapping[route]


async def run(args):
    settings=Settings()
    if settings.model_provider!="ollama_proxy":
        raise ValueError("ollama_proxy_required")
    provider=HTTPUnderstandingModel(settings.model_provider,
        settings.model_base_url,settings.model_api_key,
        settings.model_timeout_seconds,settings.model_max_output_tokens)
    rows=[]
    for repeat in range(args.repeats):
        for case in CASES:
            row={"case_id":case.case_id,"repeat":repeat,"state":"unavailable",
                 "core_correct":False,"host_authorization":False}
            try:
                core=await classify_hierarchical(provider,
                    settings.understanding_strong_model,case.prompt,case.facts,args.timeout)
                wanted_object,wanted_operation=expected_core(case)
                row.update(state="classified",
                           object_correct=core.object_type==wanted_object,
                           operation_correct=core.operation==wanted_operation,
                           expected_object=wanted_object,
                           expected_operation=wanted_operation,
                           object_type=core.object_type,
                           operation=core.operation,
                           core_correct=core_match(case,core.object_type,core.operation))
            except Exception as exc:
                row["error_class"]=type(exc).__name__
            rows.append(row)
    stable=sum(len({(r.get("object_type"),r.get("operation"))
                    for r in rows if r["case_id"]==case.case_id})==1 for case in CASES)
    report={"model":settings.understanding_strong_model,
            "cohort":"KNOWN_DEVELOPMENT_NOT_BLIND","m2_gate":"HOLD",
            "host_authorization":False,"variant":"D_hierarchical_core_only",
            "summary":{"correct":sum(r["core_correct"] for r in rows),
                       "object_correct":sum(r.get("object_correct",False) for r in rows),
                       "operation_correct":sum(r.get("operation_correct",False) for r in rows),
                       "total":len(rows),"stable_cases":stable},
            "rows":rows}
    path=Path(args.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(report["summary"]))

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--repeats",type=int,default=1)
    p.add_argument("--timeout",type=float,default=60)
    p.add_argument("--output",default="evals/results/s25-02-m2-hierarchical-d.json")
    a=p.parse_args()
    if not 1 <= a.repeats <= 5:
        p.error("repeats must be 1..5")
    if os.getenv("RUN_S25_02_SHADOW_LIVE")!="1":
        p.error("RUN_S25_02_SHADOW_LIVE=1 required")
    asyncio.run(run(a))

if __name__=="__main__":
    main()
