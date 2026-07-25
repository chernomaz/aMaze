"""S9 addon: per-agent MCP `tools/list` filtering.

Rewrites `result.tools` on every MCP `tools/list` response down to the
intersection with `policy.allowed_tools`, so an agent never sees the
descriptions of tools it is not authorised to call. See ADR-001.

Runs between StreamBlocker and PiiRedactor (see `services/proxy/main.py`
chain-order comment). Independent of PiiRedactor — this addon only touches
`tools/list`; PII only touches `tools/call`.

Correlation: MCP streamable-http can answer a POST with either an inline
JSON body or SSE frames (POST-SSE), and unrelated responses can also flow
back over the long-lived GET-SSE channel. The response side may run in a
different flow than the request side. We correlate via
`mcp_pending_list:{sid}:{jsonrpc_id}` STRING agent_id, 120 s TTL, mirroring
the `mcp_pending:*` pattern audit_log uses for tools/call.

Fail-closed: any Redis error inside the response hook while a matching
pending key exists → deny 503 `redis-unavailable`. Matches the enforcer's
convention: a broken policy plane must never leak tool descriptions.
"""
from __future__ import annotations

import json
import logging

import redis.asyncio.client as redis
from mitmproxy import http

from services.proxy._redis import client as redis_client
from services.proxy.deny import deny
from services.proxy import policy_store

logger = logging.getLogger("amaze.proxy.tool_list_filter")

MCP_SESSION_HEADER = "mcp-session-id"
PENDING_KEY_PREFIX = "mcp_pending_list"
PENDING_TTL = 120  # seconds — matches PII/tool-call pending TTL


def _pending_key(mcp_session_id: str, jsonrpc_id: object) -> str:
    return f"{PENDING_KEY_PREFIX}:{mcp_session_id}:{jsonrpc_id}"


def _parse_json_rpc(raw: bytes) -> dict | None:
    if not raw:
        return None
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return body if isinstance(body, dict) else None


def _is_tools_list_request(body: dict | None) -> bool:
    return bool(body) and body.get("method") == "tools/list"


def _filter_tools_payload(
    payload: dict, allowed: set[str]
) -> tuple[dict, int, int]:
    """Return (rewritten_payload, before, after).

    Copies `payload` shallowly, replaces `result.tools` with the filtered
    subset. Leaves `result` metadata (nextCursor etc.) untouched. If
    `payload` is not a JSON-RPC response with a tools list, returns the
    payload unchanged with before == after == 0.
    """
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload, 0, 0
    tools = result.get("tools")
    if not isinstance(tools, list):
        return payload, 0, 0
    before = len(tools)
    filtered = [
        t for t in tools
        if isinstance(t, dict) and isinstance(t.get("name"), str)
        and t["name"] in allowed
    ]
    after = len(filtered)
    if after == before:
        return payload, before, after
    new_result = dict(result)
    new_result["tools"] = filtered
    new_payload = dict(payload)
    new_payload["result"] = new_result
    return new_payload, before, after


def _extract_sse_frames(content: bytes) -> list[tuple[str, str]]:
    """Split a buffered SSE body into (raw_frame, data_payload) tuples.

    Returns the frames in order. `data_payload` is the concatenated `data:`
    lines with the leading whitespace stripped. `raw_frame` is the exact
    substring from the source body (needed so we can splice a rewritten
    frame back in without touching separators of other frames).
    """
    text = content.decode("utf-8", errors="replace")
    # Prefer CRLF separator if any frame ends with \r\n\r\n; else LF.
    sep = "\r\n\r\n" if "\r\n\r\n" in text else "\n\n"
    frames: list[tuple[str, str]] = []
    for raw_frame in text.split(sep):
        if not raw_frame.strip():
            continue
        data_lines: list[str] = []
        for line in raw_frame.splitlines():
            stripped = line.lstrip("\r")
            if stripped.startswith("data:"):
                data_lines.append(stripped[5:].lstrip(" "))
        payload = "\n".join(data_lines) if data_lines else ""
        frames.append((raw_frame, payload))
    return frames


class ToolListFilter:
    """See module docstring."""

    # ------------------------------------------------------------------ request

    async def request(self, flow: http.HTTPFlow) -> None:
        if flow.response is not None:
            return
        if flow.metadata.get("amaze_bypass"):
            return
        if flow.metadata.get("amaze_kind") != "mcp":
            return
        agent_id = flow.metadata.get("amaze_agent")
        if not agent_id:
            return
        if flow.request.method != "POST":
            return
        body = _parse_json_rpc(flow.request.content or b"")
        if not _is_tools_list_request(body):
            return

        mcp_session_id = flow.request.headers.get(MCP_SESSION_HEADER, "")
        if not mcp_session_id:
            # Some servers accept the very first tools/list before a session
            # id is negotiated. Correlate on request id alone with an empty
            # session slot — collision would need two agents to share an
            # empty session id (impossible in practice).
            mcp_session_id = ""
        jsonrpc_id = body.get("id")
        if jsonrpc_id is None:
            # Malformed JSON-RPC (id required for a request expecting a
            # response). Let it flow through — the server will 4xx.
            return

        try:
            r = await redis_client()
            await r.set(
                _pending_key(mcp_session_id, jsonrpc_id),
                agent_id,
                ex=PENDING_TTL,
            )
        except redis.RedisError as exc:
            logger.error(
                "tool_list_filter: pending write failed for agent=%s: %s — "
                "denying tools/list (fail-closed)", agent_id, exc,
            )
            deny(flow, "redis-unavailable", status=503)
            return

    # ---------------------------------------------------------------- response

    async def response(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get("amaze_bypass"):
            return
        resp = flow.response
        if resp is None:
            return
        # Only MCP flows carry pending list keys we care about.
        if flow.metadata.get("amaze_kind") != "mcp":
            return
        if resp.status_code != 200:
            return

        mcp_session_id = (
            flow.request.headers.get(MCP_SESSION_HEADER, "")
            or resp.headers.get(MCP_SESSION_HEADER, "")
        )
        # session id may still be empty on the very first tools/list; that
        # matches the empty slot the request hook wrote.

        content_type = (resp.headers.get("content-type", "") or "").lower()
        raw = resp.content or b""
        if not raw:
            return

        if "text/event-stream" in content_type:
            await self._filter_sse(flow, mcp_session_id, raw)
        else:
            await self._filter_json(flow, mcp_session_id, raw)

    # ---------------------------------------------------------- helpers: JSON

    async def _filter_json(
        self, flow: http.HTTPFlow, mcp_session_id: str, raw: bytes,
    ) -> None:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        jsonrpc_id = payload.get("id")
        if jsonrpc_id is None:
            return

        allowed = await self._lookup_allowed(flow, mcp_session_id, jsonrpc_id)
        if allowed is None:
            return

        new_payload, before, after = _filter_tools_payload(payload, allowed)
        if before == 0:
            return  # not a tools/list response after all — leave alone
        if before != after:
            flow.response.content = json.dumps(
                new_payload, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")
        flow.metadata["amaze_tool_list_filtered"] = {
            "before": before, "after": after,
        }
        logger.info(
            "tool_list_filter: filtered agent=%s %d → %d tools",
            flow.metadata.get("amaze_agent"), before, after,
        )

    # ----------------------------------------------------------- helpers: SSE

    async def _filter_sse(
        self, flow: http.HTTPFlow, mcp_session_id: str, raw: bytes,
    ) -> None:
        frames = _extract_sse_frames(raw)
        if not frames:
            return

        # Detect the pinned separator so we splice the same one back.
        text = raw.decode("utf-8", errors="replace")
        sep = "\r\n\r\n" if "\r\n\r\n" in text else "\n\n"

        any_changed = False
        total_before = 0
        total_after = 0
        rewritten_frames: list[str] = []

        for raw_frame, payload in frames:
            if not payload:
                rewritten_frames.append(raw_frame)
                continue
            try:
                data = json.loads(payload)
            except (ValueError, TypeError):
                rewritten_frames.append(raw_frame)
                continue
            if not isinstance(data, dict):
                rewritten_frames.append(raw_frame)
                continue
            jsonrpc_id = data.get("id")
            if jsonrpc_id is None:
                rewritten_frames.append(raw_frame)
                continue

            allowed = await self._lookup_allowed(flow, mcp_session_id, jsonrpc_id)
            if allowed is None:
                rewritten_frames.append(raw_frame)
                continue

            new_data, before, after = _filter_tools_payload(data, allowed)
            if before == 0:
                rewritten_frames.append(raw_frame)
                continue

            total_before += before
            total_after += after

            if before != after:
                any_changed = True
                # Replace only the `data:` line(s). Preserve other frame
                # fields (event:, id:, retry:) if present.
                new_payload_str = json.dumps(
                    new_data, separators=(",", ":"), ensure_ascii=False,
                )
                new_lines: list[str] = []
                replaced = False
                for line in raw_frame.splitlines():
                    if line.lstrip("\r").startswith("data:") and not replaced:
                        new_lines.append(f"data: {new_payload_str}")
                        replaced = True
                    elif line.lstrip("\r").startswith("data:"):
                        # Skip additional data: lines — merged into first.
                        continue
                    else:
                        new_lines.append(line)
                rewritten_frames.append("\n".join(new_lines))
            else:
                rewritten_frames.append(raw_frame)

        if total_before == 0:
            return  # no tools/list frames matched
        if any_changed:
            flow.response.content = sep.join(rewritten_frames).encode("utf-8") + sep.encode("utf-8")
        flow.metadata["amaze_tool_list_filtered"] = {
            "before": total_before, "after": total_after,
        }
        logger.info(
            "tool_list_filter: filtered SSE agent=%s %d → %d tools",
            flow.metadata.get("amaze_agent"), total_before, total_after,
        )

    # ----------------------------------------------------- helpers: pending key

    async def _lookup_allowed(
        self,
        flow: http.HTTPFlow,
        mcp_session_id: str,
        jsonrpc_id: object,
    ) -> set[str] | None:
        """Return the allowed-tool set for this pending list, or None if the
        response is not one of ours.

        Fail-closed: on Redis error we still had a pending key claim, so we
        deny the response body via the caller.
        """
        key = _pending_key(mcp_session_id, jsonrpc_id)
        try:
            r = await redis_client()
            agent_id = await r.get(key)
        except redis.RedisError as exc:
            logger.error(
                "tool_list_filter: pending lookup failed: %s — denying "
                "response (fail-closed)", exc,
            )
            deny(flow, "redis-unavailable", status=503)
            return None
        if not agent_id:
            return None
        if isinstance(agent_id, bytes):
            agent_id = agent_id.decode("utf-8")
        # One-shot: delete so a retry doesn't shadow a stale key.
        try:
            await r.delete(key)
        except redis.RedisError:
            pass  # best-effort; TTL will reclaim

        try:
            policy = await policy_store.get_policy(agent_id)
        except redis.RedisError as exc:
            logger.error(
                "tool_list_filter: policy fetch failed for agent=%s: %s — "
                "denying response (fail-closed)", agent_id, exc,
            )
            deny(flow, "redis-unavailable", status=503)
            return None
        if policy is None:
            # Agent has no policy row — discovery pass-through (matches
            # enforcer.py fallback for unknown agents).
            return None
        return set(policy.allowed_tools)
