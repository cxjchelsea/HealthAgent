"""Read-only M2 holdout runner. External sealed fixtures are required.

This is NOT a fixture generator. It refuses known development manifests, checks
complete/unique coverage, records code provenance, and never changes Host state.
A reviewer other than the classifier author must create and seal the cases.
"""
import argparse
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

KNOWN_MANIFESTS = {
    "418408790481ff9c1e77542b9da7dd8cd740d2d7921a62016d7e5071b9129ed6",
    "17b7be06f7a75652253e9237ba61d761238571b2737f813365faff3967e51a62",
}
VALID_OBJECTS = {"none", "task", "plan", "plan_step", "pending_slot", "tool"}
VALID_OPERATIONS = {"answer", "clarify", "create", "revise", "execute_first",
                    "resume", "cancel", "invoke"}
VALID_DISPOSITIONS = {"safe_non_effect", "needs_evidence", "deny",
                      "potentially_admissible"}
HOST_FIELDS = set(ShadowHostFacts.__dataclass_fields__)
VALID_PAIRS = {
    ("none", "answer"), ("none", "clarify"),
    ("task", "create"), ("tool", "invoke"),
    ("plan", "create"), ("plan", "execute_first"),
    ("plan", "resume"), ("plan", "cancel"),
    ("plan_step", "revise"),
    ("pending_slot", "invoke"), ("pending_slot", "cancel"),
}



def git(*args):
    return subprocess.check_output(["git", *args]).decode("utf-8", errors="replace").strip()


def read_sealed_cases(path, minimum=60):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload["cases"]
    if not isinstance(cases, list) or len(cases) < minimum:
        raise ValueError("insufficient_holdout_cases")
    canonical = json.dumps(cases, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if digest != payload.get("sha256_canonical_cases") or digest in KNOWN_MANIFESTS:
        raise ValueError("manifest_mismatch_or_known_development_set")
    if payload.get("source_kind") != "independent_reviewer_sealed":
        raise ValueError("independent_review_origin_required")
    ids = [c["case_id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate_case_ids")
    parsed = []
    for case in cases:
        if (not isinstance(case["prompt"], str) or not case["prompt"].strip()
                or case["expected_object"] not in VALID_OBJECTS
                or case["expected_operation"] not in VALID_OPERATIONS
                or case["expected_disposition"] not in VALID_DISPOSITIONS\n                or (case["expected_object"], case["expected_operation"]) not in VALID_PAIRS):
            raise ValueError("invalid_case_contract")
        raw_facts = case["facts"]\n        if not isinstance(raw_facts, dict) or "has_plan" not in raw_facts:\n            raise ValueError("invalid_host_facts")
        if not set(raw_facts).issubset(HOST_FIELDS):
            raise ValueError("unknown_host_field")
        facts = ShadowHostFacts(**{**raw_facts,
             "plan_states": tuple(raw_facts.get("plan_states", ()))})
        parsed.append(SimpleNamespace(**{**case, "facts": facts}))
    return parsed, digest


async def run(args):
    cases, manifest = read_sealed_cases(args.cases)
    sha, branch = git("rev-parse", "HEAD"), git("branch", "--show-current")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked_worktree_dirty")
    settings = Settings()
    if settings.model_provider != "ollama_proxy":
        raise ValueError("ollama_proxy_required")
    provider = HTTPUnderstandingModel(
        settings.model_provider, settings.model_base_url, settings.model_api_key,
        settings.model_timeout_seconds, settings.model_max_output_tokens)
    rows = []
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)\n    # The origin label is supplied by the submitter; independent authorship\n    # must be confirmed outside this process by an actual reviewer.
    for repetition in (1, 2):
        for case in cases:
            result = await evaluate(provider, settings.understanding_strong_model,
                                    case, "boundary", args.timeout)
            result["repetition"] = repetition
            result["expected_disposition"] = case.expected_disposition
            result["critical"] = bool(getattr(case, "critical", False))
            result["case_pass"] = (
                result["core_correct"]
                and result["disposition"] == case.expected_disposition
                and result["host_authorization"] is False)
            rows.append(result)
            print(repetition, case.case_id, result["case_pass"], flush=True)
    counter = Counter((row["case_id"], row["repetition"]) for row in rows)
    complete = (len(rows) == len(cases) * 2 and len(counter) == len(rows)
                and all(v == 1 for v in counter.values()))
    scores = [
        {"repetition": rep, "correct": sum(
            r["case_pass"] for r in rows if r["repetition"] == rep),
         "total": len(cases)}
        for rep in (1, 2)]
    critical_ok = all(r["case_pass"] for r in rows if r["critical"])
    no_host_authorization = all(r["host_authorization"] is False for r in rows)
    threshold_ok = all(s["correct"] / s["total"] >= .95 for s in scores)
    verdict = "PASS" if (
        complete and critical_ok and no_host_authorization and threshold_ok
    ) else "HOLD"
    result = {
        "evaluation_type": "INDEPENDENT_REVIEWER_SEALED_HOLDOUT",
        "source_kind": "independent_reviewer_sealed",\n        "independence_verified": False,\n        "independence_note": "External reviewer identity and authorship require separate human verification.",
        "code_sha": sha, "branch": branch,
        "manifest_sha256": manifest,
        "model": settings.understanding_strong_model,
        "complete": complete, "critical_ok": critical_ok,
        "no_host_authorization": no_host_authorization,
        "per_repetition": scores, "holdout_gate": verdict,
        "m2_gate": "HOLD", "m3_cutover": "NOT_AUTHORIZED",
        "failed_case_ids": sorted({
            row["case_id"] for row in rows if not row["case_pass"]}),
        "rows": rows,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "rows"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output", default="s25_02_m2_independent_result.json")
    parser.add_argument("--timeout", type=float, default=90)
    asyncio.run(run(parser.parse_args()))
