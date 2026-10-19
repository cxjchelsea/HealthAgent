"""Native provider tool-call adapter. Never fake a tool decision from text."""
import json
from dataclasses import dataclass
from typing import Any
import httpx
from agent.providers import ModelUnavailable

@dataclass
class Decision:
    content: str
    tool_calls: list[dict[str, Any]]

class NativeToolDecision:
    def __init__(self, settings):
        self.settings = settings

    async def decide(self, messages: list[dict], tools: list[dict]) -> Decision:
        s = self.settings
        if s.model_provider == "ollama_proxy":
            url = s.model_base_url.rstrip("/") + "/chat"
            body = {"model": s.model_name, "messages": messages, "tools": tools,
                    "stream": False, "think": False,
                    "options": {"temperature": 0, "num_predict": s.model_max_output_tokens}}
            headers = {}
        elif s.model_provider == "openai_compatible":
            if not s.model_api_key:
                raise ModelUnavailable("Missing model credentials")
            url = s.model_base_url.rstrip("/") + "/chat/completions"
            body = {"model": s.model_name, "messages": messages, "tools": tools,
                    "tool_choice": "auto", "stream": False, "temperature": 0}
            headers = {"Authorization": "Bearer " + s.model_api_key}
        else:
            raise ModelUnavailable("S2-A requires a tool-calling model provider")
        normalized = []
        for item in messages:
            message = dict(item)
            if s.model_provider == "openai_compatible":
                if message.get("role") == "assistant" and message.get("tool_calls"):
                    message["tool_calls"] = [
                        {**c, "function": {**c["function"],
                         "arguments": json.dumps(c["function"]["arguments"])}}
                        for c in message["tool_calls"]
                    ]
                elif message.get("role") == "tool":
                    message.pop("tool_name", None)
            elif message.get("role") == "tool":
                message.pop("tool_call_id", None)
            normalized.append(message)
        body["messages"] = normalized
        try:
            async with httpx.AsyncClient(timeout=s.model_timeout_seconds) as client:
                response = await client.post(url, json=body, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelUnavailable("Tool decision provider request failed") from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise ModelUnavailable("Provider refused tool decision")
        if s.model_provider == "ollama_proxy":
            message = payload.get("message", {})
        else:
            choices = payload.get("choices") or []
            message = choices[0].get("message", {}) if choices else {}
        if not isinstance(message, dict):
            raise ModelUnavailable("Invalid tool decision message")
        calls = []
        for index, raw in enumerate(message.get("tool_calls") or []):
            if not isinstance(raw, dict) or not isinstance(raw.get("function"), dict):
                raise ModelUnavailable("Malformed tool_call")
            fn = raw["function"]
            arguments = fn.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = None
            calls.append({"id": str(raw.get("id") or f"call_{index}"),
                          "name": fn.get("name"), "arguments": arguments})
        content = message.get("content") or ""
        if not isinstance(content, str):
            raise ModelUnavailable("Invalid decision content")
        if not calls and not content.strip():
            raise ModelUnavailable("Empty model decision")
        return Decision(content=content, tool_calls=calls)
