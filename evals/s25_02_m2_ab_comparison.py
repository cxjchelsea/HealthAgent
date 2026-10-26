"""Known-case comparison of M2 semantic architectures. Not a blind gate."""
import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from agent.route_semantic_ab import classify
from agent.route_semantic_ab_reference import extract, needed
from agent.route_semantic_v3 import SemanticIntent, compile_semantic, evaluate_semantic_v3
from agent.route_shadow_host import preview_admission
from evals.s25_02_m2_shadow_live import CASES

EXPECTED_CORE = {
    "respond":("none","answer"),"clarify":("none","clarify"),
    "plan_create":("plan","create"),"plan_revise":("plan_step","revise"),
    "plan_execute_first":("plan","execute_first"),
    "plan_resume":("plan","resume"),"cancel":(None,"cancel"),
}

def core_match(case, obj, op):
    if case.expected_route=="agent_loop":
        if case.expected_target=="pending_clarification":
            return obj=="pending_slot" and op=="invoke"
        if case.expected_effect=="mutation_requested":
            return obj=="task" and op=="create"
        return obj=="tool" and op=="invoke"
    wanted=EXPECTED_CORE.get(case.expected_route)
    return bool(wanted and (wanted[0] is None or obj==wanted[0]) and op==wanted[1])

async def run(args):
    settings=Settings()
    if settings.model_provider!="ollama_proxy":
        raise ValueError("ollama_proxy_required")
    provider=HTTPUnderstandingModel(settings.model_provider,
        settings.model_base_url,settings.model_api_key,
        settings.model_timeout_seconds,settings.model_max_output_tokens)
    rows=[]
    for repeat in range(args.repeats):
      for variant in ("A","B","C"):
          for case in CASES:
              stage="classify"
              outcome={}
              try:
                  if variant=="C":
                      outcome=await evaluate_semantic_v3(
                          provider,settings.understanding_strong_model,
                          case.prompt,case.facts,timeout_s=args.timeout)
                      obj=outcome.get("semantic_object")
                      op=outcome.get("semantic_operation")
                  else:
                      core=await classify(provider,settings.understanding_strong_model,
                                          case.prompt,case.facts,args.timeout)
                      obj,op=core.object_type,core.operation
                      outcome={"state":"classified"}
                      if variant=="B":
                          stage="reference"
                          refs=await extract(provider,settings.understanding_strong_model,
                              case.prompt,core,args.timeout) if needed(core) else None
                          stage="compile"
                          effect="mutation_requested" if (obj,op) in {
                              ("task","create"),("plan","create"),("plan_step","revise")} else (
                              "none" if op in {"answer","clarify","cancel"} else "read_only")
                          semantic=SemanticIntent(object_type=obj,operation=op,
                              effect_intent=effect,**(refs.model_dump() if refs else {}))
                          compiled=compile_semantic(semantic,case.prompt,case.facts)
                          preview=preview_admission(compiled,case.facts)
                          outcome={"state":"valid","route":compiled.primary_route,
                                   "route_target":compiled.route_target,"effect":compiled.effect_intent,
                                   "host_preview":{"disposition":preview.disposition,
                                                   "admitted_route":preview.admitted_route}}
                  correct=core_match(case,obj,op)
                  route_correct=outcome.get("state")=="valid" and (
                      outcome.get("route")==case.expected_route
                      and (case.expected_target is None or
                           outcome.get("route_target")==case.expected_target)
                      and (case.expected_effect is None or
                           outcome.get("effect")==case.expected_effect))
                  preview=outcome.get("host_preview") or {}
                  host_correct=route_correct and (
                      case.expected_admitted_route is None
                      or preview.get("admitted_route")==case.expected_admitted_route)
                  rows.append({"variant":variant,"case_id":case.case_id,
                      "repeat":repeat,"object_type":obj,"operation":op,"core_correct":correct,
                      "route_correct":route_correct,"host_correct":host_correct,
                      "state":outcome.get("state"),"stage":outcome.get("stage",stage)})
              except Exception as exc:
                  rows.append({"variant":variant,"case_id":case.case_id,
                      "repeat":repeat,"object_type":obj,"operation":op,
                    "core_correct":core_match(case,obj,op), "route_correct":False,
                      "host_correct":False,"state":"unavailable",
                      "stage":stage,"error_class":type(exc).__name__})
      summary={}
    for v in ("A","B","C"):
        part=[x for x in rows if x["variant"]==v]
        summary[v]={"total":len(part),"core_correct":sum(x["core_correct"] for x in part),
                    "route_correct":sum(x["route_correct"] for x in part),
                    "host_correct":sum(x["host_correct"] for x in part)}
    confusion={}
    for v in ("A","B","C"):
        wrong=[x for x in rows if x["variant"]==v and not x["core_correct"]]
        confusion[v]=[{"case_id":x["case_id"],
                       "object_type":x.get("object_type"),
                       "operation":x.get("operation"),
                       "repeat":x["repeat"]} for x in wrong]
    report={"repeats":args.repeats,"core_confusion":confusion,
            "timestamp_utc":datetime.now(timezone.utc).isoformat(),
        "cohort":"KNOWN_DEVELOPMENT_NOT_BLIND","model":settings.understanding_strong_model,
        "m2_gate":"HOLD","host_authorization":False,
        "notes":"A is classification-only; compare core accuracy across all variants.",
        "summary":summary,"rows":rows}
    dest=Path(args.output)
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--timeout",type=float,default=60)
    p.add_argument("--output",default="evals/results/s25-02-m2-ab-comparison.json")
    p.add_argument("--repeats",type=int,default=3)
    args=p.parse_args()
    if not 1 <= args.repeats <= 10:
        p.error("repeats must be between 1 and 10")
    if os.environ.get("RUN_S25_02_SHADOW_LIVE")!="1":
        p.error("RUN_S25_02_SHADOW_LIVE=1 required")
    asyncio.run(run(args))

if __name__=="__main__":
    main()
