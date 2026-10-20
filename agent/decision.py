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

class ToolDecisionError(ModelUnavailable):
    """Typed, redacted failure reason for model tool decision."""
    def __init__(self, code: str, metadata: dict | None = None):
        super().__init__(code)
        self.code = code
        self.metadata = metadata or {}


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
                raise ToolDecisionError("missing_credentials")
            url = s.model_base_url.rstrip("/") + "/chat/completions"
            body = {"model": s.model_name, "messages": messages, "tools": tools,
                    "tool_choice": "auto", "stream": False, "temperature": 0}
            headers = {"Authorization": "Bearer " + s.model_api_key}
        else:
            raise ToolDecisionError("unsupported_provider")
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
        # Retry one empty decision at most; never retry side-effecting tool calls.
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=s.model_timeout_seconds) as client:
                    response = await client.post(url, json=body, headers=headers)
                    response.raise_for_status()
                    payload = response.json()
            except httpx.HTTPStatusError as exc:
                raise ToolDecisionError("http_status") from exc
            except httpx.TimeoutException as exc:
                raise ToolDecisionError("timeout") from exc
            except httpx.TransportError as exc:
                raise ToolDecisionError("network") from exc
            except ValueError as exc:
                raise ToolDecisionError("invalid_json_response") from exc
            if not isinstance(payload, dict):
                raise ToolDecisionError("invalid_response")
            if payload.get("error"):
                raise ToolDecisionError("provider_rejected")
            if s.model_provider == "ollama_proxy":
                message = payload.get("message", {})
            else:
                choices = payload.get("choices") or []
                if not isinstance(choices, list) or (
                    choices and not isinstance(choices[0], dict)
                ):
                    raise ToolDecisionError("invalid_response")
                message = choices[0].get("message", {}) if choices else {}
            if not isinstance(message, dict):
                raise ToolDecisionError("invalid_response")
            raw_calls = message.get("tool_calls") or []
            if not isinstance(raw_calls, list):
                raise ToolDecisionError("malformed_tool_call")
            calls = []
            for index, raw in enumerate(raw_calls):
                if not isinstance(raw, dict) or not isinstance(raw.get("function"), dict):
                    raise ToolDecisionError("malformed_tool_call")
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
                raise ToolDecisionError("invalid_decision_content")
            if calls or content.strip():
                return Decision(content=content, tool_calls=calls)
            thinking = message.get("thinking")
            metadata = {
                "message_present": "message" in payload,
                "content_chars": len(content),
                "tool_call_count": len(calls),
                "thinking_present": isinstance(thinking, str) and bool(thinking),
                "done_reason": (payload.get("done_reason")
                                if payload.get("done_reason") in
                                ("stop", "length", "max_tokens") else "other"),
                "retry_count": attempt,
            }
            if attempt == 1:
                raise ToolDecisionError("empty_decision", metadata)
