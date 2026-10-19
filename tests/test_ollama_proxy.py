import json
import httpx
import pytest
from agent.providers import OllamaProxyProvider, ModelUnavailable, make_provider
from agent.config import Settings

def test_proxy_stream(monkeypatch):
    seen = {}
    def handler(req):
        seen['path'] = req.url.path
        seen['body'] = json.loads(req.content)
        lines = [
            {'message': {'content': '<thi'}, 'done': False},
            {'message': {'content': 'nk>secret</think>你好'}, 'done': False},
            {'message': {'content': '，世界'}, 'done': True},
        ]
        return httpx.Response(200, text='\n'.join(json.dumps(x, ensure_ascii=False) for x in lines)+'\n')
    native_client = httpx.AsyncClient
    def client_factory(*args, **kwargs):
        return native_client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr('agent.providers.httpx.AsyncClient', client_factory)
    p = OllamaProxyProvider('http://example.test/ollama/biz', 'qwen3:14b')
    import asyncio
    async def run():
        return [x async for x in p.stream([{'role':'user','content':'hi'}])]
    chunks = asyncio.run(run())
    assert ''.join(chunks) == '你好，世界'
    assert seen['path'] == '/ollama/biz/chat'
    assert seen['body']['stream'] is True
    assert seen['body']['model'] == 'qwen3:14b'

def test_proxy_error_not_leaked(monkeypatch):
    native_client = httpx.AsyncClient
    def factory(*args, **kwargs):
        return native_client(transport=httpx.MockTransport(lambda req: httpx.Response(404,text='private token')), **kwargs)
    monkeypatch.setattr('agent.providers.httpx.AsyncClient', factory)
    p=OllamaProxyProvider('http://example.test/ollama/biz','qwen3:14b')
    import asyncio
    async def run():
        return [x async for x in p.stream([{'role':'user','content':'hi'}])]
    with pytest.raises(ModelUnavailable,match='HTTP 404'):
        asyncio.run(run())


def test_factory_supports_proxy_without_api_key():
    settings=Settings(model_provider='ollama_proxy', model_base_url='http://example.test',model_name='qwen3:14b',model_api_key='')
    assert settings.provider_ready
    assert isinstance(make_provider(settings),OllamaProxyProvider)


def test_output_budget_and_limit_reason(monkeypatch):
    seen={}
    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, text=json.dumps({"message":{"content":"部分回答"},"done":True,"done_reason":"length"},ensure_ascii=False)+"\n")
    native=httpx.AsyncClient
    monkeypatch.setattr('agent.providers.httpx.AsyncClient',lambda *a,**kw:native(transport=httpx.MockTransport(handler),**kw))
    import asyncio
    async def run():return [part async for part in OllamaProxyProvider('http://example.test','qwen3:14b',max_output_tokens=4096).stream([{'role':'user','content':'hi'}])]
    with pytest.raises(ModelUnavailable,match='output limit'):
        asyncio.run(run())
    assert seen['options']['num_predict']==4096
