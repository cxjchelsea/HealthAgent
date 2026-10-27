"""Read-only exact-quote probe and fail-closed boundaries."""
import asyncio
from types import SimpleNamespace
from agent.route_shadow import ShadowRoute, QuoteEvidence
from evals.s25_02_m2_semantic_evidence_live import probe_missing_quotes


class Provider:
    def __init__(self, response):
        self.response = response
        self.calls = []
    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def sample():
    return SimpleNamespace(prompt="请先执行第一步")


def proposal(**kwargs):
    return ShadowRoute(schema_version="s25-route-v2",
        primary_route="plan_execute_first", route_target="saved_plan",
        execution_scope="first_step", effect_intent="read_only",
        ambiguity="clear", **kwargs)


def test_exact_unique_quote_probe_is_only_evidence_not_authority():
    p = Provider('{"current_turn_quote":"执行第一步","revision_step_quote":null}')
    route, state = asyncio.run(probe_missing_quotes(p, "qwen3:14b", sample(), proposal(), 5))
    assert state == "quoted_exactly"
    assert route.source_quote_evidence[0].exact_text == "执行第一步"
    assert route.primary_route == "plan_execute_first"
    assert len(p.calls) == 1


def test_ambiguous_or_absent_quote_is_not_inserted():
    for message, quote in (
        ("第一步第一步", "第一步"), ("请先执行第一步", "不存在")):
        p = Provider('{"current_turn_quote":"' + quote + '","revision_step_quote":null}')
        result, state = asyncio.run(probe_missing_quotes(
            p, "qwen3:14b", SimpleNamespace(prompt=message), proposal(), 5))
        assert state == "unavailable"
        assert not result.source_quote_evidence


def test_existing_quotes_not_overwritten():
    old = proposal(source_quote_evidence=[QuoteEvidence(exact_text="既有")])
    p = Provider("{}")
    result, state = asyncio.run(probe_missing_quotes(p, "qwen3:14b", sample(), old, 5))
    assert state == "not_needed"
    assert result.source_quote_evidence == old.source_quote_evidence
    assert not p.calls
