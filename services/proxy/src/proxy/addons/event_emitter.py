"""
EventEmitter addon.

Publishes structured JSON events to the Redis pub/sub channel
`session:{id}:events` after each successful proxied call.

The API Gateway subscribes to this channel and relays events over WebSocket
to the UI, enabling live session monitoring.
"""

import datetime
import json
import logging

import redis
from mitmproxy import http

from proxy.config import REDIS_URL

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def _now() -> str:
    return datetime.datetime.utcnow().isoformat()


class EventEmitter:
    def response(self, flow: http.HTTPFlow) -> None:
        """Emit a call event after receiving a response from upstream."""
        if flow.metadata.get("blocked"):
            return

        session_id = flow.metadata.get("session_id")
        if not session_id:
            return

        call_type = flow.metadata.get("call_type")
        if call_type in ("unknown", "registry", None):
            return

        r = get_redis()
        step_id = flow.metadata.get("current_step_id")
        tokens_delta = flow.metadata.get("tokens_delta", 0)
        status_code = flow.response.status_code if flow.response else 0

        if call_type == "llm_call":
            event = {
                "event_type": "llm_call",
                "session_id": session_id,
                "step_id": step_id,
                "provider": flow.metadata.get("llm_provider", "unknown"),
                "tokens_delta": tokens_delta,
                "status_code": status_code,
                "timestamp": _now(),
            }
        elif call_type == "mcp_call":
            event = {
                "event_type": "mcp_call",
                "session_id": session_id,
                "step_id": step_id,
                "tool_name": flow.metadata.get("tool_name", ""),
                "success": 200 <= status_code < 300,
                "status_code": status_code,
                "timestamp": _now(),
            }
        elif call_type == "agent_call":
            event = {
                "event_type": "agent_call",
                "session_id": session_id,
                "step_id": step_id,
                "target_agent": flow.metadata.get("callee_id", ""),
                "status_code": status_code,
                "timestamp": _now(),
            }
        else:
            return

        try:
            r.publish(f"session:{session_id}:events", json.dumps(event))
        except Exception as exc:
            logger.warning("Failed to publish event for session %s: %s", session_id, exc)
