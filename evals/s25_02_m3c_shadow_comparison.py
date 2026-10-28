"""M3-C shadow comparison report: observation only, never an admission gate."""
import argparse
import json
from collections import Counter
from pathlib import Path

ACCEPTED = {"match", "compatible_plan_execution"}
KNOWN = ACCEPTED | {"mismatch", "uncomparable", "not_comparable_to_semantic_route"}


def compare_rows(rows: list[dict]) -> dict:
    result = []
    for item in rows:
        shadow = item.get("route_shadow")
        if not isinstance(shadow, dict):
            shadow = {}
        state = shadow.get("state")
        comparison = shadow.get("comparison")
        identity = (shadow.get("m3_preturn_identity") or {}).get("result")
        checkpoint = (shadow.get("m3_checkpoint_snapshot") or {}).get("result")
        if state != "valid":
            verdict = "UNAVAILABLE"
        elif identity != "MATCH" or checkpoint not in {"SAME", "UNVERIFIABLE"}:
            verdict = "EVIDENCE_GAP"
        elif comparison in ACCEPTED:
            verdict = "MATCH" if comparison == "match" else "COMPATIBLE_SCOPE_UNKNOWN"
        elif comparison == "mismatch":
            verdict = "MISMATCH"
        else:
            verdict = "UNCOMPARABLE"
        result.append({
            "case": item.get("case"),
            "verdict": verdict,
            "legacy_status": shadow.get("legacy_status"),
            "legacy_reason": shadow.get("legacy_termination_reason"),
            "legacy_route": shadow.get("legacy_route_evidence"),
            "shadow_route": shadow.get("route"),
            "shadow_state": state or "missing",
            "host_authorized": False,
            "legacy_tool_calls": shadow.get("legacy_tool_calls"),
            "diagnostic_model_calls": shadow.get("diagnostic_model_calls"),
            "diagnostic_elapsed_ms": shadow.get("diagnostic_elapsed_ms"),
        })
    counts = Counter(x["verdict"] for x in result)
    strict = counts["MATCH"]
    compatible = counts["COMPATIBLE_SCOPE_UNKNOWN"]
    total = len(result)
    return {
        "schema": "s25-m3c-shadow-comparison-v1",
        "summary": {k: counts[k] for k in (
            "MATCH", "COMPATIBLE_SCOPE_UNKNOWN", "MISMATCH",
            "UNCOMPARABLE", "UNAVAILABLE", "EVIDENCE_GAP")} | {
                "total": total, "strict_match_rate": strict / total if total else None},
        "readiness": "HOLD",
        "rows": result,
        "limitations": [
            "Compatible saved-plan execution does not prove first/all scope equality.",
            "Legacy outcome evidence is not a ground-truth semantic label.",
            "Diagnostic model call count excludes old path and model-internal retries.",
            "No shadow observation grants authorization or permits cutover.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if isinstance(report, dict) and isinstance(report.get("rows"), list):
        rows = report["rows"]
    elif isinstance(report, dict) and isinstance(report.get("route_shadow"), dict):
        rows = [report]
    else:
        parser.error("input must be a response with route_shadow or report with rows array")
    output = compare_rows(rows)
    Path(args.output).write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["summary"], ensure_ascii=False))
    raise SystemExit(1 if output["summary"]["MISMATCH"] or
                     output["summary"]["EVIDENCE_GAP"] else 0)


if __name__ == "__main__":
    main()
