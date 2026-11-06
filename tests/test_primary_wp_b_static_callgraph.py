"""WP-B structural guard: the Primary branch contains no retired model owner.

A function-name-level AST check complements runtime fail-on-call probes; it
cannot alone prove absence of all indirect calls or model generalization.
"""
import ast
from pathlib import Path

RETIRED_OWNERS = frozenset({
    "review_plan_cancellation", "propose_first_route",
    "review_plan_intent", "check_plan_edit_intent",
    "classify_pending_reply", "review_pending_reference_lookup",
    "review_unsupported_capability", "review_read_only_clarification",
    "reconcile_execution_policy", "review_plan_creation_reference",
    "review_missing_plan_reference", "review_plan_execution_authorization",
    "review_plan_execution_scope", "review_plan_revision",
    "propose_new_plan_route", "propose_saved_plan_route",
    "decide_execution_policy_detail", "UnderstandingService",
})


def test_primary_branch_callgraph_excludes_legacy_semantic_owners():
    source = Path("apps/api/main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) ==
            "result is None and settings.primary_route_v1_active"
    ]
    assert len(matches) == 1, (
        "Primary scope changed: review the AST guard before continuing")
    calls = {
        node.func.id
        for node in ast.walk(matches[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
    }
    assert "propose_primary_route" in calls
    assert "admit_primary_route_v1" in calls
    assert "record_primary_admission" in calls
    assert not RETIRED_OWNERS.intersection(calls), (
        "Retired semantic owners cannot be called within Primary execution")


def test_primary_route_is_precedes_legacy_fallback():
    source = Path("apps/api/main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    primary = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.If)
        and ast.unparse(n.test) ==
            "result is None and settings.primary_route_v1_active"
    ]
    assert len(primary) == 1
    legacy_boundary = next(
        i for i, line in enumerate(source.splitlines(), 1)
        if line.strip() == "unified_new_plan_decision = None"
    )
    assert primary[0].end_lineno < legacy_boundary, (
        "Legacy fallback must remain outside the Primary scope")
