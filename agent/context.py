"""S2-B2 deterministic bounded history selection for the single Agent entry.

Only completed user/assistant messages are admitted by the caller. This module
never treats conversation text as system instructions and never fabricates tool
results or long-term memory.
"""
from typing import Iterable, Any

def build_context(messages: Iterable[Any], current: str, *,
                  max_messages: int, max_chars: int,
                  clarification_message_id: str | None = None) -> list[dict[str, str]]:
    if len(current) > max_chars:
        raise ValueError("Current user input exceeds context character budget")
    selected: list[dict[str, str]] = []
    remaining = max_chars - len(current)
    for message in reversed(list(messages)):
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)
        status = getattr(message, "status", None)
        is_clarification = (role == "assistant" and status == "partial"
                            and getattr(message, "id", None) == clarification_message_id)
        if (status != "completed" and not is_clarification) or role not in ("user", "assistant") or not isinstance(content, str):
            continue
        if len(selected) >= max_messages:
            break
        if len(content) > remaining:
            break  # preserve message integrity: never truncate a claim into misleading text
        selected.append({"role": role, "content": content})
        remaining -= len(content)
    selected.reverse()
    return selected
