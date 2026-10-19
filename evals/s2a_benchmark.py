"""S2-A evidence-based benchmark. Scripted mode validates harness, not model quality."""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from agent.config import Settings
from agent.decision import Decision, NativeToolDecision
from agent.loop import AgentLoop

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "datasets" / "s2a-20-v1.jsonl"
TITLES = {
    "007": "整理笔记", "008": "检查报告", "011": "读书",
    "012": "学习 Python", "013": "核对42", "014": "记录64",
    "015": "登记12", "016": "记录时间", "017": "写周报",
}
EXPRESSIONS = {
    "001": "12*3", "002": "(19+23)*4", "003": "120/5",
    "004": "11+17-4", "013": "6*7", "014": "8*8",
    "015": "3+9", "018": "10/0", "019": "2**10",
}
VALUES = {
    "001": 36, "002": 168, "003": 24, "004": 24,
    "013": 42, "014": 64, "015": 12,
}
# These require a separate model decision after observing an earlier tool response.
# 013/014 embed the answer in the user's goal, so a parallel call isn't evidence
# of observation-dependent reasoning.
OBSERVATION_REQUIRED = {"011", "012", "015", "016", "017"}
FAILURE_CASES = {"018", "019"}
ERROR_TERMS = ("不能", "失败", "错误", "除以零", "除数", "不支持",
               "无法", "invalid", "unsupported", "division", "error")

def load_cases():
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]

class ScriptedDecider:
    """Fixture follows case contracts; never interpreted as model success."""
    def __init__(self, case):
        self.case = case
        self.index = 0

    async def decide(self, messages, tools):
        names = self.case["expected_tools"]
        if self.index >= len(names):
            suffix = self.case["id"][-3:]
            if suffix in FAILURE_CASES:
                return Decision("这个表达式不受支持，计算失败。", [])
            if suffix in VALUES:
                return Decision(f"结果为 {VALUES[suffix]}。", [])
            return Decision("任务已完成。", [])
        suffix = self.case["id"][-3:]
        name = names[self.index]
        self.index += 1
        arguments = (
            {"expression": EXPRESSIONS[suffix]} if name == "calculate" else
            {"title": TITLES[suffix]} if name == "create_task" else {}
        )
        return Decision("", [{"id": f"fixture-{self.index}",
                              "name": name, "arguments": arguments}])

def grade(case, result):
    suffix = case["id"][-3:]
    audit = result.get("audit", [])
    calls = [x["name"] for x in audit]
    expected = case["expected_tools"]
    checks = {
        "terminal_completed": result["status"] == "completed",
        "tool_sequence": calls == expected,
        "tool_executions_ok": all(x["observation"].get("ok") is True for x in audit)
        if suffix not in FAILURE_CASES else
        (len(audit) == 1 and audit[0]["observation"].get("ok") is False),
    }
    if suffix in EXPRESSIONS:
        matches = [x for x in audit if x["name"] == "calculate"]
        checks["expression_argument"] = (
            len(matches) == 1 and
            matches[0]["arguments"].get("expression", "").replace(" ", "") ==
            EXPRESSIONS[suffix].replace(" ", "")
        )
        if suffix in VALUES:
            checks["calculation_result"] = (
                len(matches) == 1 and
                matches[0]["observation"].get("value") == VALUES[suffix]
            )
    if suffix in TITLES:
        checks["task_title_and_state"] = (
            TITLES[suffix] in result.get("tasks", []) and
            any(x["name"] == "create_task" and
                x["arguments"].get("title") == TITLES[suffix] and
                x["observation"].get("ok") is True for x in audit)
        )
    if suffix in {"009", "010"}:
        checks["empty_task_list"] = len(result.get("tasks", [])) == 0 and any(
            x["name"] == "list_tasks" and x["observation"].get("tasks") == []
            for x in audit)
    if suffix in {"011", "012", "015"}:
        checks["list_observes_created_task"] = any(
            x["name"] == "list_tasks" and
            any(t.get("title") == TITLES[suffix] for t in
                x["observation"].get("tasks", []))
            for x in audit)
    if suffix in OBSERVATION_REQUIRED:
        checks["post_observation_decision"] = (
            len(audit) >= 2 and audit[1]["step"] > audit[0]["step"])
    if suffix in FAILURE_CASES:
        checks["failure_acknowledged"] = any(
            term in result["answer"].lower() for term in ERROR_TERMS)
    if suffix == "020":
        checks["unknown_tool_not_executed"] = not audit
        checks["refusal_not_empty"] = bool(result["answer"].strip())
    if suffix in VALUES:
        checks["answer_contains_expected_value"] = str(VALUES[suffix]) in result["answer"]
    if suffix in {"005", "006", "016"}:
        checks["utc_tool_success"] = any(
            x["name"] == "utc_time" and x["observation"].get("ok") is True and
            bool(x["observation"].get("utc")) for x in audit)
    return checks

async def evaluate(real: bool):
    if real and os.getenv("RUN_S2A_LIVE") != "1":
        raise RuntimeError("Set RUN_S2A_LIVE=1 for live evaluation")
    settings = Settings() if real else None
    if real and settings.model_provider not in ("ollama_proxy", "openai_compatible"):
        raise RuntimeError("Configure real tool-calling provider")
    rows = []
    for case in load_cases():
        decider = NativeToolDecision(settings) if real else ScriptedDecider(case)
        start = time.monotonic()
        try:
            result = await AgentLoop(decider).run(case["goal"], include_evidence=True)
            checks = grade(case, result)
            rows.append({
                "id": case["id"], "category": case["category"],
                "status": result["status"], "actual_tools": [
                    x["name"] for x in result["audit"]],
                "expected_tools": case["expected_tools"],
                "checks": checks, "passed": all(checks.values()),
                "latency_ms": int((time.monotonic() - start)*1000),
                "steps": result.get("steps"), "prompt_version": result["prompt_version"],
                "token_usage_available": False,
                # No raw arguments, full observations or answer in persisted artifact.
            })
        except Exception as exc:
            rows.append({"id": case["id"], "passed": False, "error_type": type(exc).__name__})
    total = len(rows)
    return {
        "mode": "real_model" if real else "scripted_harness",
        "provider": settings.model_provider if real else "scripted",
        "model": settings.model_name if real else "scripted_fixture",
        "total": total, "passed": sum(row["passed"] for row in rows),
        "tool_match_rate": sum(row.get("checks", {}).get("tool_sequence", False)
                               for row in rows)/total,
        "results": rows, "scoring_version": "s2a-evidence-v2",
        "warning": "Scripted fixture is not real-model evidence" if not real else
                   "Mechanically graded evidence; nuanced answer quality still needs review",
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.real))
    output = Path(args.output or ("evals/results/s2a/real-v2.json" if args.real
                                 else "evals/results/s2a/offline-v2.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{report['mode']}: {report['passed']}/{report['total']} passed")
    print("evidence:", output)
    if not args.real and report["passed"] != report["total"]:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
