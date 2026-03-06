"""
SessionIdentifier addon.

Maps the source container IP to a session_id + agent_id using Redis.
Stores results in flow.metadata for downstream addons.

Handles two traffic types:
- Agent containers: have a session_id registered under agent_ip:{ip}
- MCP containers: no session_id — only allowed to call the Registry

Unknown IPs are blocked unless they're accessing registry paths.
"""

import json
import logging

import redis
from mitmproxy import http

from proxy.config import AMAZE_GATEWAY_HOST, REDIS_URL

logger = logging.getLogger(__name__)

# Paths on amaze-gateway that MCP/unknown containers are allowed to call
REGISTRY_PASSTHROUGH_PATHS = ("/registry/register", "/registry/heartbeat")

_redis_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


class SessionIdentifier:
    def request(self, flow: http.HTTPFlow) -> None:
        client_ip = flow.client_conn.peername[0]
        r = get_redis()

        raw = r.get(f"agent_ip:{client_ip}")
        if raw:
            info = json.loads(raw)
            flow.metadata["session_id"] = info["session_id"]
            flow.metadata["agent_id"] = info["agent_id"]
            flow.metadata["client_ip"] = client_ip
            return

        # No session found — check if it's an allowed registry pass-through
        host = flow.request.pretty_host
        path = flow.request.path

        if host == AMAZE_GATEWAY_HOST and any(
            path.startswith(p) for p in REGISTRY_PASSTHROUGH_PATHS
        ):
            # MCP container self-registering — allow, mark as passthrough
            flow.metadata["session_id"] = None
            flow.metadata["is_registry_passthrough"] = True
            logger.debug("Registry passthrough from %s (%s)", client_ip, path)
            return

        # Unknown IP — block
        logger.warning("Blocked request from unknown IP %s (path=%s)", client_ip, path)
        flow.response = http.Response.make(
            403,
            json.dumps({"error": "forbidden", "reason": "unknown_source_ip", "ip": client_ip}),
            {"Content-Type": "application/json"},
        )
        flow.metadata["blocked"] = True
