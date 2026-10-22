"""Narrow model-driven classification of replies to an existing tool clarification.

No keyword classifier or write authorization: original pending action must come
from a completed owner-scoped Turn; only the current user text can fill a value.
"""
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict
from agent.understanding import HTTPUnderstandingModel, UnderstandingError


class PendingReplyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["answer", "new_goal", "cancel", "uncertain"]


async def classify_pending_reply(provider: HTTPUnderstandingModel, model: str, *,
                                 question: str, reply: str, tool: str) -> str:
    prompt = ("Classify the current USER message relative to an outstanding "
              "tool-argument clarification. Output only JSON {\"kind\": "
              "\"answer\"|\"new_goal\"|\"cancel\"|\"uncertain\"}. "
              "answer means the user supplies the previously requested concrete "
              "value, including a short task title; do NOT require details about "
              "how to perform the named task. new_goal means an explicit unrelated "
              "request, cancel means explicit abandonment, uncertain means the "
              "reply does not sufficiently identify the requested value. "
              "Never fabricate the missing value or infer a new authorization.")
    try:
        raw = await provider.complete(messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({
                "pending_tool": tool, "question": question,
                "current_user_reply": reply}, ensure_ascii=False)}],
            model=model)
        return PendingReplyDecision.model_validate_json(raw).kind
    except (UnderstandingError, ValueError):
        return "uncertain"
