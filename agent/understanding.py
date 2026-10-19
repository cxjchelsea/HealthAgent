"""Conservative S1 goal intake; NOT LLM reasoning or full intent classification.

Unknown requests remain unknown. Questions with missing essential references
get a clarification flag; the model may answer conversationally as before.
"""
from pydantic import BaseModel, Field

class GoalIntake(BaseModel):
    text: str = Field(min_length=1)
    goal_kind: str
    needs_clarification: bool
    clarification_reason: str | None = None
    source: str = "rule_based_s1"
    confidence: str = "limited"

def understand(text: str) -> GoalIntake:
    normalized = text.strip()
    if not normalized:
        raise ValueError("Empty goal")
    ambiguous = normalized in {"帮我处理一下", "帮我看看", "这个怎么办", "那个呢", "继续做", "弄一下"}
    if ambiguous:
        return GoalIntake(text=normalized, goal_kind="unknown",
                          needs_clarification=True,
                          clarification_reason="missing_target_or_referent")
    if any(w in normalized for w in ("介绍", "解释", "什么是", "为什么", "如何")):
        kind = "information_request"
    else:
        kind = "general_conversation"
    return GoalIntake(text=normalized, goal_kind=kind, needs_clarification=False)
