"""Read-only M2 Object Prompt ablation. Known AI84 = DEVELOPMENT ONLY.
Compare frozen old prompt against changed prompt at ONE code HEAD, with
identical model, evidence binding, fixtures and case ordering.
"""
import argparse
import ast
import asyncio
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from agent.route_shadow_host import ShadowHostFacts
from evals.s25_02_m2_evidence_structured_live import evaluate
import agent.route_semantic_hierarchical as hierarchical

BASE_SHA = "39fa7a4ec3221a4b23b6aa9c920735603304edf2"
REMEDIATION_SHA = "be707df11e89ed143331fbcfe918a9504e3c508e"
CASE_IDS = (
    "plan_create_03", "plan_create_positive_01", "plan_create_positive_02",
    "plan_first_01", "plan_first_02", "plan_first_03", "plan_first_04",
    "plan_first_05", "plan_first_06",
    "plan_resume_missing_01", "plan_resume_missing_02",
    "plan_resume_missing_03", "plan_resume_missing_04",
    "plan_resume_missing_05", "plan_resume_missing_06",
    "pending_supply_01", "pending_supply_02",
    "pending_new_tool_01", "pending_new_tool_02",
    "plan_revision_02", "plan_revision_04", "plan_revision_06",
    "pending_cancel_01", "pending_cancel_03",
)
COHORT_SHA = "418408790481ff9c1e77542b9da7dd8cd740d2d7921a62016d7e5071b9129ed6"


def read_prompt(commit):
    raw = subprocess.check_output(
        ["git", "show", f"{commit}:agent/route_semantic_hierarchical.py"],
        text=True, encoding="utf-8")
    tree = ast.parse(raw)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(x, ast.Name) and x.id == "OBJECT_PROMPT"
            for x in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("object_prompt_missing")


def verify_inputs(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload["cases"]
    canonical = json.dumps(cases, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    if (digest != COHORT_SHA or payload["sha256_canonical_cases"] != digest
            or len(cases) != 84 or len({x["case_id"] for x in cases}) != 84):
        raise ValueError("known_development_cohort_integrity_failed")
    case_map = {x["case_id"]: x for x in cases}
    if any(i not in case_map for i in CASE_IDS):
        raise ValueError("missing_ablated_case")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                     text=True).strip()
    if actual != REMEDIATION_SHA:
        raise ValueError(f"wrong_code_sha: expected {REMEDIATION_SHA}, got {actual}")
    return case_map, actual


async def main(args):
    cases, code_sha = verify_inputs(args.cases)
    baseline, adjusted = read_prompt(BASE_SHA), read_prompt(REMEDIATION_SHA)
    settings = Settings()
    if settings.model_provider != "ollama_proxy":
        raise ValueError("ollama_proxy_required")
    provider = HTTPUnderstandingModel(
        settings.model_provider, settings.model_base_url,
        settings.model_api_key, settings.model_timeout_seconds,
        settings.model_max_output_tokens)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    original = hierarchical.OBJECT_PROMPT
    try:
        for arm, prompt in (("baseline_prompt", baseline),
                            ("adjusted_prompt", adjusted)):
            hierarchical.OBJECT_PROMPT = prompt
            for rep in (1, 2):
                for cid in CASE_IDS:
                    item = cases[cid]
                    facts = ShadowHostFacts(**{**item["facts"],
                        "plan_states": tuple(item["facts"]["plan_states"])})
                    case = SimpleNamespace(
                        case_id=cid, prompt=item["prompt"],
                        expected_object=item["expected_object"],
                        expected_operation=item["expected_operation"], facts=facts)
                    row = await evaluate(provider, settings.understanding_strong_model,
                                         case, "boundary", args.timeout)
                    row["arm"] = arm
                    row["repetition"] = rep
                    row["expected_disposition"] = item["expected_disposition"]
                    row["case_pass"] = bool(
                        row["core_correct"]
                        and row["disposition"] == item["expected_disposition"]
                        and row["host_authorization"] is False)
                    rows.append(row)
                    print(arm, rep, cid, row["object_type"], row["operation"],
                          row["disposition"], row["case_pass"], flush=True)
    finally:
        hierarchical.OBJECT_PROMPT = original
    counts = Counter((x["arm"], x["repetition"], x["case_id"]) for x in rows)
    complete = (len(rows) == 4 * len(CASE_IDS) and len(counts) == len(rows)
                and all(x == 1 for x in counts.values()))
    comparison = {}
    for arm in ("baseline_prompt", "adjusted_prompt"):
        subset = [x for x in rows if x["arm"] == arm]
        comparison[arm] = {
            "passed": sum(x["case_pass"] for x in subset), "total": len(subset),
            "failed_ids": sorted({x["case_id"] for x in subset if not x["case_pass"]}),
            "object_errors": sum(not x["core_correct"] for x in subset),
        }
    report = {
        "evaluation_type": "KNOWN_DEVELOPMENT_ABLATION_NOT_ACCEPTANCE",
        "code_sha": code_sha, "baseline_prompt_sha": BASE_SHA,
        "adjusted_prompt_sha": REMEDIATION_SHA,
        "cohort_manifest_sha256": COHORT_SHA,
        "model": settings.understanding_strong_model,
        "complete": complete, "comparison": comparison, "rows": rows,
        "m2_gate": "HOLD", "m3_cutover": "NOT_AUTHORIZED",
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k != "rows"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="s25_02_m2_ai_eval_84_sealed.json")
    parser.add_argument("--output", default="s25_02_m2_prompt_ablation.json")
    parser.add_argument("--timeout", type=float, default=90)
    asyncio.run(main(parser.parse_args()))
