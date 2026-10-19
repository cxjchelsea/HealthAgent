"""Explicit opt-in live native tool calling test. Does not run in default CI."""
import asyncio
import os
import pytest
from agent.config import Settings
from agent.decision import NativeToolDecision
from agent.loop import AgentLoop

@pytest.mark.live
def test_native_tool_use_calculation():
    if os.getenv("RUN_S2A_LIVE") != "1":
        pytest.skip("S2-A live tool test opt-in required")
    settings = Settings()
    if settings.model_provider not in ("ollama_proxy", "openai_compatible"):
        pytest.skip("Set native tool calling provider")
    result = asyncio.run(AgentLoop(NativeToolDecision(settings)).run(
        "请务必使用calculate工具计算 (19+23)*4，然后给出最终结果。"))
    assert result["status"] == "completed"
    assert "168" in result["answer"]
    assert any(x.get("name") == "calculate" and x.get("ok") for x in result["trace"])
