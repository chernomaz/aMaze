"""
TokenCounter addon.

Intercepts LLM API responses (OpenAI-format) and extracts token usage
from the `usage` field in the response body. Updates Redis counters and
checks per-edge token caps post-response.

Supports:
  - Non-streaming responses: parse JSON body directly
  - Streaming responses (SSE): accumulate usage from the final [DONE] chunk

Redis keys updated:
  session:{id}:tokens_used              — global session token total
  session:{id}:step:{n}:tokens          — per-edge token total
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


def _extract_tokens_from_body(body: bytes) -> int | None:
    """Extract total_tokens from an OpenAI-format JSON response body."""
    try:
        data = json.loads(body)
        usage = data.get("usage") or {}
        total = usage.get("total_tokens") or (
            (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
        )
        return int(total) if total else None
    except Exception:
        return None


def _extract_tokens_from_sse(body: bytes) -> int | None:
    """Extract token usage from SSE (streaming) response body."""
    total_tokens = None
    try:
        text = body.decode("utf-8", errors="ignore")
        for line in text.splitlines():
            if not line.startswith("data: "):
                continue
            chunk_str = line[6:]
            if chunk_str.strip() == "[DONE]":
                continue
            try:
                chunk = json.loads(chunk_str)
                usage = chunk.get("usage") or {}
                if usage:
                    t = usage.get("total_tokens") or (
                        (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
                    )
                    if t:
                        total_tokens = int(t)
            except Exception:
                continue
    except Exception:
        pass
    return total_tokens


class TokenCounter:
    def response(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get("blocked"):
            return
        if flow.metadata.get("call_type") != "llm_call":
            return

        session_id = flow.metadata.get("session_id")
        if not session_id or not flow.response:
            return

        content_type = flow.response.headers.get("content-type", "")
        body = flow.response.content

        if "text/event-stream" in content_type:
            tokens = _extract_tokens_from_sse(body)
        else:
            tokens = _extract_tokens_from_body(body)

        if not tokens:
            logger.debug("Could not extract token count from LLM response (session=%s)", session_id)
            return

        r = get_redis()
        flow.metadata["tokens_delta"] = tokens

        # Update global session counter
        r.incrby(f"session:{session_id}:tokens_used", tokens)

        # Update per-step counter if we have step info
        step_id = flow.metadata.get("current_step_id")
        step = flow.metadata.get("step", {})
        if step_id is not None:
            tokens_key = f"session:{session_id}:step:{step_id}:tokens"
            new_total = r.incrby(tokens_key, tokens)

            # Post-response token cap check (warn in log — can't block after response)
            token_cap = step.get("token_cap")
            if token_cap and new_total > token_cap:
                logger.warning(
                    "Edge token cap EXCEEDED (post-response) for session=%s step=%d: "
                    "%d/%d tokens",
                    session_id, step_id, new_total, token_cap,
                )
                # Emit a warning event — the next call on this step will be blocked pre-flight
                self._emit_cap_warning(session_id, step_id, token_cap, new_total, r)

        logger.debug("Counted %d tokens for session %s (step=%s)", tokens, session_id, step_id)

    def _emit_cap_warning(
        self, session_id: str, step_id: int, cap: int, current: int, r: redis.Redis
    ) -> None:
        import datetime
        event = {
            "event_type": "edge_token_cap_exceeded",
            "session_id": session_id,
            "step_id": step_id,
            "cap": cap,
            "current": current,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
        try:
            r.publish(f"session:{session_id}:events", json.dumps(event))
        except Exception as exc:
            logger.warning("Failed to publish edge_token_cap_exceeded event: %s", exc)
