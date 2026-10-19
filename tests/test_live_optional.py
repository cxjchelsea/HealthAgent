"""Run ONLY with RUN_LIVE_MODEL_TEST=1 and a real configured provider.

The test sends real data to the configured provider and can incur costs.
"""
import os
import uuid
import pytest
from fastapi.testclient import TestClient
from agent.config import Settings
from apps.api.main import create_app

@pytest.mark.live
def test_five_turn_real_provider(tmp_path):
    if os.getenv('RUN_LIVE_MODEL_TEST') != '1':
        pytest.skip('Real model test disabled: opt in with RUN_LIVE_MODEL_TEST=1')
    settings=Settings(database_url=f"sqlite:///{tmp_path/'live.db'}")
    if settings.model_provider not in ('openai_compatible', 'ollama_proxy'):
        pytest.skip('Requires real MODEL_PROVIDER')
    if settings.model_provider == 'openai_compatible' and not settings.model_api_key:
        pytest.skip('Requires MODEL_API_KEY')
    with TestClient(create_app(settings)) as client:
        cid = client.post('/conversations').json()['id']
        for question in ['你好', '请记住一个代号：木星', '我刚才的代号是什么？', '我现在问的是哪一轮？', '概括我之前给出的代号']:
            response = client.post(f'/conversations/{cid}/messages', json={
                'content': question, 'client_request_id': str(uuid.uuid4())})
            assert 'event: message_done' in response.text
            assert 'status": "completed"' in response.text
