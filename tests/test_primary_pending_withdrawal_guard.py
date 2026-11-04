"""Explicit withdrawal is a restrictive Host veto, never a new route owner."""
import pytest

from agent.pending_write_withdrawal_guard import explicitly_withdraws_pending_task_write
from agent.primary_host_admission_v1 import PrimaryHostFacts, admit_primary_route_v1
from agent.primary_route_v1 import RouteSignal, bind_route_signal


@pytest.mark.parametrize("utterance", [
    "算了，这个待办不创建了。",
    "不创建了",
    "不要创建任务",
    "这个任务不用创建了。",
    "取消这个待办",
    "Never mind, skip this",
])
def test_pending_write_explicit_withdrawal_blocks_effect(utterance):
    assert explicitly_withdraws_pending_task_write(utterance)
    proposal = bind_route_signal(
        RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "clarify_response", "quote": utterance,
        }), utterance, saved_plan_present=False, pending_action="create_task",
        pending_origin_verified=True)
    decision = admit_primary_route_v1(proposal, PrimaryHostFacts(
        checkpoint=None, pending_action="create_task",
        pending_origin_verified=True, pending_write_authority_verified=True,
        guarded_mode=True, turn_created=True, current_request=utterance))
    assert decision.disposition == "restrict"
    assert decision.reason == "primary_v1_pending_task_withdrawn"
    assert decision.route_kind == "clarify_response"


@pytest.mark.parametrize("utterance", [
    "整理虚构实验设备清单",
    "检查模拟仪器序列号",
    "整理取消订单的记录",
])
def test_pending_write_valid_titles_keep_authorization(utterance):
    assert not explicitly_withdraws_pending_task_write(utterance)
    proposal = bind_route_signal(
        RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "clarify_response", "quote": utterance,
        }), utterance, saved_plan_present=False, pending_action="create_task",
        pending_origin_verified=True)
    decision = admit_primary_route_v1(proposal, PrimaryHostFacts(
        checkpoint=None, pending_action="create_task",
        pending_origin_verified=True, pending_write_authority_verified=True,
        guarded_mode=True, turn_created=True, current_request=utterance))
    assert decision.disposition == "allow"
    assert decision.reason == "primary_v1_pending_task_authorized"


def test_unverified_pending_stays_denied_despite_good_title():
    utterance = "整理模拟设备清单"
    proposal = bind_route_signal(
        RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "clarify_response", "quote": utterance,
        }), utterance, saved_plan_present=False, pending_action="create_task",
        pending_origin_verified=True)
    decision = admit_primary_route_v1(proposal, PrimaryHostFacts(
        checkpoint=None, pending_action="create_task",
        pending_origin_verified=False, pending_write_authority_verified=False,
        guarded_mode=True, turn_created=True, current_request=utterance))
    assert decision.disposition == "deny"
    assert decision.reason == "primary_v1_pending_origin_unverified"
