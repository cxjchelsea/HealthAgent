"""Explicit real model dynamic dependency: result unavailable in original goal."""
import asyncio
import os
import re
from datetime import datetime, timezone

import pytest

from agent.config import Settings
from agent.decision import Decision, NativeToolDecision
from agent.loop import AgentLoop


GOAL = (
    "先使用 utc_time 获取真正的当前 UTC 时间。读取工具结果中的小时和分钟，"
    "然后调用 create_task，标题必须为“记录 UTC HH:MM”，其中 HH:MM "
    "必须替换为刚才 utc_time 返回的真实小时和分钟。不要猜测时间。"
)


def verify_dependency(result):
    assert result["status"] == "completed"
    audit = result["audit"]
    assert [entry["name"] for entry in audit] == ["utc_time", "create_task"]
    first, second = audit
    assert first["observation"]["ok"] is True
    assert second["observation"]["ok"] is True
    assert second["step"] > first["step"], "create_task must follow observing UTC result"
    utc = datetime.fromisoformat(first["observation"]["utc"].replace("Z", "+00:00"))
    hhmm = utc.astimezone(timezone.utc).strftime("%H:%M")
    title = second["arguments"]["title"]
    assert isinstance(title, str) and re.fullmatch(
        r"记录 UTC \d{2}:\d{2}", title
    ), f"unexpected task title shape: {title!r}"
    assert title == f"记录 UTC {hhmm}", "task title did not use observed UTC time"
    assert second["observation"]["title"] == title
    assert result["tasks"] == [title]


class ObservableModel:
    async def decide(self, messages, tools):
        observations = [m for m in messages if m["role"] == "tool"]
        if not observations:
            return Decision("", [{"id": "time", "name": "utc_time", "arguments": {}}])
        if len(observations) == 1:
            import json
            value = json.loads(observations[0]["content"])["utc"]
            hour = datetime.fromisoformat(value).strftime("%H:%M")
            return Decision("", [{"id": "create", "name": "create_task",
                                  "arguments": {"title": f"记录 UTC {hour}"}}])
        return Decision("已创建记录时间的任务", [])


def test_dynamic_dependency_deterministic():
    fixed = datetime(2026, 10, 19, 9, 42, tzinfo=timezone.utc)
    result = asyncio.run(AgentLoop(ObservableModel(), clock=lambda: fixed).run(
        GOAL, include_evidence=True))
    verify_dependency(result)


@pytest.mark.live
def test_dynamic_dependency_real_qwen():
    if os.getenv("RUN_S2A_LIVE") != "1":
        pytest.skip("opt-in live provider test")
    settings = Settings()
    if settings.model_provider not in ("ollama_proxy", "openai_compatible"):
        pytest.skip("real model provider required")
    result = asyncio.run(AgentLoop(NativeToolDecision(settings)).run(
        GOAL, include_evidence=True))
    verify_dependency(result)
