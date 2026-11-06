"""Contract-level tests for work package 1 (no provider/network/tool execution)."""
import asyncio
import json

import pytest
from pydantic import ValidationError

from agent.primary_route_v1 import (
    ROUTE_SCHEMA_VERSION, RouteSignal, PrimaryRouteRejected,
    adapt_legacy_route, bind_route_signal, propose_primary_route,
)


def signal(route_kind="respond", quote="你好", **kwargs):
    body = {
        "schema_version": ROUTE_SCHEMA_VERSION, "route_kind": route_kind,
        "quote": quote, "execution_scope": "none", "effect_intent": "none",
        "steps": [],
    }
    body.update(kwargs)
    return body


def test_primary_model_call_attribution_is_request_scoped():
    from agent.m5_model_call_metrics import model_call_category

    class InspectingProvider:
        def __init__(self):
            self.categories = []

        async def complete(self, **kwargs):
            self.categories.append(model_call_category.get())
            return json.dumps(signal("respond", "你好"), ensure_ascii=False)

    provider = InspectingProvider()
    assert model_call_category.get() == "other"
    selected = asyncio.run(propose_primary_route(
        provider, "test", "你好", saved_plan_present=False))
    assert selected.route_kind == "respond"
    assert provider.categories == ["primary"]
    assert model_call_category.get() == "other"


class Provider:
    def __init__(self, body):
        self.body = body
        self.calls = 0
        self.last_schema = None

    async def complete(self, *, model, messages, response_schema):
        self.calls += 1
        self.last_schema = response_schema
        return json.dumps(self.body, ensure_ascii=False)


@pytest.mark.parametrize("wrong", [
    {"schema_version": "s25-m5-primary-v1"},
    {"route_kind": "other"},
    {"route_kind": "execute_first"},
    {"unexpected": 1},
    {"execution_scope": "single_tool"},
    {"steps": [{"goal": "bad", "tool_name": "calculate",
                "arguments": {"expression": "2+2"}, "quote": "2+2"}]},
])
def test_schema_rejects_wrong_version_alias_and_payload(wrong):
    with pytest.raises(ValidationError):
        RouteSignal.model_validate(signal(**wrong))


@pytest.mark.parametrize("legacy,canonical", [
    ("create", "plan_create"), ("revise", "plan_revise"),
    ("execute_first", "plan_execute_first"), ("resume", "plan_resume"),
    ("cancel", "plan_cancel"),
])
def test_legacy_adapter_is_explicit(legacy, canonical):
    assert adapt_legacy_route(legacy) == canonical


@pytest.mark.parametrize("unmapped", ["other", "agent_loop", "respond", "ordinary_tool", "plan_only", ""])
def test_legacy_adapter_must_not_guess(unmapped):
    with pytest.raises(PrimaryRouteRejected, match="primary_route_legacy_unmapped"):
        adapt_legacy_route(unmapped)


def test_exact_unique_evidence_not_offsets_from_model():
    payload = RouteSignal.model_validate(signal())
    assert bind_route_signal(payload, "请说你好", saved_plan_present=False,
                             pending_action=None).evidence.start == 2
    with pytest.raises(PrimaryRouteRejected, match="primary_route_quote_ambiguous"):
        bind_route_signal(payload, "你好你好", saved_plan_present=False,
                          pending_action=None)
    with pytest.raises(PrimaryRouteRejected, match="primary_route_quote_not_found"):
        bind_route_signal(payload, "再见", saved_plan_present=False, pending_action=None)


def test_plan_route_never_creates_own_checkpoint():
    payload = RouteSignal.model_validate(signal(
        "plan_resume", "继续执行", execution_scope="remaining_steps",
        effect_intent="read_only"))
    with pytest.raises(PrimaryRouteRejected, match="primary_route_missing_saved_plan"):
        bind_route_signal(payload, "继续执行", saved_plan_present=False,
                          pending_action=None)
    approved_as_proposal = bind_route_signal(
        payload, "继续执行", saved_plan_present=True, pending_action=None)
    assert approved_as_proposal.route_kind == "plan_resume"
    assert not hasattr(approved_as_proposal, "admission_result")


def test_saved_plan_must_not_capture_ordinary_request():
    payload = RouteSignal.model_validate(signal(
        "ordinary_tool", "请计算", execution_scope="none",
        effect_intent="read_only"))
    bound = bind_route_signal(payload, "请计算 1+2", saved_plan_present=True,
                              pending_action=None)
    assert bound.route_kind == "ordinary_tool"


def test_pending_reply_requires_durable_original_request():
    payload = RouteSignal.model_validate(signal(
        "clarify_response", "MCP"))
    with pytest.raises(PrimaryRouteRejected, match="primary_route_untrusted_pending_origin"):
        bind_route_signal(payload, "MCP", saved_plan_present=False,
                          pending_action="mcp_reference_lookup")
    bound = bind_route_signal(
        payload, "MCP", saved_plan_present=False,
        pending_action="mcp_reference_lookup", pending_origin_verified=True)
    assert bound.route_kind == "clarify_response"


def test_plan_create_requires_grounded_steps_and_only_saves():
    phrase = "只保存两步：计算3+4，再查询MCP"
    payload = RouteSignal.model_validate(signal(
        "plan_create", "只保存两步", effect_intent="mutation_requested",
        steps=[
            {"goal": "计算", "tool_name": "calculate",
             "arguments": {"expression": "3+4"}, "quote": "计算3+4"},
            {"goal": "查询", "tool_name": "mcp_reference_lookup",
             "arguments": {"term": "MCP"}, "quote": "查询MCP"},
        ]))
    b = bind_route_signal(payload, phrase, saved_plan_present=False,
                          pending_action=None)
    assert b.route_kind == "plan_create" and len(b.steps) == 2
    with pytest.raises(PrimaryRouteRejected, match="primary_route_unbound_step_args"):
        bind_route_signal(payload.model_copy(update={
            "steps": [payload.steps[0].model_copy(update={
                "arguments": {"expression": "9+9"}}), payload.steps[1]]}),
            phrase, saved_plan_present=False, pending_action=None)


def test_revision_needs_host_verified_step_number():
    payload = RouteSignal.model_validate(signal(
        "plan_revise", "修改第二步", effect_intent="mutation_requested",
        step_number=2, revision_old_argument="3+4",
        revision_new_argument="7+8"))
    with pytest.raises(PrimaryRouteRejected, match="primary_route_unbound_step"):
        bind_route_signal(payload, "修改第二步由3+4改成7+8", saved_plan_present=True,
                          pending_action=None, saved_step_count=1)
    result = bind_route_signal(payload, "修改第二步由3+4改成7+8", saved_plan_present=True,
                               pending_action=None, saved_step_count=2)
    assert result.step_number == 2


def test_exactly_one_semantic_model_call_no_legacy_fallback():
    provider = Provider(signal(
        "ordinary_tool", "请计算", execution_scope="none",
        effect_intent="read_only"))
    route = asyncio.run(propose_primary_route(provider, "model", "请计算 2+3",
                                        saved_plan_present=False))
    assert route.route_kind == "ordinary_tool"
    assert provider.calls == 1
    assert provider.last_schema["properties"]["schema_version"]


def test_model_invalid_output_fails_closed_without_retry():
    provider = Provider(signal(route_kind="other"))
    with pytest.raises(PrimaryRouteRejected, match="primary_route_invalid_signal"):
        asyncio.run(propose_primary_route(provider, "model", "你好",
                                    saved_plan_present=False))
    assert provider.calls == 1


def test_model_plan_evidence_invalid_fails_closed():
    provider = Provider(signal(
        "plan_cancel", "取消不存在的文字", effect_intent="mutation_requested"))
    with pytest.raises(PrimaryRouteRejected, match="primary_route_quote_not_found"):
        asyncio.run(propose_primary_route(provider, "model", "取消当前计划",
                                    saved_plan_present=True))
    assert provider.calls == 1



def test_invalid_primary_schema_reports_safe_field_categories_only():
    """Model output and Pydantic messages must not leak in diagnostics."""
    secret = "private-prompt-canary-92831"
    provider = Provider({
        "schema_version": "primary-route-v1",
        "route_kind": "ordinary_tool",
        "quote": "请计算",
        "effect_intent": "unsafe_value_" + secret,
        "execution_scope": "none",
        "steps": [],
        "unexpected_" + secret: secret,
    })
    with pytest.raises(PrimaryRouteRejected) as caught:
        asyncio.run(propose_primary_route(provider, "model",
                                          "请计算 2+3",
                                          saved_plan_present=False))
    assert str(caught.value) == "primary_route_invalid_signal"
    issues = caught.value.validation_issues
    assert {"field": "effect_intent", "category": "literal_error"} in issues
    assert {"field": "root", "category": "extra_forbidden"} in issues
    assert secret not in json.dumps(issues)
    assert provider.calls == 1


def test_model_validator_mismatch_keeps_single_safe_category():
    provider = Provider(signal(
        "plan_create", "创建计划", effect_intent="mutation_requested",
        steps=[]))
    with pytest.raises(PrimaryRouteRejected) as caught:
        asyncio.run(propose_primary_route(provider, "model",
                                          "请创建计划", saved_plan_present=False))
    assert caught.value.validation_issues == [
        {"field": "root", "category": "primary_route_steps_mismatch"}]
    assert provider.calls == 1



@pytest.mark.parametrize("candidate,reason", [
    (signal("ordinary_tool", "请计算", effect_intent="read_only",
            write_tool_name="create_task", write_arguments={"title": "秘密"}),
     "primary_route_unexpected_write_contract"),
    (signal("respond", "你好", effect_intent="read_only"),
     "primary_route_scope_effect_mismatch"),
    (signal("plan_cancel", "取消计划", effect_intent="mutation_requested",
            step_number=1),
     "primary_route_unexpected_step"),
    (signal("clarify", "帮我看看这个"),
     "primary_route_clarification_mismatch"),
    (signal("ordinary_tool", "创建任务", effect_intent="mutation_requested"),
     "primary_route_write_contract_missing"),
])
def test_route_invariants_produce_distinct_safe_diagnostic_codes(candidate, reason):
    provider = Provider(candidate)
    with pytest.raises(PrimaryRouteRejected) as caught:
        asyncio.run(propose_primary_route(
            provider, "model", candidate["quote"],
            saved_plan_present=False))
    assert str(caught.value) == "primary_route_invalid_signal"
    assert caught.value.validation_issues == [
        {"field": "root", "category": reason}]
    assert provider.calls == 1



def test_qwen_prompt_matches_strict_route_scope_contract():
    """Generation guidance must not present a tool request as plan execution."""
    from agent.primary_route_v1 import _PRIMARY_SYSTEM
    schema = RouteSignal.model_json_schema()
    scope_enum = schema["properties"]["execution_scope"]["enum"]
    assert "single_tool" not in scope_enum
    assert "none" in scope_enum
    assert "execution_scope=none, effect_intent=read_only" in _PRIMARY_SYSTEM
    assert "ordinary_tool/none/read_only" in _PRIMARY_SYSTEM
    assert "plan_create/none/mutation_requested" in _PRIMARY_SYSTEM
    assert "respond/none/none" in _PRIMARY_SYSTEM
    assert "plan_missing/none/none" in _PRIMARY_SYSTEM
    # The worked sample must be parseable, correctly scoped and marked as
    # non-authoritative; no semantic correction is performed by the parser.
    marker = "Read-only tool OUTPUT FORMAT EXAMPLE ONLY (not a default route): "
    fragment = _PRIMARY_SYSTEM.split(marker, 1)[1].split(
        " Never copy the example quote", 1)[0]
    worked = RouteSignal.model_validate_json(fragment)
    assert worked.route_kind == "ordinary_tool"
    assert worked.execution_scope == "none"
    assert worked.effect_intent == "read_only"


def test_mismatched_scope_still_fails_closed_without_repair():
    invalid = signal(
        "ordinary_tool", "请计算", execution_scope="first_step",
        effect_intent="read_only")
    provider = Provider(invalid)
    with pytest.raises(PrimaryRouteRejected) as caught:
        asyncio.run(propose_primary_route(
            provider, "model", "请计算 2+3", saved_plan_present=False))
    assert caught.value.validation_issues == [{
        "field": "root",
        "category": "primary_route_scope_effect_mismatch",
    }]
    assert provider.calls == 1



def test_scope_mismatch_exposes_only_allowlisted_enum_triple():
    secret = "dont-leak-secret-literal-724"
    provider = Provider(signal(
        "ordinary_tool", secret, execution_scope="first_step",
        effect_intent="read_only"))
    with pytest.raises(PrimaryRouteRejected) as caught:
        asyncio.run(propose_primary_route(
            provider, "model", "请计算 2+3", saved_plan_present=False))
    # Extra fields are rejected; the root mismatch still reports bounded
    # public enum labels. Untrusted values never enter a diagnostic.
    assert caught.value.route_triple == {
        "route_kind": "ordinary_tool", "execution_scope": "first_step",
        "effect_intent": "read_only"}
    assert secret not in json.dumps(caught.value.route_triple)
    assert provider.calls == 1


def test_untrusted_route_triple_values_are_never_returned():
    from agent.primary_route_v1 import safe_route_triple
    secret = "private-part-abc-7123"
    data = {
        "route_kind": secret,
        "execution_scope": "single_tool",
        "effect_intent": secret,
        "quote": secret,
    }
    issues = [{"field": "root",
               "category": "primary_route_scope_effect_mismatch"}]
    assert safe_route_triple(json.dumps(data), issues) == {
        "route_kind": "invalid",
        "execution_scope": "invalid",
        "effect_intent": "invalid"}
    assert safe_route_triple(json.dumps(data), []) is None



def test_missing_saved_plan_prompt_uses_trusted_snapshot_without_execution():
    """Plan existence is a Host fact; the model never gains write or run rights."""
    from agent.primary_route_v1 import _PRIMARY_SYSTEM
    assert "saved_plan_exists=false" in _PRIMARY_SYSTEM
    assert "saved_plan_exists=true" in _PRIMARY_SYSTEM
    assert "plan_missing/none/none" in _PRIMARY_SYSTEM
    assert "plan_resume/remaining_steps/read_only" in _PRIMARY_SYSTEM
    assert "Do not invent a saved plan" in _PRIMARY_SYSTEM
    assert "Host verifies snapshot existence again" in _PRIMARY_SYSTEM


def test_model_proposed_missing_plan_binds_but_cannot_dispatch():
    message = "请继续执行之前保存的计划剩余步骤。"
    provider = Provider(signal(
        "plan_missing", "继续执行之前保存的计划剩余步骤",
        execution_scope="none", effect_intent="none"))
    route = asyncio.run(propose_primary_route(
        provider, "model", message, saved_plan_present=False))
    assert route.route_kind == "plan_missing"
    assert provider.calls == 1


def test_model_resume_absent_plan_remains_refused_no_reclassification():
    message = "请继续执行之前保存的计划剩余步骤。"
    provider = Provider(signal(
        "plan_resume", "继续执行之前保存的计划剩余步骤",
        execution_scope="remaining_steps", effect_intent="read_only"))
    with pytest.raises(PrimaryRouteRejected,
                       match="primary_route_missing_saved_plan"):
        asyncio.run(propose_primary_route(
            provider, "model", message, saved_plan_present=False))
    assert provider.calls == 1



def test_revision_generation_contract_requires_bare_old_and_new_expressions():
    from agent.primary_route_v1 import _PRIMARY_SYSTEM
    assert "revision_old_argument='5*6'" in _PRIMARY_SYSTEM
    assert "revision_new_argument='7*8'" in _PRIMARY_SYSTEM
    assert "NEVER output '计算 5*6'" in _PRIMARY_SYSTEM
    assert "stored expected_arguments.expression" in _PRIMARY_SYSTEM
    assert "clarify route" in _PRIMARY_SYSTEM



def test_primary_includes_bounded_trusted_plan_steps_for_revision():
    message = "将第二步由计算 5*6 改为计算 7*8，第一步不变"
    class CaptureProvider:
        def __init__(self):
            self.payload = None
            self.calls = 0
        async def complete(self, *, model, messages, response_schema):
            self.calls += 1
            self.payload = json.loads(messages[-1]["content"])
            return json.dumps(signal(
                "plan_revise", "第二步由计算 5*6 改为计算 7*8",
                effect_intent="mutation_requested", step_number=2,
                revision_old_argument="5*6", revision_new_argument="7*8"))
    provider = CaptureProvider()
    context = [
        {"step_number": 1, "expected_tool": "calculate",
         "expected_arguments": {"expression": "3*4"}, "status": "pending"},
        {"step_number": 2, "expected_tool": "calculate",
         "expected_arguments": {"expression": "5*6"}, "status": "pending"},
    ]
    result = asyncio.run(propose_primary_route(
        provider, "test", message, saved_plan_present=True,
        saved_step_count=2, saved_plan_steps=context))
    assert result.route_kind == "plan_revise"
    assert provider.calls == 1
    assert provider.payload["saved_plan_steps"] == context
    assert provider.payload["saved_plan_exists"] is True
    absent = CaptureProvider()
    # The provider's proposal will be rejected because there is no checkpoint,
    # but the absent plan must never carry stale step data to the model.
    with pytest.raises(PrimaryRouteRejected, match="primary_route_missing_saved_plan"):
        asyncio.run(propose_primary_route(
            absent, "test", message, saved_plan_present=False,
            saved_plan_steps=context))
    assert absent.calls == 1
    assert absent.payload["saved_plan_steps"] == []



def test_saved_plan_first_prompt_does_not_echo_snapshot_into_output_steps():
    from agent.primary_route_v1 import _PRIMARY_SYSTEM
    assert "OUTPUT-vs-CONTEXT BOUNDARY" in _PRIMARY_SYSTEM
    assert "NEVER copy saved_plan_steps" in _PRIMARY_SYSTEM
    marker = "Existing-plan FIRST OUTPUT FORMAT EXAMPLE ONLY: "
    example = _PRIMARY_SYSTEM.split(marker, 1)[1].split(
        " If current_request asks", 1)[0]
    signal = RouteSignal.model_validate_json(example)
    assert signal.route_kind == "plan_execute_first"
    assert signal.execution_scope == "first_step"
    assert signal.effect_intent == "read_only"
    assert signal.steps == []
    assert "plan_resume/remaining_steps/read_only" in _PRIMARY_SYSTEM


def test_primary_first_snapshot_is_readonly_input_and_output_steps_must_be_empty():
    message = "现在只执行已保存计划的第一步，第二步先不要执行。"

    class SnapshotProvider:
        def __init__(self, echo_steps):
            self.echo_steps = echo_steps
            self.calls = 0
            self.payload = None

        async def complete(self, *, model, messages, response_schema):
            self.calls += 1
            self.payload = json.loads(messages[-1]["content"])
            return json.dumps(signal(
                "plan_execute_first", "只执行已保存计划的第一步",
                execution_scope="first_step", effect_intent="read_only",
                steps=self.payload["saved_plan_steps"] if self.echo_steps else []),
                ensure_ascii=False)

    saved = [
        {"step_number": 1, "expected_tool": "calculate",
         "expected_arguments": {"expression": "3*4"}, "status": "pending"},
        {"step_number": 2, "expected_tool": "calculate",
         "expected_arguments": {"expression": "5*6"}, "status": "pending"},
    ]
    valid = SnapshotProvider(False)
    result = asyncio.run(propose_primary_route(
        valid, "test", message, saved_plan_present=True,
        saved_step_count=2, saved_plan_steps=saved))
    assert result.route_kind == "plan_execute_first"
    assert result.steps == []
    assert valid.payload["saved_plan_steps"] == saved
    assert valid.calls == 1

    invalid = SnapshotProvider(True)
    with pytest.raises(PrimaryRouteRejected) as caught:
        asyncio.run(propose_primary_route(
            invalid, "test", message, saved_plan_present=True,
            saved_step_count=2, saved_plan_steps=saved))
    assert {"field": "root", "category": "primary_route_steps_mismatch"} in (
        caught.value.validation_issues
    ) or any(issue["field"].startswith("steps") for issue in caught.value.validation_issues)
    assert invalid.calls == 1



def test_verified_pending_reply_context_and_output_contract_are_distinct():
    from agent.primary_route_v1 import (
        _PRIMARY_SYSTEM, _PENDING_CURRENT_TURN_SYSTEM,
    )
    # The old full clarify_response JSON exemplar was a possible recency
    # bias; the schema remains unchanged, while a separate current-turn
    # semantic instruction gives the model a fresh-vs-continuation contrast.
    assert "Verified Pending reply OUTPUT FORMAT EXAMPLE ONLY: " not in _PRIMARY_SYSTEM
    assert "pending_origin_verified=true" in _PRIMARY_SYSTEM
    assert "pending_question" in _PRIMARY_SYSTEM
    assert "clarify_response" in _PENDING_CURRENT_TURN_SYSTEM
    assert "respond/none/none" in _PENDING_CURRENT_TURN_SYSTEM
    assert "ordinary_tool/none/read_only" in _PENDING_CURRENT_TURN_SYSTEM


@pytest.mark.parametrize("action", ["create_task", "mcp_reference_lookup"])
def test_primary_passes_only_host_verified_pending_question(action):
    reply = "整理模拟记录"
    class Capture:
        def __init__(self):
            self.payload = None
            self.calls = 0
        async def complete(self, *, model, messages, response_schema):
            self.calls += 1
            self.payload = json.loads(messages[-1]["content"])
            return json.dumps(signal("clarify_response", reply),
                              ensure_ascii=False)

    yes = Capture()
    proposal = asyncio.run(propose_primary_route(
        yes, "test", reply, saved_plan_present=False,
        pending_action=action, pending_origin_verified=True,
        pending_question="请提供任务标题或查询词条"))
    assert proposal.route_kind == "clarify_response"
    assert yes.calls == 1
    assert yes.payload["pending_action"] == action
    assert yes.payload["pending_origin_verified"] is True
    assert yes.payload["pending_question"] == "请提供任务标题或查询词条"

    no = Capture()
    with pytest.raises(PrimaryRouteRejected, match="primary_route_untrusted_pending_origin"):
        asyncio.run(propose_primary_route(
            no, "test", reply, saved_plan_present=False,
            pending_action=action, pending_origin_verified=False,
            pending_question="未经验证的旧问题"))
    assert no.calls == 1
    assert no.payload["pending_action"] is None
    assert no.payload["pending_origin_verified"] is False
    assert no.payload["pending_question"] is None


def test_pending_mcp_validator_error_is_reported_as_safe_category():
    from agent.primary_route_v1 import safe_route_validation_issues
    invalid = signal(
        "clarify_response", "RAG",
        pending_read_tool_name="mcp_reference_lookup")
    with pytest.raises(ValidationError) as caught:
        RouteSignal.model_validate(invalid)
    issues = safe_route_validation_issues(caught.value)
    assert {"field": "root", "category": "primary_route_pending_read_invalid"} in issues
    assert "RAG" not in str(issues)



def test_context_schema_binds_root_quote_without_rewriting_proposal():
    from agent.primary_route_v1 import primary_route_generation_schema
    request = "替我建立一个待办事项，不过我还没有决定标题，请先问我名称。"
    schema = primary_route_generation_schema(request)
    assert schema["properties"]["quote"]["const"] == request
    assert schema["properties"]["route_kind"] == RouteSignal.model_json_schema()["properties"]["route_kind"]
    assert schema["properties"]["pending_read_tool_name"] == (
        RouteSignal.model_json_schema()["properties"]["pending_read_tool_name"])
    assert "const" not in primary_route_generation_schema("a" * 401)["properties"]["quote"]
    # Host still rejects a fabricated or mismatched quote.
    with pytest.raises(PrimaryRouteRejected, match="primary_route_quote_not_found"):
        bind_route_signal(RouteSignal.model_validate(
            signal("clarify", "虚构短句", clarification_question="标题是什么？",
                   pending_write_tool_name="create_task")),
            request, saved_plan_present=False, pending_action=None)


@pytest.mark.parametrize("action", ["create_task", "mcp_reference_lookup"])
def test_verified_pending_schema_forbids_copying_origin_intent(action):
    from agent.primary_route_v1 import primary_route_generation_schema
    schema = primary_route_generation_schema(
        "RAG", pending_origin_verified=True)
    assert schema["properties"]["quote"]["const"] == "RAG"
    assert schema["properties"]["pending_read_tool_name"]["type"] == "null"
    assert schema["properties"]["pending_write_tool_name"]["type"] == "null"
    assert schema["properties"]["route_kind"] == RouteSignal.model_json_schema()["properties"]["route_kind"]
    # The original runtime model remains fail closed if any provider ignores it.
    bad = signal("clarify_response", "RAG",
                 **({"pending_read_tool_name": action} if action == "mcp_reference_lookup"
                    else {"pending_write_tool_name": action}))
    with pytest.raises(ValidationError):
        RouteSignal.model_validate(bad)


def test_single_model_receives_host_bounded_schema_for_real_pending_reply():
    from agent.primary_route_v1 import primary_route_generation_schema
    class ObserveSchema:
        def __init__(self):
            self.calls = 0
            self.seen = None
        async def complete(self, *, model, messages, response_schema):
            self.calls += 1
            self.seen = response_schema
            return json.dumps(signal("clarify_response", "RAG"))

    provider = ObserveSchema()
    route = asyncio.run(propose_primary_route(
        provider, "test", "RAG", saved_plan_present=False,
        pending_action="mcp_reference_lookup",
        pending_origin_verified=True,
        pending_question="查哪个术语？"))
    assert route.route_kind == "clarify_response"
    assert provider.calls == 1
    assert provider.seen["properties"]["quote"]["const"] == "RAG"
    assert provider.seen["properties"]["pending_read_tool_name"]["type"] == "null"
    assert provider.seen["properties"]["pending_write_tool_name"]["type"] == "null"


def test_verified_pending_task_generation_disallows_fresh_write_contract():
    from agent.primary_route_v1 import primary_route_generation_schema
    message = "整理虚构实验设备清单"
    schema = primary_route_generation_schema(message, pending_origin_verified=True)
    assert schema["properties"]["quote"]["const"] == message
    for field in ("pending_write_tool_name", "pending_read_tool_name",
                  "write_tool_name", "write_arguments"):
        assert schema["properties"][field]["type"] == "null"
    assert schema["properties"]["route_kind"] == RouteSignal.model_json_schema()["properties"]["route_kind"]
    # No permissions are added to the normal path, where a separately
    # Host-admitted ordinary task may still carry explicit tool arguments.
    non_pending = primary_route_generation_schema(message)
    for field in ("write_tool_name", "write_arguments"):
        assert non_pending["properties"][field] == (
            RouteSignal.model_json_schema()["properties"][field])


def test_pending_task_source_bound_reply_only_one_model_call_and_no_host_repair():
    class Observe:
        def __init__(self, bad):
            self.calls = 0
            self.schema = None
            self.bad = bad

        async def complete(self, *, model, messages, response_schema):
            self.calls += 1
            self.schema = response_schema
            value = signal("clarify_response", "整理虚构实验设备清单")
            if self.bad:
                value["write_tool_name"] = "create_task"
                value["write_arguments"] = {"title": "整理虚构实验设备清单"}
            return json.dumps(value, ensure_ascii=False)

    message = "整理虚构实验设备清单"
    good = Observe(False)
    route = asyncio.run(propose_primary_route(
        good, "test", message, saved_plan_present=False,
        pending_action="create_task", pending_origin_verified=True,
        pending_question="请提供待办事项名称。"))
    assert route.route_kind == "clarify_response"
    assert good.calls == 1
    assert good.schema["properties"]["write_tool_name"]["type"] == "null"
    assert good.schema["properties"]["write_arguments"]["type"] == "null"

    bad = Observe(True)
    with pytest.raises(PrimaryRouteRejected, match="primary_route_invalid_signal") as ex:
        asyncio.run(propose_primary_route(
            bad, "test", message, saved_plan_present=False,
            pending_action="create_task", pending_origin_verified=True,
            pending_question="请提供待办事项名称。"))
    assert bad.calls == 1
    assert ex.value.validation_issues == [{
        "field": "root", "category": "primary_route_unexpected_write_contract"}]


def test_primary_pending_withdrawal_prompt_does_not_grant_write():
    from agent.primary_route_v1 import _PRIMARY_SYSTEM
    assert "Pending write cancellation is a DIFFERENT intent" in _PRIMARY_SYSTEM
    assert "choose respond/none/none" in _PRIMARY_SYSTEM
    assert "not the missing title" in _PRIMARY_SYSTEM
