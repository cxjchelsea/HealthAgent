"""S2-D real Qwen local failure recovery scenario.

Uses only the safe arithmetic tool to trigger a reproducible divide-by-zero
observation, followed by a model-chosen plan revision and successful calculation.
Not run by CI; no mocked model decisions or arbitrary failure hooks.
"""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import httpx
from agent.providers import ModelUnavailable

from agent.config import Settings
from agent.decision import NativeToolDecision, ToolDecisionError
from agent.loop import AgentLoop


class DiagnosticDecider:
    """Intercept only failure metadata; never log prompts, credentials, or responses."""
    def __init__(self, decider):
        self.decider = decider
        self.requests = 0
        self.total_ms = 0
        self.failure = None

    async def decide(self, messages, tools):
        self.requests += 1
        started = time.monotonic()
        try:
            return await self.decider.decide(messages, tools)
        except ModelUnavailable as exc:
            cause = exc.__cause__
            category = ("http_status" if isinstance(cause, httpx.HTTPStatusError)
                        else "timeout" if isinstance(cause, httpx.TimeoutException)
                        else "network" if isinstance(cause, httpx.TransportError)
                        else "provider_response")
            self.failure = {"category": category,
                            "exception_type": type(cause).__name__ if cause else type(exc).__name__}
            if isinstance(exc, ToolDecisionError):
                self.failure["reason_code"] = exc.code
                if exc.metadata:
                    self.failure["response_shape"] = exc.metadata
            if isinstance(cause, httpx.HTTPStatusError):
                self.failure["http_status"] = cause.response.status_code
            raise
        finally:
            self.total_ms += round((time.monotonic() - started) * 1000)


async def evaluate(strategy="dynamic"):
    if os.getenv("RUN_S2D_LIVE") != "1":
        raise RuntimeError("Set RUN_S2D_LIVE=1 to allow model provider calls")
    settings = Settings()
    # v2 fixed-request real-provider matrix returned propose_plan for this goal.
    # Avoid prescribing tool schema internals in the user-facing task.
    goal = "请先用 propose_plan 制定计划，先用 calculate 计算8/0并观察失败，再修订计划计算8*8。"
    decider = DiagnosticDecider(NativeToolDecision(settings))
    started = time.monotonic()
    result = await AgentLoop(decider, max_steps=10, skill_strategy=strategy,
                             skill_budget_chars=2000).run(goal, include_evidence=True)
    duration_ms = round((time.monotonic() - started) * 1000)
    audit = result.get("audit", [])
    failed = any(x["name"] == "calculate" and not x["observation"].get("ok") for x in audit)
    revised = any(x["event"] == "plan_revised" for x in result.get("plan_events", []))
    recovered = any(x["name"] == "calculate" and x["observation"].get("value") == 64
                    for x in audit)
    verdict = {"observed_failure": failed, "model_revised_plan": revised,
               "observed_recovery": recovered, "terminal_completed": result["status"] == "completed",
               "all_plan_steps_verified": bool(result.get("plan")) and all(
                   x["status"] == "completed" for x in result["plan"])}
    return {"suite": "s2d-qwen-recovery-v3", "mode": "REAL_PROVIDER",
            "provider": settings.model_provider, "model": settings.model_name,
            "strategy": strategy,
            "checks": verdict, "passed": all(verdict.values()),
            "agent_status": result["status"],
            "termination_reason": result["status"],
            "duration_ms": duration_ms,
            "decision_requests": decider.requests,
            "decision_duration_ms": decider.total_ms,
            "provider_failure": decider.failure,
            "prompt_version": result.get("prompt_version"),
            "decision_steps": result.get("steps"),
            "plan": result.get("plan"), "events": result.get("plan_events"),
            "trace": result.get("trace"),
            "limitations": ["Single controlled failure; no statistical model reliability claim",
                            "Only bounded tool and state metadata saved, not response text"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=("dynamic", "all"), default="dynamic")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.strategy))
    path = Path(args.output or f"evals/results/s2d/qwen-recovery-{args.strategy}-v3.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("S2-D real Qwen recovery:", "PASS" if report["passed"] else "FAIL", path)
    print("Status:", report["agent_status"], "Provider failure:", report["provider_failure"],
          "Decision requests:", report["decision_requests"])
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
