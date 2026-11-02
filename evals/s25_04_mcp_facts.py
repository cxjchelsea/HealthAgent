"""Conservative bilingual correctness check for the fixed public mock MCP catalog.

This is a fixture-specific check, not an unrestricted semantic evaluator.
"""
import re


def mcp_definition_facts(answer: str, definition: str, source: str) -> dict[str, bool]:
    text = answer.casefold()
    known = definition.casefold() == (
        "model context protocol: a protocol for connecting tools and data sources."
    )
    expanded = ("model context protocol" in text
                or "模型上下文协议" in text)
    protocol = bool(re.search(r"\bprotocol\b|协议", text))
    connects = bool(re.search(r"\bconnect(?:s|ing|ion)?\b|连接|接入|对接", text))
    tools = bool(re.search(r"\btools?\b|工具", text))
    data = bool(re.search(r"\bdata(?:\s+sources?)?\b|数据源|数据来源", text))
    return {
        "fixture_supported": known,
        "expanded_name": expanded,
        "protocol": protocol,
        "connects": connects,
        "tools": tools,
        "data_sources": data,
        "source": bool(source) and source.casefold() in text,
    }


def mcp_definition_faithful(answer: str, definition: str, source: str) -> bool:
    facts = mcp_definition_facts(answer, definition, source)
    return all(facts.values())
