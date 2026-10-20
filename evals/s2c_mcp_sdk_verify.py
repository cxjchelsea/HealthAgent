"""Real MCP Python SDK interoperability against HealthAgent's separate stdio server.

Use the independent SDK client, not the in-repository JSON-RPC client.
"""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def verify():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "agent.mcp_stdio_server"]
    )
    async with asyncio.timeout(15):
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                negotiated = await session.initialize()
                assert negotiated.protocolVersion, "Protocol version missing"
                tools = await session.list_tools()
                names = [tool.name for tool in tools.tools]
                assert names == ["mcp_reference_lookup"], names
                success = await session.call_tool(
                    "mcp_reference_lookup", arguments={"term": "rag"}
                )
                assert not success.isError
                value = json.loads(success.content[0].text)
                assert value["ok"] and value["term"] == "rag"
                missing = await session.call_tool(
                    "mcp_reference_lookup", arguments={"term": "not_present"}
                )
                assert missing.isError
                return {"protocol_version": str(negotiated.protocolVersion),
                        "tools": names, "success": True, "negative_case": True}


def main():
    print(json.dumps(asyncio.run(verify()), ensure_ascii=False))


if __name__ == "__main__":
    main()
