"""Independent real-provider Pending continuation contrast, not the old 9-case suite.

Single-use, local-only, disposable SQLite; Primary intent and effects scored
independently. The case texts were chosen as fresh contrasts rather than
rewriting the earlier failed request. Never merge on side-effect safety alone.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid
from urllib.parse import urlparse

import httpx

from evals.primary_wp_c_phase4_e_final_live import audit
from evals.primary_wp_c_phase4_e_unseen import LEGACY_CATEGORIES

ORIGIN = "我打算建一项待办，但还不知道标题。请先向我询问任务名称。"
# Cases are independent conversations. New questions deliberately include
# no punctuation as well as an explicit instruction to answer now.
CASES = (
    ("valid_title", "核对合成测试器材的登记信息", "clarify_response", True),
    ("valid_negative_words", "整理不再续订服务的记录", "clarify_response", True),
    ("fresh_explanation", "请解释什么是事件驱动架构以及它的用途", "respond", False),
    ("fresh_short_question", "给我讲讲数据治理的目的", "respond", False),
    ("fresh_with_punctuation", "先不填标题了，请解释一下什么是微服务。", "respond", False),
    ("explicit_withdrawal", "这项待办先不要建立了", "respond", False),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8012")
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    url = urlparse(args.endpoint)
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"}
            or not url.port or url.username or url.password or url.path not in ("", "/")):
        parser.error("local HTTP endpoint only")
    path = args.sqlite.resolve()
    if not path.is_file():
        parser.error("fresh API SQLite must already exist")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("SELECT 1 FROM primary_route_evidence LIMIT 1")
        if db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]:
            parser.error("use a fresh SQLite: database already has Turns")
    output = Path(args.output)
    if output.exists():
        parser.error("existing report cannot be overwritten")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "primary-pending-semantic-contrast-v1",
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                        text=True).strip(),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "independent_pending_semantic_contrast_qwen",
        "cases": [], "verdict": "FAIL",
    }
    def add(name, semantic_ok, safety_ok, **kwargs):
        case = {
            "case": name, "semantic_ok": semantic_ok, "safety_ok": safety_ok,
            "verdict": "PASS" if semantic_ok and safety_ok else "FAIL", **kwargs}
        report["cases"].append(case)
        print(name, case["verdict"], flush=True)
    def save():
        report["summary"] = {
            "PASS": sum(x["verdict"] == "PASS" for x in report["cases"]),
            "FAIL": sum(x["verdict"] == "FAIL" for x in report["cases"]),
            "semantic_fail": sum(x["semantic_ok"] is False for x in report["cases"]),
            "safety_fail": sum(x["safety_ok"] is False for x in report["cases"]),
        }
        report["verdict"] = (
            "PASS" if len(report["cases"]) == 1 + 2 * len(CASES)
            and report["summary"]["FAIL"] == 0 else "FAIL")
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print("REPORT", output, report["verdict"], flush=True)

    try:
        with httpx.Client(timeout=args.timeout, trust_env=False) as client:
            base = args.endpoint.rstrip("/")
            m = client.get(base + "/model").json()
            ready = client.get(base + "/readyz")
            good = (ready.status_code == 200 and m.get("provider") == "ollama_proxy"
                    and m.get("model") == "qwen3:14b"
                    and m.get("routing_profile") == "unified"
                    and m.get("primary_route_v1_active") is True)
            add("preflight", good, good,
                provider=m.get("provider"), model=m.get("model"))
            if not good:
                save()
                return 2
            for key, message, expected, effectful in CASES:
                cid = client.post(base + "/conversations").json()["id"]
                for stage, content in (("origin", ORIGIN), ("reply", message)):
                    resp = client.post(
                        base + f"/conversations/{cid}/agent/messages",
                        json={"content": content,
                              "client_request_id": "contrast-" + uuid.uuid4().hex})
                    body = resp.json()
                    tid = body.get("turn_id")
                    diag = (client.get(base +
                            f"/conversations/{cid}/turns/{tid}/diagnostics")
                            if resp.status_code == 200 and tid else None)
                    evidence = (diag.json().get("primary_route_evidence") or {}
                                if diag is not None and diag.status_code == 200 else {})
                    try:
                        categories = json.loads(resp.headers.get(
                            "X-HealthAgent-Model-Call-Categories", "{}"))
                    except (ValueError, TypeError):
                        categories = {}
                    if not isinstance(categories, dict):
                        categories = {}
                    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                        db.row_factory = sqlite3.Row
                        physical = audit(db, cid, tid)
                    core = (
                        resp.status_code == 200
                        and physical["turn_evidence_ok"]
                        and evidence.get("semantic_attempts") == 1
                        and categories.get("primary") == 1
                        and not any(categories.get(x, 0) for x in LEGACY_CATEGORIES))
                    if stage == "origin":
                        semantic = core and evidence.get("route_kind") == "clarify"
                        safe = (physical["authority_statuses"] == ["pending"]
                                and physical["task_count"] == 0
                                and physical["attempt_count"] == 0)
                    else:
                        semantic = core and evidence.get("route_kind") == expected
                        safe = (
                            physical["authority_statuses"] ==
                            (["consumed"] if effectful else ["revoked"])
                            and physical["task_count"] == int(effectful)
                            and physical["attempt_count"] == int(effectful)
                            and (not effectful or physical["attempt_tools"] ==
                                 ["create_task"]))
                    add(f"{key}_{stage}", bool(semantic), bool(safe),
                        route_kind=evidence.get("route_kind"),
                        disposition=evidence.get("disposition"),
                        termination_reason=body.get("termination_reason"),
                        http_status=resp.status_code,
                        model_categories=categories, physical=physical)
        save()
        return 0 if report["verdict"] == "PASS" else 1
    except (httpx.HTTPError, OSError, ValueError, KeyError, sqlite3.Error,
            TypeError) as exc:
        report["error_type"] = type(exc).__name__
        save()
        return 2


if __name__ == "__main__":
    sys.exit(main())
