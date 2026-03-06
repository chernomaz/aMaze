"""
PolicyEnforcer addon.

After the GraphEnforcer passes, calls the Policy Engine service with
the current session counters. Blocks the call if policy says block/warn-as-block.

Counters are read from Redis (live values) rather than the DB.
"""

import json
import logging

import httpx
import redis
from mitmproxy import http

from proxy.config import POLICY_ENGINE_URL, REDIS_URL

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def _block(flow: http.HTTPFlow, status: int, error: str, **kwargs) -> None:
    body = {"error": error, **kwargs}
    flow.response = http.Response.make(
        status,
        json.dumps(body),
        {"Content-Type": "application/json"},
    )
    flow.metadata["blocked"] = True
    flow.metadata["block_reason"] = error


class PolicyEnforcer:
    def request(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get("blocked"):
            return

        session_id = flow.metadata.get("session_id")
        call_type = flow.metadata.get("call_type")

        if not session_id or call_type in ("registry", "unknown", None):
            return

        r = get_redis()

        # Load cached policy
        policy_raw = r.get(f"session:{session_id}:policy")
        if not policy_raw:
            logger.warning("No policy found for session %s — allowing call", session_id)
            return

        policy = json.loads(policy_raw)

        # Read live counters from Redis
        counters = {
            "tokens_used": int(r.get(f"session:{session_id}:tokens_used") or 0),
            "tokens_this_turn": 0,  # pre-request; we don't know yet
            "iterations_completed": int(r.get(f"session:{session_id}:iterations_completed") or 0),
            "mcp_calls_made": int(r.get(f"session:{session_id}:mcp_calls_made") or 0),
            "agent_calls_made": int(r.get(f"session:{session_id}:agent_calls_made") or 0),
        }

        payload = {
            "policy": policy,
            "request_type": call_type,
            "tool_name": flow.metadata.get("tool_name"),
            "provider": flow.metadata.get("llm_provider"),
            "estimated_tokens": None,
            "current_counters": counters,
        }

        try:
            resp = httpx.post(
                f"{POLICY_ENGINE_URL}/evaluate",
                json=payload,
                timeout=5,
            )
            resp.raise_for_status()
            result = resp.json()
        except Exception as exc:
            logger.error("Policy engine call failed: %s — allowing call", exc)
            return

        decision = result.get("decision", "allow")
        if decision == "block":
            _block(
                flow, 429, "policy_violation",
                reason=result.get("reason", ""),
                violation_type=result.get("violation_type"),
            )
            self._emit_violation(session_id, result, flow, r)
            return

        if decision == "warn":
            logger.warning(
                "Policy warn for session %s: %s", session_id, result.get("reason")
            )
            flow.metadata["policy_warning"] = result.get("reason")

        # Update call-type counters in Redis (increment before the call)
        if call_type == "mcp_call":
            r.incr(f"session:{session_id}:mcp_calls_made")
        elif call_type == "agent_call":
            r.incr(f"session:{session_id}:agent_calls_made")
        elif call_type == "llm_call":
            r.incr(f"session:{session_id}:iterations_completed")

    def _emit_violation(self, session_id: str, result: dict, flow: http.HTTPFlow, r: redis.Redis) -> None:
        import datetime
        event = {
            "event_type": "policy_violation",
            "session_id": session_id,
            "violation_type": result.get("violation_type"),
            "reason": result.get("reason"),
            "step_id": flow.metadata.get("current_step_id"),
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
        try:
            r.publish(f"session:{session_id}:events", json.dumps(event))
        except Exception as exc:
            logger.warning("Failed to publish policy_violation event: %s", exc)
