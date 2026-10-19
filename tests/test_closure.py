"""S1 closure regression checks without a paid/remote provider."""
import asyncio
import json

from fastapi.testclient import TestClient
from agent.config import Settings
from apps.api.main import create_app
from services.db import Turn, database
from sqlalchemy import select


class PartialThenTimeout:
    async def stream(self, messages):
        yield "partial "
        raise asyncio.TimeoutError("private upstream endpoint")


class SeveralDeltas:
    async def stream(self, messages):
        for text in ("one ", "two ", "three"):
            yield text


def _run(tmp_path, provider):
    url = f"sqlite:///{tmp_path/'close.db'}"
    app = create_app(Settings(database_url=url, model_provider="fake"), provider=provider)
    with TestClient(app) as client:
        cid = client.post('/conversations').json()['id']
        result = client.post(f'/conversations/{cid}/messages', json={
            'content': 'test', 'client_request_id': 'closure-req-1234'})
        history = client.get(f'/conversations/{cid}').json()['messages']
    engine, Session = database(url)
    with Session() as db:
        turn = db.scalar(select(Turn).where(Turn.conversation_id == cid))
        assert turn is not None
        saved = (turn.state, turn.output_chars, turn.duration_ms)
    engine.dispose()
    return result.text, history[-1], saved


def test_timeout_after_partial_output_is_not_completed(tmp_path):
    text, assistant, saved = _run(tmp_path, PartialThenTimeout())
    assert 'event: message_delta' in text
    assert 'event: error' in text
    assert 'event: message_done' not in text
    assert assistant['status'] == 'provider_error'
    assert assistant['content'] == 'partial '
    assert saved[0] == 'provider_error'
    assert saved[1] == len('partial ')
    assert 'private upstream endpoint' not in text


def test_metrics_and_complete_stream(tmp_path):
    text, assistant, saved = _run(tmp_path, SeveralDeltas())
    assert text.count('event: message_delta') == 3
    assert 'event: message_done' in text
    assert '"estimated": true' in text
    assert '"token_usage_available": false' in text
    assert assistant['status'] == 'completed'
    assert assistant['content'] == 'one two three'
    assert saved[0] == 'completed'
    assert saved[1] == len('one two three')
    assert saved[2] >= 0
