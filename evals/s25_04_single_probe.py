"""S25-04 focused diagnostic: one isolated API request, safe metadata only.

Set RUN_S25_04_LIVE=1; does not call a write tool or export prompt text.
"""
import json
import os
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from agent.config import Settings
from apps.api.main import create_app


def main():
    if os.getenv("RUN_S25_04_LIVE") != "1":
        raise RuntimeError("RUN_S25_04_LIVE=1 required")
    base = Settings()
    if base.model_provider == "fake":
        raise RuntimeError("Real provider required")
    folder = Path("evals/results/s25-04")
    folder.mkdir(parents=True, exist_ok=True)
    dbfile = folder / ("probe-" + uuid.uuid4().hex + ".sqlite")
    settings = Settings(**{
        **base.model_dump(), "database_url": "sqlite:///" + str(dbfile.resolve()).replace("\\", "/"),
        "skill_strategy": "semantic_topk", "skill_budget_chars": 2000,
        "routing_profile": "legacy", "route_admission_mode": "legacy",
        "understanding_strategy": "direct", "m5_unified_route_enabled": False,
        "m5_first_route_enabled": False,
    })
    try:
        with TestClient(create_app(settings)) as client:
            cid = client.post("/conversations").json()["id"]
            res = client.post(f"/conversations/{cid}/agent/messages", json={
                "content": "请查询当前会话已保存的待办事项，若没有请如实报告。",
                "client_request_id": str(uuid.uuid4())
            })
            obj = res.json()
            detail = obj.get("detail") if isinstance(obj.get("detail"), dict) else {}
            trace = obj.get("trace") or []
            report = {
                "suite": "s25-04-single-readonly-probe-v1",
                "provider": settings.model_provider, "model": settings.model_name,
                "routing_profile": settings.effective_routing_profile,
                "http_status": res.status_code,
                "reason_code": detail.get("code") or obj.get("failure_code")
                               or obj.get("termination_reason"),
                "agent_status": obj.get("status"),
                "execution_mode": obj.get("execution_mode"),
                "trace_types": [x.get("type") for x in trace],
                "tool_events": [{"name": x.get("name"), "ok": x.get("ok"),
                                 "error_code": x.get("error_code")}
                                for x in trace if x.get("type") == "tool"],
                "model_http_attempts": res.headers.get("X-HealthAgent-Model-HTTP-Attempts"),
                "model_call_categories": res.headers.get("X-HealthAgent-Model-Call-Categories"),
                "notes": ["No prompts, model response text, user content or values exported.",
                          "One isolated read-only request. Does not establish write safety."]
            }
        target = folder / "single-readonly-probe-v1.json"
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("Evidence:", target)
    finally:
        dbfile.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
