"""M3-B physical real API report audit: fail closed on absent preturn identity.

This is a report checker, not a proof of authenticated pre-execution permission.
"""
import argparse
import json
from pathlib import Path


def audit(report: dict) -> dict:
    rows = []
    for item in report.get("rows", []):
        shadow = item.get("route_shadow") or {}
        pre = shadow.get("m3_preturn_identity") or {}
        identity_ok = all(item.get(k) for k in
                          ("conversation_id", "client_request_id", "turn_id", "message_id"))
        pre_ok = pre.get("result") == "MATCH"
        shadow_ok = shadow.get("state") == "valid" and item.get("shadow_result") == "PASS"
        comparison = shadow.get("comparison")
        strict_match = comparison == "match"
        status = ("PASS" if identity_ok and pre_ok and shadow_ok and strict_match
                  else "REVIEW" if identity_ok and pre_ok and shadow_ok
                  and comparison in {"compatible_plan_execution", "uncomparable"}
                  else "FAIL")
        rows.append({"case": item.get("case"), "status": status,
                     "identity": "present" if identity_ok else "missing",
                     "preturn": pre.get("result", "missing"),
                     "legacy_comparison": comparison or "missing",
                     "host_admission_verified": False})
    def count(verdict: str) -> int:
        return sum(row["status"] == verdict for row in rows)
    return {"source": "m3_b_real_api_same_turn_report_audit",
            "summary": {"PASS": count("PASS"), "REVIEW": count("REVIEW"),
                        "FAIL": count("FAIL"), "total": len(rows)},
            "m3_b_gate": "HOLD",
            "rows": rows,
            "limitations": [
                "Exact identity and old-route compatibility do not prove a preturn authorization snapshot.",
                "M3 target/scope/effect and independent tool schema facts are not yet fully captured.",
                "No admission or cutover may be inferred from this report.",
            ]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = audit(json.loads(Path(args.live_report).read_text(encoding="utf-8")))
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                encoding="utf-8")
    print(result["summary"])
    raise SystemExit(1 if result["summary"]["FAIL"] else 0)


if __name__ == "__main__":
    main()
