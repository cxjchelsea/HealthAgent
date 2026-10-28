"""Cross-route comparison: observed equality is not execution authority."""
import pytest
from agent.route_shadow import compare_to_legacy
from evals.s25_02_m3c_shadow_comparison import compare_rows


@pytest.mark.parametrize("case,reason,trace,route,verdict", [
    ("create", "plan_created_pending_execution", [], "plan_create", "MATCH"),
    ("revision", "plan_revised_pending_execution", [], "plan_revise", "MATCH"),
    ("missing", "plan_not_found", [], "plan_resume", "MATCH"),
    ("needs_input", "needs_input", [], "clarify", "MATCH"),
    ("tool", None, [{"type": "tool", "name": "calculate"}], "agent_loop", "MATCH"),
    ("scope", None, [{"type": "plan_resume_execution"}], "plan_resume",
     "COMPATIBLE_SCOPE_UNKNOWN"),
    ("false_resume", None, [{"type": "tool", "name": "calculate"}],
     "plan_resume", "MISMATCH"),
])
def test_cross_route_comparison(case, reason, trace, route, verdict):
    legacy = {"status": "partial", "termination_reason": reason,
              "trace": trace}
    shadow = {"state": "valid", "route": route}
    diagnostic = {**shadow, **compare_to_legacy(shadow, legacy),
                  "m3_preturn_identity": {"result": "MATCH"},
                  "m3_checkpoint_snapshot": {"result": "SAME"},
                  "diagnostic_model_calls": 1,
                  "diagnostic_elapsed_ms": 5}
    report = compare_rows([{"case": case, "route_shadow": diagnostic}])
    assert report["rows"][0]["verdict"] == verdict
    assert report["rows"][0]["host_authorized"] is False
    assert report["readiness"] == "HOLD"


def test_shadow_unavailable_and_unknown_not_counted_as_matches():
    data = [
        {"case": "schema", "route_shadow": {"state": "unavailable"}},
        {"case": "ambiguous", "route_shadow": {
            "state": "valid", "comparison": "uncomparable",
            "m3_preturn_identity": {"result": "MATCH"},
            "m3_checkpoint_snapshot": {"result": "SAME"}}},
        {"case": "identity", "route_shadow": {
            "state": "valid", "comparison": "match",
            "m3_preturn_identity": {"result": "MISMATCH"},
            "m3_checkpoint_snapshot": {"result": "SAME"}}},
    ]
    summary = compare_rows(data)["summary"]
    assert summary["UNAVAILABLE"] == 1
    assert summary["UNCOMPARABLE"] == 1
    assert summary["EVIDENCE_GAP"] == 1
    assert summary["MATCH"] == 0


def test_empty_report_cannot_claim_equivalence():
    report = compare_rows([])
    assert report["summary"]["strict_match_rate"] is None
    assert report["readiness"] == "HOLD"


def test_cli_accepts_powershell_utf8_bom(tmp_path, monkeypatch):
    import json
    import sys
    from evals.s25_02_m3c_shadow_comparison import main

    source = tmp_path / "powershell-response.json"
    output = tmp_path / "comparison.json"
    source.write_text(json.dumps({"route_shadow": {
        "state": "valid", "route": "agent_loop", "comparison": "match",
        "m3_preturn_identity": {"result": "MATCH"},
        "m3_checkpoint_snapshot": {"result": "SAME"},
    }}), encoding="utf-8-sig")
    monkeypatch.setattr(sys, "argv", [
        "s25_02_m3c_shadow_comparison", "--input", str(source),
        "--output", str(output),
    ])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["MATCH"] == 1


def test_ungrounded_plan_diagnostic_remains_unavailable():
    report = compare_rows([{"case": "D03_create", "route_shadow": {
        "state": "unavailable",
        "error_class": "ValueError",
        "semantic_schema_valid": True,
        "semantic_proposal": {"route": "plan_create"},
        "semantic_route_comparison_only": "plan_create",
        "evidence_admissible": False,
        "validation_code": "shadow_plan_steps_ungrounded",
        "legacy_route_evidence": "plan_create",
        "comparison": "uncomparable",
        "m3_preturn_identity": {"result": "MATCH"},
        "m3_checkpoint_snapshot": {"result": "UNVERIFIABLE"},
    }}])
    assert report["summary"]["UNAVAILABLE"] == 1
    assert report["summary"]["MATCH"] == 0
    assert report["readiness"] == "HOLD"
