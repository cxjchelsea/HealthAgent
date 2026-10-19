"""Explicit opt-in 5-turn Qwen verification through the single Agent conversation."""
import os
import uuid
import pytest
from fastapi.testclient import TestClient
from agent.config import Settings
from apps.api.main import create_app
@pytest.mark.live
def test_five_turn_real_provider(tmp_path):
    if os.getenv("RUN_LIVE_MODEL_TEST") != "1":
        pytest.skip("Real model opt-in required")
    settings=Settings(database_url=f"sqlite:///{tmp_path/'live.db'}")
    if settings.model_provider not in ("openai_compatible","ollama_proxy"):
        pytest.skip("Configure real native-tool provider")
    questions=["你好，请简单用中文回复。","请记住代号：木星。",
               "刚才告诉你的代号是什么？只回答代号。",
               "请简短介绍你的工具能力。","我们约定的代号是什么？只回答代号。"]
    with TestClient(create_app(settings)) as c:
        cid=c.post("/conversations").json()["id"]
        for i,question in enumerate(questions):
            r=c.post(f"/conversations/{cid}/agent/messages",json={
                "content":question,"client_request_id":str(uuid.uuid4())})
            assert r.status_code==200,r.text
            assert r.json()["status"]=="completed"
            if i in (2, 4):
                assert "木星" in r.json()["answer"]
