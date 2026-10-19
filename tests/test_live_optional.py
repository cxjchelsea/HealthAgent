"""Opt-in 5-turn real-model verification; no prompts or credentials in evidence."""
import json
import os
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.config import Settings
from apps.api.main import create_app

@pytest.mark.live
def test_five_turn_real_provider(tmp_path):
    if os.getenv("RUN_LIVE_MODEL_TEST") != "1":
        pytest.skip("Real model test opt-in required")
    settings = Settings(database_url=f"sqlite:///{tmp_path/'live.db'}")
    if settings.model_provider not in ("openai_compatible", "ollama_proxy"):
        pytest.skip("Configure a real provider")
    if settings.model_provider == "openai_compatible" and not settings.model_api_key:
        pytest.skip("Requires MODEL_API_KEY")

    questions = [
        "你好。请简单用中文回复。",
        "请记住我给你的代号是：木星。",
        "我刚刚告诉你的代号是什么？只回答代号。",
        "请简短介绍你能完成什么任务。",
        "我们之前约定的代号是什么？只回答代号。",
    ]
    records = []
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        for idx, question in enumerate(questions):
            start = time.monotonic()
            response = client.post(
                f"/conversations/{cid}/messages",
                json={"content": question, "client_request_id": str(uuid.uuid4())},
            )
            assert response.status_code == 200
            assert "event: message_done" in response.text
            assert '"status": "completed"' in response.text
            answer = client.get(f"/conversations/{cid}").json()["messages"][-1]
            assert answer["status"] == "completed"
            assert answer["content"].strip()
            recall = idx in (2, 4)
            if recall:
                assert "木星" in answer["content"], "Model failed to recall the prior turn"
            records.append({
                "turn": idx + 1,
                "completed": True,
                "recall_check": recall,
                "latency_ms": int((time.monotonic() - start) * 1000),
                "token_usage_available": False,
            })

    if os.getenv("WRITE_LIVE_EVIDENCE") == "1":
        target = Path("evals/results/live")
        target.mkdir(parents=True, exist_ok=True)
        (target / "s1-evidence.json").write_text(
            json.dumps({
                "provider": settings.model_provider,
                "model": settings.model_name,
                "turns": records,
                "source": "user_initiated_live_pytest",
                "note": "No official token accounting; no message contents stored.",
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
