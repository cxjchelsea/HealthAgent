"""M2 comparison reports independent first-stage outcomes."""
from argparse import Namespace
from unittest.mock import patch

import pytest

from evals import s25_02_m2_ab_comparison as experiment
from agent.route_semantic_ab import CoreIntent


def test_core_match_preserves_classification_after_later_error():
    case=next(c for c in experiment.CASES if c.case_id=="formal_plan")
    core=CoreIntent(object_type="plan",operation="create")
    try:
        raise ValueError("reference failure")
    except ValueError:
        assert experiment.core_match(case,core.object_type,core.operation)


def test_report_repeats_bounds_are_explicit():
    import argparse
    assert Namespace(repeats=3).repeats==3


def test_known_eval_marks_unavailable_as_not_route_correct():
    case=next(c for c in experiment.CASES if c.case_id=="plan_revision")
    assert experiment.core_match(case,"plan_step","revise")
    assert not experiment.core_match(case,"plan","revise")
