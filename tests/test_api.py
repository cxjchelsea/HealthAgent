import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from agent.config import Settings
from apps.api.main import create_app
from agent.providers import ModelUnavailable

@pytest.fixture
def client(tmp_path):
    settings=Settings(database_url=f"sqlite:///{tmp_path/'test.db'}",model_provider="fake",user_tokens="alpha:user-a,beta:user-b",allow_demo_identity=False)
    with TestClient(create_app(settings)) as c:
        yield c

def headers(token="alpha"):return {"Authorization":f"Bearer {token}"}

def make(client, token="alpha"):
    response=client.post('/conversations',headers=headers(token))
    assert response.status_code==201
    return response.json()['id']

def test_health_ready(client):
    assert client.get('/healthz').json()['status']=='ok'
    assert client.get('/readyz').json()['status']=='ready'

def test_auth_isolation(client):
    a=make(client)
    assert client.get('/conversations/'+a,headers=headers('beta')).status_code==404
    assert client.get('/conversations/'+a).status_code==401

def test_sse_and_persistence(client):
    cid=make(client)
    with client.stream('POST',f'/conversations/{cid}/messages',headers=headers(),json={'content':'你好','client_request_id':'req-12345678'}) as response:
        assert response.status_code==200
        text=''.join(response.iter_text())
        assert 'event: message_delta' in text
        assert 'event: message_done' in text
    history=client.get('/conversations/'+cid,headers=headers()).json()['messages']
    assert [m['role'] for m in history]==['user','assistant']
    assert history[-1]['status']=='completed'
    assert 'FAKE MODEL' in history[-1]['content']

def test_duplicate_and_invalid(client):
    cid=make(client)
    body={'content':'hello','client_request_id':'req-abcdef1234'}
    assert client.post(f'/conversations/{cid}/messages',headers=headers(),json=body).status_code==200
    assert client.post(f'/conversations/{cid}/messages',headers=headers(),json=body).status_code==409
    assert client.post(f'/conversations/{cid}/messages',headers=headers(),json={'content':'','client_request_id':'anything123'}).status_code==422

class ExplodingModel:
    async def stream(self, messages):
        raise ModelUnavailable('secret-key-must-not-leak')
        yield ''

def test_provider_failure_not_committed(tmp_path):
    s=Settings(database_url=f"sqlite:///{tmp_path/'err.db'}",model_provider='fake')
    with TestClient(create_app(s,provider=ExplodingModel())) as c:
        cid=c.post('/conversations').json()['id']
        result=c.post(f'/conversations/{cid}/messages',json={'content':'hello','client_request_id':'123456789'})
        assert 'event: error' in result.text
        assert 'secret-key' not in result.text
        hist=c.get('/conversations/'+cid).json()['messages']
        assert hist[-1]['status']=='provider_error'

class CaptureModel:
    def __init__(self):
        self.calls = []

    async def stream(self, messages):
        self.calls.append(list(messages))
        yield f"turn {len(self.calls)}"


def test_two_turn_context_is_passed_to_model(tmp_path):
    model = CaptureModel()
    settings = Settings(database_url=f"sqlite:///{tmp_path/'multi.db'}", model_provider="fake")
    with TestClient(create_app(settings, provider=model)) as c:
        cid = c.post('/conversations').json()['id']
        for index in range(2):
            r = c.post(f'/conversations/{cid}/messages', json={
                'content': f'question {index}', 'client_request_id': f'request-{index}-abc1234'})
            assert 'event: message_done' in r.text
        assert len(model.calls) == 2
        assert [item['role'] for item in model.calls[-1]] == ['user', 'assistant', 'user']
        assert model.calls[-1][-1]['content'] == 'question 1'

def test_web_page_served(client):
    response=client.get('/')
    assert response.status_code == 200
    assert 'HealthAgent' in response.text


def test_web_contains_restoration(client):
    html=client.get('/').text
    assert 'localStorage.setItem(KEY,conversation)' in html
    assert "encodeURIComponent(saved)" in html
