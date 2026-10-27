"""Regression checks for exact ordered steps and held-cohort integrity."""
import asyncio
from agent.route_semantic_hierarchical import HierarchicalDiagnostic
from agent.route_shadow_host import ShadowHostFacts
from agent.route_semantic_evidence import verify_core_evidence
from evals.s25_02_m2_plan_step_quotes import collect_ordered_step_quotes
from evals.s25_02_m2_evidence_structured_live import proposal_from_core
from evals.s25_02_m2_followup_sealed_v2 import CASES, assert_frozen
from evals.s25_02_m2_predeclared_v1 import CASES as OLD

class Provider:
    def __init__(self, value):
        self.value=value
    async def complete(self, **kwargs):
        return self.value

def test_new_corpus_immutable_and_different():
    assert_frozen()
    assert len(CASES)==24
    assert len({c.case_id for c in CASES})==24
    assert not {c.prompt for c in CASES} & {c.prompt for c in OLD}

def test_plan_create_positive_is_only_preview():
    text="把流程保存起来：先整理凭证，然后核对清单。"
    p=Provider('{"first":"整理凭证","second":"核对清单"}')
    quotes=asyncio.run(collect_ordered_step_quotes(p,"qwen3:14b",text))
    core=HierarchicalDiagnostic("plan","create")
    proposal=proposal_from_core(core,text,text,None,plan_steps=quotes)
    facts=ShadowHostFacts(has_plan=False,current_write_authorized=True,
                          source_provenance_valid=True,plan_schema_valid=True)
    out=verify_core_evidence(core,proposal,text,facts)
    assert out.disposition=="potentially_admissible"
    assert out.host_authorization is False

def test_plan_create_does_not_accept_missing_or_forged_steps():
    msg="保存一个流程：核对清单，然后填写结果。"
    core=HierarchicalDiagnostic("plan","create")
    facts=ShadowHostFacts(has_plan=False,current_write_authorized=True,
                          source_provenance_valid=True,plan_schema_valid=True)
    for value in ('{"first":null,"second":null}',
                  '{"first":"无中生有","second":"填写结果"}',
                  '{"first":"填写结果","second":"核对清单"}'):
        quotes=asyncio.run(collect_ordered_step_quotes(
            Provider(value),"qwen3:14b",msg))
        p=proposal_from_core(core,msg,msg,None,plan_steps=quotes)
        out=verify_core_evidence(core,p,msg,facts)
        assert out.disposition!="potentially_admissible"

def test_plan_create_lacking_write_authority_is_not_admitted():
    msg="请保存流程：先核对清单，再归档报告。"
    p=Provider('{"first":"核对清单","second":"归档报告"}')
    quotes=asyncio.run(collect_ordered_step_quotes(p,"qwen3:14b",msg))
    core=HierarchicalDiagnostic("plan","create")
    proposal=proposal_from_core(core,msg,msg,None,plan_steps=quotes)
    out=verify_core_evidence(core,proposal,msg,
                             ShadowHostFacts(has_plan=False,plan_schema_valid=True,
                                             source_provenance_valid=True))
    assert out.disposition=="needs_evidence"
