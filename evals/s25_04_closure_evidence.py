"""S25-04 final evidence runner: repeat isolated physical suites and preserve results.

Does not change runtime routes, bypass authorization, or reinterpret failures.
This orchestration executes existing official evaluation entrypoints verbatim.
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "evals" / "results" / "s25-04" / "closure"
SOURCES = [
    ("unified", "evals.s25_04_unified_physical",
     ROOT / "evals/results/s25-04/unified-physical-v1.json"),
    ("multitool", "evals.s25_04_multitool_unified",
     ROOT / "evals/results/s25-04/multitool-unified-v2.json"),
    ("comparison", "evals.s25_04_live_comparison",
     ROOT / "evals/results/s25-04/live-comparison-v1.json"),
]


def main():
    if os.getenv("RUN_S25_04_LIVE") != "1":
        raise RuntimeError("RUN_S25_04_LIVE=1 required")
    count = int(os.getenv("S25_04_CLOSURE_REPEATS", "3"))
    if not 1 <= count <= 5:
        raise ValueError("S25_04_CLOSURE_REPEATS must be 1..5")
    DEST.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["S25_04_MULTITOOL_REPEATS"] = "5"
    env["S25_04_REPEATS"] = "3"
    env["S25_04_STRATEGIES"] = "dynamic,all,semantic_topk"
    manifest = {
        "suite": "s25-04-closure-evidence-v1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT,
            text=True, capture_output=True, check=True).stdout.strip(),
        "physical_repetitions": count, "runs": [],
        "all_passed": False,
        "limitations": [
            "Live real-provider outcomes vary. Preserve each run separately.",
            "No proof of successful alternate-tool recovery after failure.",
            "Token usage unavailable unless provider explicitly reports it.",
            "Physical suites use synthetic isolated SQLite and a controlled MCP fault.",
        ],
    }
    for label, module, source in SOURCES:
        attempts = count if label == "unified" else 1
        for i in range(1, attempts + 1):
            if source.exists():
                source.unlink()
            process = subprocess.run(
                [sys.executable, "-m", module],
                cwd=ROOT, env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            target = DEST / f"{label}-{i}.json"
            copied = source.exists()
            if copied:
                shutil.copyfile(source, target)
            report = json.loads(target.read_text(encoding="utf-8")) if copied else {}
            if label == "comparison":
                cases = report.get("cases", [])
                evidence_ok = bool(cases) and all(
                    c.get("http_status") == 200
                    and c.get("agent_terminal_success") is True
                    and c.get("tool_evidence_matched") is True for c in cases)
            else:
                evidence_ok = bool(report.get("total")) and (
                    report.get("passed") == report.get("total"))
            record = {"suite": label, "iteration": i,
                      "exit_code": process.returncode,
                      "report_found": copied, "evidence_passed": evidence_ok,
                      "passed": process.returncode == 0 and copied and evidence_ok,
                      "report": str(target.relative_to(ROOT)) if copied else None,
                      "total": report.get("total"), "passed_cases": report.get("passed")}
            manifest["runs"].append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            if not record["passed"]:
                (DEST / f"{label}-{i}.log").write_text(
                    process.stdout[-12000:], encoding="utf-8")
    manifest["all_passed"] = all(r["passed"] for r in manifest["runs"])
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    out = DEST / "closure-manifest-v1.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("Evidence:", out)
    if not manifest["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
