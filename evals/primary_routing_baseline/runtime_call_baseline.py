"""Run the existing S25-04 Unified physical suite with call-site instrumentation.

Copy this script to D:\\HealthAgent (root) and run it there. The original source,
model prompts, Host decisions, and tool results remain unchanged.
"""
import asyncio
import collections
import functools
import json
import os
import runpy
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import apps.api.main as api

AC2 = (
    "review_plan_cancellation", "propose_first_route", "review_plan_intent",
    "check_plan_edit_intent", "classify_pending_reply",
    "review_pending_reference_lookup", "review_unsupported_capability",
    "review_read_only_clarification", "reconcile_execution_policy",
    "review_plan_creation_reference", "review_missing_plan_reference",
    "review_plan_execution_authorization", "review_plan_execution_scope",
)
EXTRA = (
    "propose_new_plan_route", "propose_saved_plan_route", "review_plan_revision",
    "decide_execution_policy_detail",
)
CALLS = AC2 + EXTRA
counts = collections.Counter()
durations = collections.defaultdict(float)
failures = collections.Counter()
requests = []


def instrument_async(name, fn):
    @functools.wraps(fn)
    async def wrapped(*args, **kwargs):
        counts[name] += 1
        started = time.monotonic()
        try:
            return await fn(*args, **kwargs)
        except BaseException:
            failures[name] += 1
            raise
        finally:
            durations[name] += (time.monotonic() - started) * 1000
    return wrapped


def instrument_understanding(fn):
    @functools.wraps(fn)
    async def wrapped(*args, **kwargs):
        counts["UnderstandingService.understand"] += 1
        started = time.monotonic()
        try:
            return await fn(*args, **kwargs)
        except BaseException:
            failures["UnderstandingService.understand"] += 1
            raise
        finally:
            durations["UnderstandingService.understand"] += (time.monotonic() - started) * 1000
    return wrapped


def snapshot():
    return dict(counts)


def diff(before, after):
    return {k: after.get(k, 0) - before.get(k, 0)
            for k in sorted(set(before) | set(after))
            if after.get(k, 0) != before.get(k, 0)}


orig_post = TestClient.post


def observed_post(self, url, *args, **kwargs):
    if not (isinstance(url, str) and url.endswith("/agent/messages")):
        return orig_post(self, url, *args, **kwargs)
    # Sequential suite: global per-request snapshots are valid only without concurrency.
    before = snapshot()
    started = time.monotonic()
    try:
        response = orig_post(self, url, *args, **kwargs)
    except BaseException:
        requests.append({"sequence": len(requests) + 1,
                         "http_status": None, "call_counts": diff(before, snapshot()),
                         "elapsed_ms": round((time.monotonic() - started) * 1000),
                         "request_exception": True})
        raise
    requests.append({"sequence": len(requests) + 1,
                     "http_status": response.status_code,
                     "call_counts": diff(before, snapshot()),
                     "elapsed_ms": round((time.monotonic() - started) * 1000)})
    return response


def main():
    if os.getenv("RUN_S25_04_LIVE") != "1":
        raise SystemExit("Set RUN_S25_04_LIVE=1 for this real-provider baseline")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain"], text=True)
    if head != "41d91a3ae3fceeaa6f5401313f7471285b8cd86f":
        raise SystemExit("Expected frozen main@41d91a3; found " + head)
    if os.getenv("ROUTING_PROFILE", "auto") == "legacy":
        raise SystemExit("Legacy routing_profile is not the target of this audit")
    from agent.understanding import UnderstandingService
    from contextlib import ExitStack
    old = UnderstandingService.understand
    try:
        with ExitStack() as stack:
            for name in CALLS:
                fn = getattr(api, name)
                stack.enter_context(patch.object(api, name, instrument_async(name, fn)))
            stack.enter_context(patch.object(UnderstandingService, "understand",
                                             instrument_understanding(old)))
            stack.enter_context(patch.object(TestClient, "post", observed_post))
            # Existing integration suite owns its scenarios and isolated SQLite.
            runpy.run_module("evals.s25_04_unified_physical", run_name="__main__")
    finally:
        output = {
            "kind": "runtime_semantic_call_baseline",
            "source_head": head,
            "working_tree_dirty": bool(status.strip()),
            "scope": "S25-04 Unified physical suite, sequential TestClient HTTP turns",
            "scope_limit": "not exhaustive: counts are observed calls, not all possible branches",
            "ac2_names": list(AC2),
            "ac2_total": sum(counts[x] for x in AC2),
            "ac2_counts": {x: counts[x] for x in AC2},
            "other_counts": {x: counts[x] for x in EXTRA + ("UnderstandingService.understand",)},
            "elapsed_ms_by_function": {x: round(durations[x], 1) for x in CALLS + ("UnderstandingService.understand",)},
            "errors_by_function": dict(failures),
            "requests": requests,
            "limitations": ["No source code changed or model behavior mocked.",
                            "Only Python-level entry calls counted, not nested model HTTP retries.",
                            "Runtime categories require separate baseline for unexercised routes.",
                            "Shadow calls are not AC2 and are excluded from these counters."],
        }
        target = Path("artifacts/primary-routing-main-runtime-baseline.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print("AC2 total observed:", output["ac2_total"])
        print("Per-function counts:", json.dumps(output["ac2_counts"], ensure_ascii=False))
        print("Evidence:", target)


if __name__ == "__main__":
    main()