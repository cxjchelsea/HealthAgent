import json
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Protocol
import httpx
from agent.http_policy import model_http_trust_env
from agent.understanding import model_call_counter

logger = logging.getLogger(__name__)

class ModelUnavailable(Exception):
    pass

class ModelProvider(Protocol):
    async def stream(self, messages: list[dict[str,str]]) -> AsyncIterator[str]: ...

class FakeProvider:
    async def stream(self, messages):
        last = messages[-1]["content"]
        # Deterministic smoke fixture; explicitly NOT an LLM or intent classifier.
        text = f"[FAKE MODEL] 收到：{last}。此响应仅用于测试流式接口。"
        for part in [text[i:i+12] for i in range(0,len(text),12)]:
            yield part

class OpenAICompatibleProvider:
    def __init__(self, base_url, api_key, model, timeout=45):
        self.base_url=base_url.rstrip("/")
        self.api_key=api_key
        self.model=model
        self.timeout=timeout

    async def stream(self, messages):
        if not self.api_key:
            raise ModelUnavailable("MODEL_API_KEY is not configured")
        url=f"{self.base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout,connect=10)) as client:
                counter = model_call_counter.get()
                if counter is not None:
                    counter["total"] += 1
                async with client.stream("POST", url,
                    headers={"Authorization":f"Bearer {self.api_key}"},
                    json={"model":self.model,"messages":messages,"stream":True}) as response:
                    if response.status_code >= 400:
                        raise ModelUnavailable(f"Provider HTTP {response.status_code}")
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data=line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            payload=json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if payload.get("error"):
                            raise ModelUnavailable("Provider returned an error")
                        for choice in payload.get("choices",[]):
                            delta=choice.get("delta") or {}
                            if isinstance(delta.get("content"),str):
                                yield delta["content"]
        except httpx.HTTPError as exc:
            raise ModelUnavailable("Provider network/timeout failure") from exc

class OllamaProxyProvider:
    """Ollama-style gateway using {base}/chat, with NDJSON streaming."""

    def __init__(self, base_url: str, model: str, timeout: float = 120, max_output_tokens: int = 2048):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        url = f"{self.base_url}/chat"
        # Optional thinking control; supported by native Ollama but may be ignored by a proxy.
        thinking = os.environ.get("MODEL_THINKING_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        request_body = {
            "think": thinking,
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": 0.25, "num_predict": self.max_output_tokens},
        }
        # Qwen3 may emit reasoning separately or within <think> tags.
        # Do not expose model thinking text as chat content.
        in_think = False
        think_buffer = ""
        completed = False
        started_at = time.perf_counter()
        first_visible_at = None
        visible_parts = 0
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10), trust_env=model_http_trust_env('ollama_proxy', self.base_url)) as client:
                counter = model_call_counter.get()
                if counter is not None:
                    counter["total"] += 1
                async with client.stream("POST", url, json=request_body) as response:
                    if response.status_code >= 400:
                        raise ModelUnavailable(f"Ollama gateway HTTP {response.status_code}")
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise ModelUnavailable("Invalid gateway NDJSON") from exc
                        if payload.get("error"):
                            raise ModelUnavailable("Ollama gateway reported an error")
                        message = payload.get("message") or {}
                        content = message.get("content", "")
                        if not isinstance(content, str):
                            raise ModelUnavailable("Invalid gateway content")
                        # Suppress reasoning blocks, including markers crossing chunks.
                        think_buffer += content
                        while think_buffer:
                            if in_think:
                                end = think_buffer.find("</think>")
                                if end < 0:
                                    think_buffer = think_buffer[-7:]
                                    break
                                think_buffer = think_buffer[end + len("</think>"):]
                                in_think = False
                            else:
                                start = think_buffer.find("<think>")
                                if start >= 0:
                                    if start:
                                        if first_visible_at is None:
                                            first_visible_at = time.perf_counter()
                                            logger.info("qwen_proxy_first_visible_ms=%d", (first_visible_at-started_at)*1000)
                                        visible_parts += 1
                                        yield think_buffer[:start]
                                    think_buffer = think_buffer[start + len("<think>"):]
                                    in_think = True
                                else:
                                    # Keep a short suffix in case a split marker follows.
                                    reserve = 0
                                    for j in range(1, min(6, len(think_buffer)) + 1):
                                        if "<think>".startswith(think_buffer[-j:]):
                                            reserve = j
                                    plain = think_buffer[:-reserve] if reserve else think_buffer
                                    if plain:
                                        if first_visible_at is None:
                                            first_visible_at = time.perf_counter()
                                            logger.info("qwen_proxy_first_visible_ms=%d", (first_visible_at-started_at)*1000)
                                        visible_parts += 1
                                        yield plain
                                    think_buffer = think_buffer[-reserve:] if reserve else ""
                                    break
                        if payload.get("done") is True:
                            if payload.get("done_reason") in ("length", "max_tokens"):
                                raise ModelUnavailable("Ollama gateway output limit reached")
                            completed = True
                            break
                    if not completed:
                        raise ModelUnavailable("Ollama gateway stream ended without done=true")
                    if think_buffer and not in_think:
                        if first_visible_at is None:
                            first_visible_at = time.perf_counter()
                            logger.info("qwen_proxy_first_visible_ms=%d", (first_visible_at-started_at)*1000)
                        visible_parts += 1
                        yield think_buffer
                    logger.info("qwen_proxy_completed_ms=%d visible_parts=%d", (time.perf_counter()-started_at)*1000, visible_parts)
        except httpx.HTTPError as exc:
            raise ModelUnavailable("Ollama gateway network/timeout failure") from exc

def make_provider(settings):
    if settings.model_provider == "fake":
        return FakeProvider()
    if settings.model_provider == "openai_compatible":
        return OpenAICompatibleProvider(settings.model_base_url, settings.model_api_key,
                                       settings.model_name,settings.model_timeout_seconds)
    if settings.model_provider == "ollama_proxy":
        return OllamaProxyProvider(settings.model_base_url, settings.model_name, settings.model_timeout_seconds, settings.model_max_output_tokens)
    raise ValueError("MODEL_PROVIDER must be fake, openai_compatible, or ollama_proxy")
