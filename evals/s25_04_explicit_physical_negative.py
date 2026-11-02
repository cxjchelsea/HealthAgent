"""Physical negative acceptance for explicit ordinary calculation.

Requires a running real-provider API. This harness NEVER silently passes
when the saved-plan fixture was not created. Uses public owner-scoped HTTP.
"""
import json
import os
import uuid
from pathlib import Path

import httpx


BASE = os.getenv("S25_04_ACCEPT_BASE", "http://127.0.0.1:8012").rstrip("/")
TOKEN = os.getenv("S25_04_ACCEPT_TOKEN", "")
HEADERS = {"Authorization": "Bearer " + TOKEN} if TOKEN else {}
OUT = Path("evals/results/s25-04/explicit-physical-negative-v1.json")


def main():
    report = {"suite": "s25-04-explicit-physical-negative-v1",
              "base": BASE, "cases": [], "passed": False}

    def check(name, good, **details):
        report["cases"].append({"case": name, "passed": bool(good), **details})

    def new_id():
        return str(uuid.uuid4())

    def send(client, cid, content, operation=None, request_id=None):
        payload = {"content": content, "client_request_id": request_id or new_id()}
        if operation is not None:
            payload["selected_ordinary_operation"] = operation
        response = client.post(f"/conversations/{cid}/agent/messages", json=payload)
        try:
            data = response.json()
        except ValueError:
            data = {"unparsed": response.text[:200]}
        return response.status_code, data, payload["client_request_id"]

    try:
        with httpx.Client(base_url=BASE, headers=HEADERS, timeout=180.0) as client:
            profile = client.get("/model")
            profile.raise_for_status()
            info = profile.json()
            report["routing_profile"] = info.get("routing_profile")
            if info.get("routing_profile") != "unified":
                raise RuntimeError("Unified routing profile is required")
            response = client.post("/conversations")
            response.raise_for_status()
            cid = response.json()["id"]
            report["conversation_id"] = cid

            # Positive baseline plus exact replay of the same Turn/request_id.
            status, result, rid = send(client, cid, "请计算 (19+23)*4", "calculate")
            tools = [e for e in result.get("trace", []) if e.get("type") == "tool"]
            check("explicit_calculate", status == 200 and
                  result.get("status") == "completed" and
                  len(tools) == 1 and tools[0].get("name") == "calculate" and
                  tools[0].get("ok") is True and
                  result.get("plan_checkpoint") is None,
                  http_status=status, termination=result.get("termination_reason"),
                  tools=len(tools))
            again, replay, _ = send(client, cid, "请计算 (19+23)*4",
                                    "calculate", request_id=rid)
            check("replay_rejected", again == 409,
                  http_status=again, response=replay)

            # Mixed text cannot use the explicit selector.
            mixed = "请计算 (19+23)*4，并保存执行计划"
            mixed_status, mixed_result, _ = send(client, cid, mixed, "calculate")
            mixed_tools = [e for e in mixed_result.get("trace", [])
                           if e.get("type") == "tool"]
            mixed_plan = mixed_result.get("plan_checkpoint")
            check("mixed_not_dispatched_as_calculation",
                  not any(e.get("name") == "calculate" and e.get("ok")
                          for e in mixed_tools) and not mixed_plan,
                  http_status=mixed_status,
                  termination=mixed_result.get("termination_reason"),
                  trace_tools=mixed_tools,
                  checkpoint_present=bool(mixed_plan))

            # A saved plan MUST exist to test the pre-turn checkpoint guard.
            created_status, created, _ = send(
                client, cid,
                "请创建一个两步可执行计划：第一步计算 3+4，第二步计算 8+9。只保存，不执行。")
            created_ok = (
                created_status == 200 and
                created.get("termination_reason") == "plan_created_pending_execution"
                and bool(created.get("plan_checkpoint")))
            check("saved_plan_fixture_created", created_ok,
                  http_status=created_status,
                  termination=created.get("termination_reason"),
                  checkpoint_present=bool(created.get("plan_checkpoint")))
            if created_ok:
                checkpoint_before = created["plan_checkpoint"]
                guarded_status, guarded, _ = send(client, cid,
                    "请计算 (19+23)*4", "calculate")
                checkpoint_after_response = client.get(f"/conversations/{cid}")
                checkpoint_after_response.raise_for_status()
                # Explicit operation cannot force tool dispatch while a plan exists.
                guard_tools = [e for e in guarded.get("trace", [])
                               if e.get("type") == "tool" and
                               e.get("name") == "calculate" and e.get("ok")]
                check("saved_plan_prevents_explicit_bypass",
                      not guard_tools and not guarded.get("plan_cancellation_requested"),
                      http_status=guarded_status,
                      termination=guarded.get("termination_reason"),
                      tool_invocations=len(guard_tools))
                report["checkpoint_fixture_revision"] = checkpoint_before.get("revision")
            else:
                check("saved_plan_prevents_explicit_bypass", False,
                      reason="fixture_not_established")

            # Read-back checks (owner scoped API): no fabricated side effects.
            detail = client.get(f"/conversations/{cid}")
            detail.raise_for_status()
            memories = client.get("/memories")
            memories.raise_for_status()
            task_state = client.get(f"/conversations/{cid}/agent/task-state")
            task_state.raise_for_status()
            evidence = client.get(f"/conversations/{cid}/turns/{result['turn_id']}/evidence")
            evidence.raise_for_status()
            report["readback"] = {
                "conversation_http": detail.status_code,
                "memories_http": memories.status_code,
                "task_state_http": task_state.status_code,
                "evidence_http": evidence.status_code,
                "baseline_attempts": evidence.json().get("attempts", []),
            }
            check("readback_endpoints_accessible", True)
    except Exception as exc:
        report["infrastructure_error"] = type(exc).__name__ + ": " + str(exc)[:240]
    report["passed"] = bool(report["cases"]) and all(
        case["passed"] for case in report["cases"]) and "infrastructure_error" not in report
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Evidence:", OUT)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
