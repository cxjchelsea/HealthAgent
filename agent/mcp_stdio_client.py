"""Bounded MCP stdio client with initialize, tool discovery, and tools/call.

Only starts the checked-in trusted server module, never a model-provided command.
A separate subprocess isolates the mock service from the Agent interpreter.
"""
import json
import queue
import threading
import time
import subprocess
import sys

# Local policy, not populated from MCP tools/list or model prompts.
TRUSTED_REFERENCE_SERVER = {"name": "healthagent-trusted-reference", "version": "1.0.0"}
TRUSTED_REFERENCE_TOOL = {
    "name": "mcp_reference_lookup",
    "inputSchema": {
        "type": "object",
        "properties": {"term": {"type": "string", "minLength": 1, "maxLength": 80}},
        "required": ["term"],
        "additionalProperties": False,
    },
}


class StdioMCPClient:
    def __init__(self, timeout_seconds: float = 5.0):
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("MCP timeout must be in (0, 30]")
        self.timeout_seconds = timeout_seconds

    def call(self, name: str, arguments: dict) -> dict:
        return self._call(name, arguments, verify_trusted_catalog=False)

    def call_trusted_reference(self, arguments: dict) -> dict:
        """Strict local-server binding for S25-05 Registry; no remote discovery grants."""
        return self._call_verified_reference(arguments)

    def _call_verified_reference(self, arguments: dict) -> dict:
        """Verify the fixed catalog on one live process before any tool call."""
        deadline = time.monotonic() + self.timeout_seconds
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "agent.mcp_stdio_server"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
            )
        except OSError:
            return {"ok": False, "error": "mcp_transport_error"}
        frames: queue.Queue = queue.Queue()

        def read_frames() -> None:
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    frames.put(line)
            except (OSError, ValueError) as exc:
                frames.put(exc)
            finally:
                frames.put(None)

        threading.Thread(target=read_frames, daemon=True).start()

        def exchange(request: dict) -> dict:
            if process.stdin is None:
                raise OSError("missing stdin")
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            try:
                line = frames.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError from None
            if not isinstance(line, str):
                raise OSError("missing response")
            if len(line) > 100000:
                raise ValueError("oversized response")
            frame = json.loads(line)
            if (not isinstance(frame, dict)
                    or frame.get("jsonrpc") != "2.0"
                    or frame.get("id") != request["id"]
                    or not isinstance(frame.get("result"), dict)):
                raise ValueError("invalid response frame")
            return frame["result"]

        try:
            init = exchange({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18",
                           "capabilities": {},
                           "clientInfo": {"name": "healthagent", "version": "1.0.0"}},
            })
            if (init.get("protocolVersion") != "2025-06-18"
                    or init.get("serverInfo") != TRUSTED_REFERENCE_SERVER):
                return {"ok": False, "error": "mcp_catalog_mismatch"}
            if process.stdin is None:
                raise OSError("missing stdin")
            process.stdin.write(json.dumps({
                "jsonrpc": "2.0", "method": "notifications/initialized",
            }) + "\n")
            process.stdin.flush()
            catalog = exchange({
                "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
            })
            available = catalog.get("tools")
            if (not isinstance(available, list) or len(available) != 1
                    or not isinstance(available[0], dict)
                    or any(available[0].get(key) != value
                           for key, value in TRUSTED_REFERENCE_TOOL.items())):
                return {"ok": False, "error": "mcp_catalog_mismatch"}
            call = exchange({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "mcp_reference_lookup", "arguments": arguments},
            })
            content = call["content"]
            if (not isinstance(content, list) or len(content) != 1
                    or content[0].get("type") != "text"):
                return {"ok": False, "error": "mcp_protocol_error"}
            response = json.loads(content[0]["text"])
            if (not isinstance(response, dict)
                    or not isinstance(response.get("ok"), bool)
                    or call.get("isError", False) != (not response["ok"])):
                return {"ok": False, "error": "mcp_protocol_error"}
            return response
        except TimeoutError:
            return {"ok": False, "error": "mcp_timeout"}
        except (OSError, BrokenPipeError):
            return {"ok": False, "error": "mcp_transport_error"}
        except (AssertionError, KeyError, TypeError, ValueError):
            return {"ok": False, "error": "mcp_protocol_error"}
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()

    def _call(self, name: str, arguments: dict, *, verify_trusted_catalog: bool) -> dict:
        if name != "mcp_reference_lookup":
            return {"ok": False, "error": "mcp_tool_not_allowlisted"}
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "healthagent", "version": "1.0.0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": name, "arguments": arguments}},
        ]
        data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
        try:
            finished = subprocess.run(
                [sys.executable, "-m", "agent.mcp_stdio_server"],
                input=data, text=True, capture_output=True, timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "mcp_timeout"}
        except OSError:
            return {"ok": False, "error": "mcp_transport_error"}
        if finished.returncode or len(finished.stdout) > 100000:
            return {"ok": False, "error": "mcp_transport_error"}
        try:
            frames = [json.loads(s) for s in finished.stdout.splitlines()]
            indexed = {frame["id"]: frame for frame in frames}
            assert set(indexed) == {1, 2, 3}
            assert indexed[1]["result"]["protocolVersion"] == "2025-06-18"
            available = indexed[2]["result"]["tools"]
            if verify_trusted_catalog:
                server = indexed[1]["result"]["serverInfo"]
                if server != TRUSTED_REFERENCE_SERVER:
                    return {"ok": False, "error": "mcp_catalog_mismatch"}
                if (not isinstance(available, list) or len(available) != 1
                        or not isinstance(available[0], dict)
                        or any(available[0].get(k) != v
                               for k, v in TRUSTED_REFERENCE_TOOL.items())):
                    return {"ok": False, "error": "mcp_catalog_mismatch"}
            assert any(t["name"] == name for t in available)
            call = indexed[3]["result"]
            assert len(call["content"]) == 1 and call["content"][0]["type"] == "text"
            response = json.loads(call["content"][0]["text"])
            assert isinstance(response, dict) and isinstance(response.get("ok"), bool)
            assert call.get("isError", False) == (not response["ok"])
            return response
        except (AssertionError, KeyError, TypeError, ValueError):
            return {"ok": False, "error": "mcp_protocol_error"}
