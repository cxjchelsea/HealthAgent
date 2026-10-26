"""M3-B API audit must not count missing identity or uncomparable routes as PASS."""
from evals.s25_02_m3_api_audit import audit


def item(comparison="match", preturn="MATCH"):
    return {"case":"F02_calculation","conversation_id":"c",
            "client_request_id":"r","turn_id":"t","message_id":"m",
            "shadow_result":"PASS","route_shadow":{
                "state":"valid","comparison":comparison,
                "m3_preturn_identity":{"result":preturn}}}


def test_exact_route_and_identity_passes_as_audit_only():
    report=audit({"rows":[item()]})
    assert report["summary"]["PASS"]==1
    assert report["m3_b_gate"]=="HOLD"
    assert report["rows"][0]["host_admission_verified"] is False


def test_missing_preturn_fails():
    report=audit({"rows":[item(preturn="MISMATCH")]})
    assert report["summary"]["FAIL"]==1


def test_plan_compatibility_is_review_not_strict_pass():
    report=audit({"rows":[item(comparison="compatible_plan_execution")]})
    assert report["summary"]["REVIEW"]==1


def test_unknown_legacy_comparison_does_not_pass():
    report=audit({"rows":[item(comparison="uncomparable")]})
    assert report["summary"]["REVIEW"]==1


def test_missing_request_identity_fails():
    sample=item()
    sample["client_request_id"]=None
    assert audit({"rows":[sample]})["summary"]["FAIL"]==1
