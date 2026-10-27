"""M2 follow-up held cohort; freeze BEFORE modifying semantic prompts.
Author-curated, NOT independent third-party blind. Never adapt after live runs.
"""
import hashlib
import json
from dataclasses import dataclass
from agent.route_shadow_host import ShadowHostFacts

@dataclass(frozen=True)
class Case:
    case_id: str
    prompt: str
    expected_object: str
    expected_operation: str
    facts: ShadowHostFacts
    critical: bool = False

_DATA = [["u01","仅为活动主持人写一段开场白，留在聊天窗口即可。","none","answer",false,false],["u02","Draft a short acknowledgment for the report without persisting anything.","none","answer",false,false],["u03","我没说要动哪个项目，先问清楚是哪一个。","none","clarify",false,true],["u04","把周末检查消防箱记为一条以后可以查询的待办。","task","create",false,true],["u05","Persist one reminder to renew the parking permit, not a sequence.","task","create",false,true],["u06","存一份两环节作业安排：先核对温湿度记录，接着归档读数；暂不启动。","plan","create",false,true],["u07","Save an ordered procedure: verify shipment labels, then file the inspection log. Do not run.","plan","create",false,true],["u08","更新已有巡查方案第三步的描述，不要运行步骤。","plan_step","revise",true,true],["u09","Change the content of saved step 2 to use the revised checklist without running it.","plan_step","revise",true,true],["u10","已归档的值班方案现在从起始环节执行，其余环节保持待命。","plan","execute_first",true,true],["u11","Begin execution at the initial stage of the saved audit workflow; keep everything following untouched.","plan","execute_first",true,true],["u12","只开始已经存档计划的首项操作，不启动余下环节。","plan","execute_first",true,true],["u13","首项已结束，接着运行保留计划后续未完成的部分。","plan","resume",true,true],["u14","Resume unfinished downstream stages of the stored maintenance sequence.","plan","resume",true,true],["u15","继续完成先前登记的巡检作业余下步骤。","plan","resume",false,true],["u16","给我使用计算工具求 26 加 57，不要修改已有事项。","tool","invoke",false,true],["u17","Invoke the arithmetic utility to multiply 14 by 9, independently of any saved workflow.","tool","invoke",true,true],["u18","等待中的检索先不回答，转而使用计算工具算 72 除以 8。","tool","invoke",false,true,"pending"],["u19","先不要补刚才缺的地址，另外调用时钟工具读当前 UTC 时间。","tool","invoke",false,true,"pending"],["u20","缺的百科检索主题是量子点。","pending_slot","invoke",false,true,"pending"],["u21","补充那个待检索词：水循环。","pending_slot","invoke",false,true,"pending"],["u22","暂停先前查询，给我一句简短的自我介绍即可。","none","answer",false,true,"pending"],["u23","没有说明需要改动存档计划中的哪一步，请先向我询问步骤编号。","none","clarify",true,true],["u24","使用知识库检索核聚变的定义，只进行查询。","tool","invoke",false,true]]
def _facts(has_plan, pending=False):
    return ShadowHostFacts(has_plan=has_plan,
        plan_states=("pending","pending","pending") if has_plan else (),
        revision_valid=has_plan,
        pending_action="mcp_reference_lookup" if pending else None,
        pending_field="term" if pending else None,
        pending_original_request_bound=pending)

CASES = tuple(Case(cid,p,o,a,_facts(h,mark=="pending"),critical)
    for cid,p,o,a,h,critical,*optional in _DATA
    for mark in [optional[0] if optional else ""])
MIN_PASS_RATE = 0.95
REQUIRED_REPETITIONS = 2

def manifest_payload():
    return [{"id":c.case_id,"prompt":c.prompt,"object":c.expected_object,
             "operation":c.expected_operation,"critical":c.critical,
             "has_plan":c.facts.has_plan,
             "pending_action":c.facts.pending_action} for c in CASES]

def manifest_sha256():
    blob=json.dumps(manifest_payload(),ensure_ascii=False,sort_keys=True,
                    separators=(",",":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()

# SEALED_MANIFEST_SHA256 is populated in the PRE-IMPLEMENTATION freeze commit.
SEALED_MANIFEST_SHA256 = "PENDING_FREEZE"
def assert_frozen():
    if len(CASES)!=24 or len({c.case_id for c in CASES})!=24:
        raise RuntimeError("invalid_case_count")
    if manifest_sha256()!=SEALED_MANIFEST_SHA256:
        raise RuntimeError("manifest_changed")
