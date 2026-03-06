"""
MCPClient — helper for calling MCP tools from within an agent container.

All calls go through the proxy (HTTP_PROXY env var). The proxy:
1. Validates the call against the execution graph step
2. Checks per-edge loop and token caps
3. Routes to the correct MCP container on amaze-mcp-net
4. Emits a mcp_call event to the session stream

Usage:
    from agent_runtime import MCPClient

    mcp = MCPClient()
    content = mcp.call("filesystem-mcp.read_file", path="/workspace/notes.txt")
    results = mcp.call("websearch-mcp.search", query="python async patterns")
"""

import os

import httpx


class MCPClient:
    def __init__(self) -> None:
        self._session_id = os.environ.get("AMAZE_SESSION_ID", "")
        self._agent_id = os.environ.get("AMAZE_AGENT_ID", "")
        proxy_url = os.environ.get("AMAZE_PROXY_URL", "http://proxy:8080")

        self._client = httpx.Client(
            proxy=proxy_url,
            headers={
                "X-Amaze-Session-ID": self._session_id,
                "X-Amaze-Agent-ID": self._agent_id,
                "X-Amaze-Call-Type": "mcp_call",
            },
            timeout=60,
        )

    def call(self, tool_name: str, **kwargs) -> dict:
        """
        Call an MCP tool by its fully qualified name (server.tool_name).

        kwargs are passed as the tool input. The proxy routes the request to
        the correct MCP container based on the server prefix in tool_name.

        Example:
            mcp.call("filesystem-mcp.read_file", path="/workspace/input.txt")
            mcp.call("calculator-mcp.calculate", expression="2 ** 10")
        """
        # Proxy intercepts this URL and routes to the MCP server
        resp = self._client.post(
            f"http://amaze-gateway/mcp/{tool_name}",
            json={"tool": tool_name, "input": kwargs},
            headers={"X-Amaze-Tool-Name": tool_name},
        )
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "MCPClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()
