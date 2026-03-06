"""
GraphEnforcer addon — the core of aMaze's execution control.

For sessions with an ExecutionGraph, validates every outbound call against the
current expected step. Enforces:
  - Call type must match the current step's call_type
  - callee_id must match (if specified on the step)
  - Per-edge loop counter must not exceed step.max_loops
  - Per-edge token usage must not exceed step.token_cap (pre-check)

On success: atomically increments the loop counter and advances current_step.
On failure: returns HTTP 403/429 with structured JSON error.

Redis keys used:
  session:{id}:graph           — cached ExecutionGraph JSON
  session:{id}:current_step    — current step_id (int)
  session:{id}:step:{n}:loops  — loop counter for step n
  session:{id}:step:{n}:tokens — token counter for step n
"""

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


def _block(flow: http.HTTPFlow, status: int, error: str, **kwargs) -> None:
    body = {"error": error, **kwargs}
    flow.response = http.Response.make(
        status,
        json.dumps(body),
        {"Content-Type": "application/json"},
    )
    flow.metadata["blocked"] = True
    flow.metadata["block_reason"] = error


class GraphEnforcer:
    def request(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get("blocked"):
            return

        session_id = flow.metadata.get("session_id")
        call_type = flow.metadata.get("call_type")

        # Skip: no session (registry passthrough), or non-enforced call types
        if not session_id or call_type in ("registry", "unknown", None):
            return

        r = get_redis()

        # Load graph — if no graph defined, skip enforcement
        graph_raw = r.get(f"session:{session_id}:graph")
        if not graph_raw:
            return

        graph = json.loads(graph_raw)
        current_step_id = int(r.get(f"session:{session_id}:current_step") or graph["start_step_id"])

        # Find the step definition
        step = next(
            (s for s in graph["steps"] if s["step_id"] == current_step_id), None
        )
        if step is None:
            _block(
                flow, 403, "graph_violation",
                reason="current_step not found in graph",
                current_step=current_step_id,
            )
            return

        # Terminal step — no further calls allowed
        if step["call_type"] == "terminal":
            _block(
                flow, 403, "graph_violation",
                reason="session_is_terminal",
                current_step=current_step_id,
            )
            return

        # --- Validate call_type ---
        if step["call_type"] != call_type:
            _block(
                flow, 403, "graph_violation",
                expected={"call_type": step["call_type"], "callee_id": step.get("callee_id")},
                got={"call_type": call_type, "callee_id": flow.metadata.get("callee_id")},
                step_id=current_step_id,
            )
            self._emit_violation(session_id, current_step_id, step, call_type, flow, r)
            return

        # --- Validate callee_id (if the step requires a specific target) ---
        callee_id = flow.metadata.get("callee_id")
        if step.get("callee_id") and step["callee_id"] != callee_id:
            _block(
                flow, 403, "graph_violation",
                expected={"call_type": step["call_type"], "callee_id": step["callee_id"]},
                got={"call_type": call_type, "callee_id": callee_id},
                step_id=current_step_id,
            )
            self._emit_violation(session_id, current_step_id, step, call_type, flow, r)
            return

        # --- Check per-edge loop counter ---
        loops_key = f"session:{session_id}:step:{current_step_id}:loops"
        current_loops = int(r.get(loops_key) or 0)
        max_loops = step.get("max_loops", 1)

        if current_loops >= max_loops:
            _block(
                flow, 429, "edge_loop_exceeded",
                step_id=current_step_id,
                label=step.get("label", ""),
                limit=max_loops,
                current=current_loops,
            )
            return

        # --- Check per-edge token cap (pre-flight: current usage only) ---
        token_cap = step.get("token_cap")
        if token_cap is not None:
            tokens_key = f"session:{session_id}:step:{current_step_id}:tokens"
            current_tokens = int(r.get(tokens_key) or 0)
            if current_tokens >= token_cap:
                _block(
                    flow, 429, "edge_token_cap_exceeded",
                    step_id=current_step_id,
                    label=step.get("label", ""),
                    cap=token_cap,
                    current=current_tokens,
                )
                return

        # --- All checks passed ---

        # Atomically increment loop counter
        r.incr(loops_key)

        # Advance step pointer
        next_step_ids = step.get("next_step_ids", [])
        if next_step_ids:
            # Support branching: agent can declare intended next step via header
            declared = flow.request.headers.get("X-Amaze-Next-Step")
            if declared and declared.isdigit() and int(declared) in next_step_ids:
                next_step = int(declared)
            else:
                next_step = next_step_ids[0]
            r.set(f"session:{session_id}:current_step", next_step)
        # else: no next steps — step stays at current (terminal step will block next call)

        # Store step info for event emission and token counter
        flow.metadata["current_step_id"] = current_step_id
        flow.metadata["step"] = step

        logger.debug(
            "Graph step %d passed for session %s (call_type=%s loops=%d/%d)",
            current_step_id, session_id, call_type, current_loops + 1, max_loops,
        )

    def _emit_violation(
        self, session_id: str, step_id: int, step: dict,
        actual_call_type: str, flow: http.HTTPFlow, r: redis.Redis,
    ) -> None:
        """Publish a graph_violation event to the session stream."""
        import datetime, json as _json
        event = {
            "event_type": "graph_violation",
            "session_id": session_id,
            "step_id": step_id,
            "expected": {"call_type": step["call_type"], "callee_id": step.get("callee_id")},
            "got": {
                "call_type": actual_call_type,
                "callee_id": flow.metadata.get("callee_id"),
            },
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
        try:
            r.publish(f"session:{session_id}:events", _json.dumps(event))
        except Exception as exc:
            logger.warning("Failed to publish graph_violation event: %s", exc)
