"""S25-06 E3 real-model dependency plan creation -> FIRST -> RESUME acceptance.

Only the real configured provider proposes routes. Uses isolated SQLite, trusted
checked-in local MCP catalog and a temporary development bearer identity.
Exports bounded verdicts and safe enums, never user/model text or tool results.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import statistics
import tempfile
import time
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from apps.api.main import create_app
from agent.primary_route_v1 import propose_primary_route as original_primary_route
import apps.api.main as api_main
from services.db import (
    AgentAttempt, ConversationPlan, DependencyWorkerFence,
    PlanExecutionClaim, PlanToolDispatch, PrimaryRouteEvidence, Turn, database,
)

CREATE = "请先使用 MCP 查询 RAG 的定义，然后把第一步查询结果中的 definition 原文传给 text_stats 统计字符数。先保存这两步依赖计划，不要执行。"
FIRST = "现在只执行已保存计划的第一步，不要执行第二步。"
RESUME = "继续执行已保存计划的剩余步骤，第二步必须使用第一步的实际查询结果。"
EXPECTED_TOOLS = ("mcp_reference_lookup", "text_stats")
EXPECTED_VERSION = "1.0.0"


@contextmanager
def evidence_session(url):
    engine, Session = database(url)
    try:
        yield Session
    finally:
        engine.dispose()


def _as_json(response):
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _safe_issues(data):
    """Retain only fixed, source-audited diagnostic labels, never raw input."""
    from agent.primary_route_v1 import (
        _ROUTE_DIAGNOSTIC_FIELDS, _ROUTE_DIAGNOSTIC_TYPES,
    )
    result = []
    for issue in (data.get("primary_v1_validation_issues") or [])[:8]:
        if not isinstance(issue, dict):
            continue
        field = issue.get("field")
        category = issue.get("category")
        if not isinstance(field, str) or not isinstance(category, str):
            continue
        # Host diagnostics use the dot-joined first three safe field labels,
        # or the fixed root marker; reject any arbitrary provider strings.
        parts = field.split(".")
        if ((field == "root" or
             (1 <= len(parts) <= 3 and
              all(p in _ROUTE_DIAGNOSTIC_FIELDS for p in parts)))
                and category in _ROUTE_DIAGNOSTIC_TYPES):
            result.append({"field": field, "category": category})
    return result


def _safe_dependency_bindings_shape(dep, message):
    """Emit only fixed categories and booleans; no raw model key or value."""
    steps = dep.get("steps") if isinstance(dep, dict) else None
    if not isinstance(steps, list):
        return []
    result = []
    for step in steps[:8]:
        if not isinstance(step, dict):
            result.append({"tool": "invalid", "bindings_shape": "invalid_step"})
            continue
        tool = step.get("expected_tool")
        tool = tool if isinstance(tool, str) and tool in {"mcp_reference_lookup", "text_stats"} else "other"
        expected = "term" if tool == "mcp_reference_lookup" else (
            "text" if tool == "text_stats" else None)
        bindings = step.get("argument_bindings")
        if not isinstance(bindings, dict):
            result.append({"tool": tool, "bindings_shape": "not_object"})
            continue
        binding = bindings.get(expected) if expected else None
        kind = binding.get("kind") if isinstance(binding, dict) else None
        kind = kind if isinstance(kind, str) else None
        expected_keys = {"term"} if tool == "mcp_reference_lookup" else (
            {"text"} if tool == "text_stats" else set())
        # Only the count of unexpected names. NEVER persist those names.
        result_item = {
            "tool": tool, "bindings_shape": "object",
            "binding_count": min(len(bindings), 9),
            "expected_key_present": expected in bindings if expected else False,
            "extra_key_count": min(len(set(bindings) - expected_keys), 9),
            "binding_kind": kind if kind in {"literal", "observation_ref"}
                            else "missing_or_other",
        }
        if kind == "literal" and expected:
            value = binding.get("value")
            minimum, maximum = (1, 80) if tool == "mcp_reference_lookup" else (1, 4000)
            result_item["literal_length_valid"] = (
                isinstance(value, str) and minimum <= len(value) <= maximum)
        if tool == "mcp_reference_lookup" and kind == "literal":
            quote = binding.get("source_quote")
            value = binding.get("value")
            result_item["source_quote_in_request"] = (
                isinstance(quote, str) and bool(quote.strip())
                and quote.strip().casefold() in message.casefold())
            result_item["literal_in_quote"] = (
                isinstance(quote, str) and isinstance(value, str)
                and bool(value.strip())
                and value.casefold() in quote.casefold())
        elif tool == "text_stats" and kind == "observation_ref":
            result_item["references_first_step"] = (
                isinstance(steps[0], dict)
                and binding.get("source_step_id") == steps[0].get("id"))
            result_item["field_is_definition"] = (
                binding.get("field") == "definition")
        result.append(result_item)
    return result


def safe_primary_output_shape(raw, message=None):
    """Inspect only bounded structure, not model content or arguments."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"shape": "invalid_json"}
    if not isinstance(data, dict):
        return {"shape": "non_object"}
    route = data.get("route_kind")
    valid_routes = {"plan_create", "plan_execute_first", "plan_resume",
                    "plan_revise", "plan_cancel", "plan_missing",
                    "ordinary_tool", "respond", "clarify",
                    "clarify_response", "reject"}
    steps = data.get("steps")
    dep = data.get("dependency_plan")
    return {
        "shape": "object",
        "route": route if isinstance(route, str) and route in valid_routes
                 else "invalid",
        "steps_shape": ("array" if isinstance(steps, list)
                        else "missing" if "steps" not in data
                        else "not_array"),
        "steps_count": min(len(steps), 9) if isinstance(steps, list) else None,
        "dependency_plan_shape": (
            "missing" if "dependency_plan" not in data else
            "null" if dep is None else
            "object" if isinstance(dep, dict) else "invalid"),
        "dependency_steps_shape": (
            "array" if isinstance(dep, dict)
            and isinstance(dep.get("steps"), list) else
            "missing" if isinstance(dep, dict)
            and "steps" not in dep else "not_array" if isinstance(dep, dict)
            else "not_applicable"),
        "dependency_steps_count": (
            min(len(dep["steps"]), 9)
            if isinstance(dep, dict) and isinstance(dep.get("steps"), list)
            else None),
        "dependency_binding_shapes": (
            _safe_dependency_bindings_shape(dep, message)
            if isinstance(message, str) else []),
    }


@contextmanager
def observe_real_primary_output():
    """Observe the unchanged real model response; never replace route/model."""
    shapes = []

    async def route(provider, model, message, **kwargs):
        class ObservedProvider:
            async def complete(self, **params):
                raw = await provider.complete(**params)
                shapes.append(safe_primary_output_shape(raw, message))
                return raw
        return await original_primary_route(
            ObservedProvider(), model, message, **kwargs)

    previous = api_main.propose_primary_route
    api_main.propose_primary_route = route
    try:
        yield shapes
    finally:
        api_main.propose_primary_route = previous


def snapshot(Session, cid):
    """Evidence from durable rows, not model answer/trace assertions."""
    with Session() as db:
        record = db.get(ConversationPlan, cid)
        plan = json.loads(record.snapshot) if record else None
        attempts = list(db.scalars(select(AgentAttempt).where(
            AgentAttempt.conversation_id == cid)))
        dispatches = list(db.scalars(select(PlanToolDispatch).where(
            PlanToolDispatch.conversation_id == cid)))
        primary = list(db.scalars(select(PrimaryRouteEvidence).where(
            PrimaryRouteEvidence.conversation_id == cid)))
        claims = list(db.scalars(select(PlanExecutionClaim).where(
            PlanExecutionClaim.conversation_id == cid)))
        fences = list(db.scalars(select(DependencyWorkerFence).where(
            DependencyWorkerFence.conversation_id == cid)))
        turns = list(db.scalars(select(Turn).where(Turn.conversation_id == cid)))
        return {
            "plan": plan, "attempts": attempts, "dispatches": dispatches,
            "primary": primary, "claims": claims, "fences": fences,
            "turns": turns,
        }


def score(stage, response, state):
    data = _as_json(response)
    plan = state["plan"]
    steps = plan.get("steps", []) if isinstance(plan, dict) else []
    versions = plan.get("plan_schema_version") if isinstance(plan, dict) else None
    statuses = [s.get("status") for s in steps if isinstance(s, dict)]
    tools = [s.get("expected_tool") for s in steps if isinstance(s, dict)]
    dispatches = state["dispatches"]
    attempts = state["attempts"]
    primary = state["primary"]
    no_running_turn = not any(t.state == "running" for t in state["turns"])
    evidence = len(primary) == len(state["turns"]) and all(
        e.disposition == "allow" for e in primary)
    common = (
        response.status_code == 200
        and versions == "dependency-plan-v1"
        and plan.get("plan_executor_version") == "dependency-plan-read-v1"
        and len(steps) == 2
        and tools == list(EXPECTED_TOOLS)
        and no_running_turn and evidence
        and not state["claims"]
    )
    if stage == "create":
        # Actual model must emit valid dependency bindings, not two
        # independent literal steps or a legacy linear plan.
        bindings = steps[1].get("argument_bindings", {}) if len(steps) == 2 else {}
        dependency = bindings.get("text", {}) if isinstance(bindings, dict) else {}
        passed = (
            common and statuses == ["pending", "pending"]
            and steps[1].get("depends_on") == [steps[0].get("id")]
            and dependency.get("kind") == "observation_ref"
            and dependency.get("source_step_id") == steps[0].get("id")
            and dependency.get("field") == "definition"
            and data.get("termination_reason") ==
            "dependency_plan_created_pending_execution"
            and len(attempts) == 0 and len(dispatches) == 0
            and len(state["fences"]) == 0
            and primary[0].route_kind == "plan_create"
            and primary[0].admission_reason == "dependency_create_candidate"
        )
    else:
        count = 1 if stage == "first" else 2
        expected_status = (["completed", "pending"] if count == 1
                           else ["completed", "completed"])
        passed = (
            common and statuses == expected_status
            and data.get("termination_reason") == "dependency_steps_checkpointed"
            and len(dispatches) == count and len(attempts) == count
            and len({d.id for d in dispatches}) == count
            and all(d.status == "checkpointed"
                    and d.tool_version == EXPECTED_VERSION
                    and d.receipt_json is not None for d in dispatches)
            and all(a.status == "succeeded"
                    and a.tool_version == EXPECTED_VERSION for a in attempts)
            and {d.id for d in dispatches} == {a.id for a in attempts}
            and {d.tool_name for d in dispatches} ==
            set(EXPECTED_TOOLS[:count])
        )
        if count == 2:
            # Independently compare the actual second dispatch argument with
            # the first durable Receipt. Never export either raw value.
            dep = steps[1].get("argument_bindings", {}).get("text", {})
            source = next((d for d in dispatches
                           if d.tool_name == "mcp_reference_lookup"), None)
            target = next((d for d in dispatches
                           if d.tool_name == "text_stats"), None)
            try:
                source_receipt = json.loads(source.receipt_json) if source else {}
                target_args = json.loads(target.arguments_json) if target else {}
                matches_observation = (
                    source_receipt.get("ok") is True
                    and isinstance(source_receipt.get("definition"), str)
                    and target_args == {"text": source_receipt["definition"]}
                )
            except (ValueError, TypeError):
                matches_observation = False
            passed = (passed and dep.get("kind") == "observation_ref"
                      and dep.get("source_step_id") == steps[0].get("id")
                      and matches_observation)
    return {
        "stage": stage, "pass": bool(passed),
        "http_status": response.status_code,
        "termination_reason": data.get("termination_reason"),
        "refusal_code": data.get("primary_v1_refusal_code"),
        "validation_issues": _safe_issues(data),
        "plan_format": versions,
        "step_statuses": statuses[:8],
        "attempt_count": len(attempts),
        "dispatch_count": len(dispatches),
        "primary_evidence_count": len(primary),
        "claim_count": len(state["claims"]),
        "model_http_attempts": response.headers.get(
            "X-HealthAgent-Model-HTTP-Attempts"),
    }


def evaluate(repeats=2):
    if os.getenv("RUN_S25_06_E3_LIVE") != "1":
        raise RuntimeError("RUN_S25_06_E3_LIVE=1 required")
    if not 1 <= repeats <= 5:
        raise ValueError("repeats must be 1..5")
    base = Settings()
    if base.model_provider not in {"ollama_proxy", "openai_compatible"}:
        raise RuntimeError("real model provider required (not fake)")
    if not base.model_base_url or base.model_base_url.startswith(
            "https://api.openai.com"):
        raise RuntimeError("explicit authorized local/compatible endpoint required")
    records = []
    with tempfile.TemporaryDirectory(prefix="s25-06-e3-") as directory:
        for repeat in range(1, repeats + 1):
            token = secrets.token_urlsafe(32)
            runtime = base.model_dump()
            for key in ("route_admission_mode", "understanding_strategy",
                        "m5_unified_route_enabled", "m5_first_route_enabled",
                        "routing_profile"):
                runtime.pop(key, None)
            dbfile = (Path(directory) / f"e3-{repeat}.sqlite").resolve()
            settings = Settings(**{
                **runtime, "database_url": "sqlite:///" + dbfile.as_posix(),
                "routing_profile": "unified",
                "s25_06_dependency_plan_enabled": True,
                "allow_demo_identity": False,
                "user_tokens": token + ":s25-06-e3-synthetic-owner",
                "skill_strategy": "all",
                "s25_05_text_stats_enabled": True,
                "s25_05_mcp_reference_registry_enabled": True,
            })
            with (TestClient(create_app(settings)) as client,
                  evidence_session(settings.database_url) as Session,
                  observe_real_primary_output() as primary_shapes):
                client.headers.update({"Authorization": "Bearer " + token})
                cid_response = client.post("/conversations")
                cid_response.raise_for_status()
                cid = cid_response.json()["id"]
                previous_ok = True
                for stage, prompt in (
                        ("create", CREATE), ("first", FIRST),
                        ("resume", RESUME)):
                    if not previous_ok:
                        records.append({
                            "repeat": repeat, "stage": stage, "pass": False,
                            "failure_kind": "prerequisite_not_verified",
                        })
                        continue
                    started = time.monotonic()
                    primary_start = len(primary_shapes)
                    try:
                        response = client.post(
                            f"/conversations/{cid}/agent/messages",
                            json={"content": prompt,
                                  "client_request_id": "s25-06-e3-" + uuid.uuid4().hex},
                            timeout=max(120.0, settings.model_timeout_seconds * 8))
                        entry = score(stage, response, snapshot(Session, cid))
                    except Exception as exc:
                        entry = {"stage": stage, "pass": False,
                                 "failure_kind": type(exc).__name__}
                    if stage == "create":
                        # Every attempt is bounded shape metadata; this
                        # captures retries without exporting model output.
                        entry["primary_output_shapes"] = primary_shapes[
                            primary_start:primary_start + 4]
                    entry["repeat"] = repeat
                    entry["duration_ms"] = int((time.monotonic() - started) * 1000)
                    records.append(entry)
                    previous_ok = entry["pass"]
    durations = [x["duration_ms"] for x in records if "duration_ms" in x]
    passed = sum(x["pass"] for x in records)
    return {
        "suite": "s25-06-e3-live-v1", "real_provider": True,
        "provider": base.model_provider, "model": base.model_name,
        "routing_profile": "unified", "repeats": repeats,
        "passed": passed, "total": len(records),
        "all_passed": len(records) > 0 and passed == len(records),
        "median_duration_ms": statistics.median(durations) if durations else None,
        "cases": records,
        "limitations": [
            "Real Qwen must run in the operator's configured environment.",
            "Isolated SQLite, local trusted MCP catalog, development bearer only.",
            "No raw provider output, prompts, results, credentials or tokens exported.",
            "PostgreSQL crash/restart and recovery disposition are separate gates.",
            "No clinical or production IAM/effect guarantee is implied.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", default="artifacts/s25-06-e3-live.json")
    args = parser.parse_args()
    report = evaluate(args.repeats)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(json.dumps({
        "passed": report["passed"], "total": report["total"],
        "all_passed": report["all_passed"], "output": str(path),
    }, ensure_ascii=False))
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
