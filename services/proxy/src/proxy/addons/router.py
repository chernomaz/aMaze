"""
UpstreamRouter addon.

Rewrites request URLs for amaze-gateway virtual routes before forwarding:
  http://amaze-gateway/mcp/{tool_name}        → http://{mcp_host}:{mcp_port}/call
  http://amaze-gateway/agents/{name}/invoke   → http://orchestrator:8001/sessions/{id}/invoke
  http://amaze-gateway/registry/...           → http://registry:8002/...

LLM API calls (api.openai.com, etc.) are passed through unchanged — the
mitmproxy proxy mode forwards them to their original destination.

MCP routing uses the Registry to resolve the tool's internal host/port.
A simple TTL cache avoids a Registry call on every MCP request.
"""

import json
import logging
import time

import httpx
import redis
from mitmproxy import http
from mitmproxy.net.http import http1

from proxy.config import AMAZE_GATEWAY_HOST, ORCHESTRATOR_URL, REDIS_URL, REGISTRY_URL

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None

# Simple in-process cache for MCP routes: {tool_name: (host, port, expires_at)}
_mcp_route_cache: dict[str, tuple[str, int, float]] = {}
MCP_CACHE_TTL = 60  # seconds


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def _resolve_mcp_tool(tool_name: str) -> tuple[str, int] | None:
    """Look up MCP tool's internal host/port. Uses in-process TTL cache."""
    now = time.time()
    if tool_name in _mcp_route_cache:
        host, port, expires_at = _mcp_route_cache[tool_name]
        if now < expires_at:
            return host, port

    try:
        resp = httpx.get(f"{REGISTRY_URL}/capabilities/{tool_name}", timeout=5)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        host = data["internal_host"]
        port = data["internal_port"]
        _mcp_route_cache[tool_name] = (host, port, now + MCP_CACHE_TTL)
        return host, port
    except Exception as exc:
        logger.error("Failed to resolve MCP tool '%s': %s", tool_name, exc)
        return None


class UpstreamRouter:
    def request(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get("blocked"):
            return

        host = flow.request.pretty_host
        if host != AMAZE_GATEWAY_HOST:
            # LLM calls and other traffic: pass through unchanged
            return

        path = flow.request.path
        call_type = flow.metadata.get("call_type")

        # --- Registry passthrough ---
        if call_type == "registry" or path.startswith("/registry/"):
            # Strip /registry prefix and forward to registry service
            registry_path = path[len("/registry"):]
            flow.request.url = f"http://registry:8002{registry_path}"
            return

        # --- MCP call routing ---
        if call_type == "mcp_call":
            tool_name = flow.metadata.get("tool_name", "")
            route = _resolve_mcp_tool(tool_name)

            if route is None:
                flow.response = http.Response.make(
                    404,
                    json.dumps({"error": "mcp_tool_not_found", "tool_name": tool_name}),
                    {"Content-Type": "application/json"},
                )
                flow.metadata["blocked"] = True
                return

            mcp_host, mcp_port = route
            # Rewrite to MCP container's /call endpoint
            flow.request.url = f"http://{mcp_host}:{mcp_port}/call"
            # Normalize body: MCP containers expect {"tool": name, "input": {...}}
            # (already set by mcp_client.py — no changes needed)
            return

        # --- Agent-to-agent routing ---
        if call_type == "agent_call":
            agent_name = flow.metadata.get("callee_id", "")
            session_id = flow.metadata.get("session_id", "")

            # Route to orchestrator: it will spawn or find the child agent container
            flow.request.url = f"{ORCHESTRATOR_URL}/sessions/{session_id}/invoke-agent"
            # Inject agent name so orchestrator knows which agent to invoke
            flow.request.headers["X-Amaze-Target-Agent"] = agent_name
            return

        # Unknown gateway path
        flow.response = http.Response.make(
            400,
            json.dumps({"error": "unknown_gateway_path", "path": path}),
            {"Content-Type": "application/json"},
        )
        flow.metadata["blocked"] = True
