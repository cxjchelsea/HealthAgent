"""Offline deterministic harness and opt-in real provider benchmark.

Offline scripted fixtures exercise executor, graph, assertions only.
They are NEVER reported as model tool-selection accuracy.
"""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from agent.decision import Decision, NativeToolDecision
from agent.loop import AgentLoop
from agent.config import Settings

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "datasets" / "s2a-20-v1.jsonl"

def load_cases():
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]

class ScriptedDecider:
    def __init__(self, tools):
        self.names = list(tools)
        self.index = 0
    async def decide(self, messages, tools):
        if self.index < len(self.names):
            name = self.names[self.index]
            self.index += 1
            args = ({"expression": "12*3"} if name == "calculate" else
                    {"title": "benchmark-task"} if name == "create_task" else {})
            return Decision("", [{"id": f"benchmark-{self.index}", "name": name, "arguments": args}])
        return Decision("已完成任务。", [])

async def evaluate(real: bool) -> dict:
    if real and os.getenv("RUN_S2A_LIVE") != "1":
        raise RuntimeError("Set RUN_S2A_LIVE=1 before real provider evaluations")
    settings = Settings() if real else None
    if real and settings.model_provider not in ("ollama_proxy", "openai_compatible"):
        raise RuntimeError("Configure a real tool-calling provider")
    rows = []
    for case in load_cases():
        decider = NativeToolDecision(settings) if real else ScriptedDecider(case["expected_tools"])
        start = time.monotonic()
        try:
            result = await AgentLoop(decider).run(case["goal"])
            actual = [x["name"] for x in result["trace"] if x["type"] == "tool"]
            expected = case["expected_tools"]
            # Do not mark failure/safety cases successful just because a model obeyed an unsafe request.
            tool_match = actual == expected
            answer_match = (case["expected_answer_contains"] is None or
                            case["expected_answer_contains"] in result["answer"]) if real else None
            success = (result["status"] == "completed" and tool_match and
                       (answer_match is not False))
            rows.append({"id": case["id"], "category": case["category"],
                         "status": result["status"], "actual_tools": actual,
                         "expected_tools": expected, "tool_match": tool_match,
                         "answer_match": answer_match, "passed": success,
                         "latency_ms": int((time.monotonic()-start)*1000),
                         "steps": result.get("steps"),
                         "prompt_version": result["prompt_version"],
                         "token_usage_available": False})
        except Exception as exc:
            rows.append({"id": case["id"], "passed": False, "error_type": type(exc).__name__})
    total = len(rows)
    return {"mode": "real_model" if real else "scripted_harness",
            "provider": settings.model_provider if real else "scripted",
            "model": settings.model_name if real else "scripted_fixture",
            "total": total, "passed": sum(x["passed"] for x in rows),
            "tool_match_rate": sum(bool(x.get("tool_match")) for x in rows)/total,
            "results": rows,
            "warning": "Scripted fixtures do not measure model ability" if not real else
                       "Real-provider benchmark; human review needed for nuanced final-answer correctness"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", help="Actually call the configured model")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    result = asyncio.run(evaluate(args.real))
    output = Path(args.output or ("evals/results/s2a/real.json" if args.real else
                                  "evals/results/s2a/offline.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{result['mode']}: {result['passed']}/{result['total']} tool-match {result['tool_match_rate']:.1%}")
    print("evidence:", output)
    if result["passed"] < result["total"] and not args.real:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
