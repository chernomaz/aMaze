"""S9 addon: push `notifications/tools/list_changed` to live agent SSE
streams when their policy's `allowed_tools` changes.

Startup subscribes to Redis Pub/Sub channel `policy:changed`. When the
orchestrator publishes `{agent_id, changed_fields}` and `allowed_tools`
is in `changed_fields`, this addon queues a
`notifications/tools/list_changed` JSON-RPC frame into every live MCP
GET-SSE flow owned by that agent.

Delivery model
--------------
mitmproxy's `flow.response.stream` handler is a synchronous per-chunk
callback — it only runs when upstream sends a chunk. We can't push bytes
independently. So injection works as follows:

1. `responseheaders` detects an MCP GET-SSE flow, wraps the existing
   stream handler (set by AuditLog upstream in the chain) with a
   drain-queue-then-forward wrapper, and registers the flow in
   `agent_sse_streams[agent_id]`.
2. Pub/Sub consumer appends a frame to each affected flow's `_inject_q`.
3. On the next upstream chunk (SSE keepalive / real message), the
   wrapper prepends the queued frames and forwards.

MCP servers typically send SSE keepalive pings every 15-30 s, so end-to-
end injection latency is bounded by that. Documented trade-off vs the
forced-reconnect option (see ADR-001 §Option C).

Chain position: AFTER AuditLog. AuditLog's GET-SSE interceptor already
owns `flow.response.stream`. This addon wraps it so both keep working.
"""
from __future__ import annotations

import asyncio
import json
import logging
import weakref
from typing import Callable

import redis.asyncio.client as redis
from mitmproxy import http

from services.proxy._redis import client as redis_client

logger = logging.getLogger("amaze.proxy.list_change_notifier")

POLICY_CHANGED_CHANNEL = "policy:changed"

# The frame we inject when allowed_tools changes.
_LIST_CHANGED_PAYLOAD = json.dumps(
    {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"},
    separators=(",", ":"),
)


def _list_changed_frame(sep: bytes) -> bytes:
    """Build the SSE frame bytes matching the flow's pinned LF/CRLF
    separator. Same trick as PII SSE (CHANGELOG §0.8) — mixing separators
    mid-stream can glue frames together on strict parsers.
    """
    return f"data: {_LIST_CHANGED_PAYLOAD}".encode("utf-8") + sep


class _FlowSlot:
    """Per-flow injection state.

    `_inject_q` is a bytes list drained by the wrapper on the next
    upstream chunk. `sep` is pinned on first upstream chunk sighting.
    `lock` guards concurrent queue writes from the Pub/Sub consumer vs.
    the mitmproxy event loop draining.
    """
    # NOTE: `__weakref__` must be in __slots__ for WeakSet to hold slots
    # instances. Without it, WeakSet(_FlowSlot()) raises TypeError:
    # "cannot create weak reference to '_FlowSlot' object" — an mtmproxy-
    # instance-hangs bug on every registered SSE flow.
    __slots__ = (
        "flow", "_inject_q", "sep", "lock", "_original_handler",
        "__weakref__",
    )

    def __init__(self, flow: http.HTTPFlow, original: Callable[[bytes], bytes] | bool):
        self.flow = flow
        self._inject_q: list[bytes] = []
        self.sep: bytes = b"\n\n"  # default; pinned on first chunk
        self.lock = asyncio.Lock()
        self._original_handler = original

    def queue(self, payload: bytes) -> None:
        self._inject_q.append(payload)

    def drain(self) -> bytes:
        if not self._inject_q:
            return b""
        out = b"".join(self._inject_q)
        self._inject_q.clear()
        return out


class ListChangeNotifier:
    """See module docstring."""

    def __init__(self) -> None:
        # agent_id -> set of _FlowSlot (weakref to avoid leaking on flow end)
        self._agents: dict[str, "weakref.WeakSet[_FlowSlot]"] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ load

    def load(self, loader) -> None:  # noqa: ARG002 — mitmproxy hook signature
        pass

    def running(self) -> None:
        """mitmproxy `running` hook — event loop is up. Spawn Pub/Sub task."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._pubsub_loop(),
                name="amaze.list_change_notifier.pubsub",
            )
            logger.info("list_change_notifier: pubsub task started")

    async def done(self) -> None:
        """mitmproxy `done` hook — shutdown."""
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    # --------------------------------------------------------- responseheaders

    async def responseheaders(self, flow: http.HTTPFlow) -> None:
        """Register live MCP GET-SSE flows so we can inject into them."""
        if flow.metadata.get("amaze_bypass"):
            return
        if flow.metadata.get("amaze_kind") != "mcp":
            return
        if flow.request.method != "GET":
            return
        resp = flow.response
        if resp is None:
            return
        ct = (resp.headers.get("content-type", "") or "").lower()
        if "text/event-stream" not in ct:
            return

        agent_id = flow.metadata.get("amaze_agent")
        if not agent_id:
            return

        original = flow.response.stream  # may be False, True, or a callable
        slot = _FlowSlot(flow, original)
        flow.metadata["amaze_list_change_slot"] = slot
        flow.response.stream = self._wrap(slot)
        self._agents.setdefault(agent_id, weakref.WeakSet()).add(slot)
        logger.debug(
            "list_change_notifier: registered GET-SSE flow agent=%s", agent_id,
        )

    def _wrap(self, slot: _FlowSlot) -> Callable[[bytes], bytes]:
        """Return a stream handler that prepends queued injections to each
        upstream chunk, then delegates to the original handler (if any).
        """
        first_chunk = True

        def handle(chunk: bytes) -> bytes:
            nonlocal first_chunk
            if first_chunk:
                # Pin LF/CRLF separator from the first chunk we see.
                if b"\r\n\r\n" in chunk:
                    slot.sep = b"\r\n\r\n"
                else:
                    slot.sep = b"\n\n"
                first_chunk = False

            injected = slot.drain()

            if callable(slot._original_handler):
                # Chain: let audit_log's handler transform the upstream chunk
                # first, then prepend our injection to the downstream bytes.
                downstream_chunk = slot._original_handler(chunk)
            else:
                downstream_chunk = chunk

            if injected:
                logger.info(
                    "list_change_notifier: injecting %d bytes on next chunk "
                    "for agent=%s", len(injected),
                    slot.flow.metadata.get("amaze_agent"),
                )
                # Tag the flow so debug_pauser can skip these frames when
                # recording steps — they are proxy-originated, not upstream.
                slot.flow.metadata["amaze_injected_list_changed"] = True
                return injected + downstream_chunk
            return downstream_chunk

        return handle

    # ------------------------------------------------------------- pubsub loop

    async def _pubsub_loop(self) -> None:
        """Subscribe to `policy:changed` with reconnect backoff. Fan out
        injections to matching agent slots.
        """
        backoff = 1.0
        while not self._stop.is_set():
            try:
                r = await redis_client()
                pubsub = r.pubsub()
                await pubsub.subscribe(POLICY_CHANGED_CHANNEL)
                logger.info(
                    "list_change_notifier: subscribed to %s",
                    POLICY_CHANGED_CHANNEL,
                )
                backoff = 1.0
                async for msg in pubsub.listen():
                    if msg is None:
                        continue
                    if msg.get("type") != "message":
                        continue
                    await self._handle_message(msg.get("data"))
            except asyncio.CancelledError:
                return
            except redis.RedisError as exc:
                logger.warning(
                    "list_change_notifier: pubsub error, reconnecting in %.1fs: %s",
                    backoff, exc,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            except Exception as exc:  # noqa: BLE001 — keep the loop alive
                logger.error(
                    "list_change_notifier: unexpected pubsub error: %s", exc,
                    exc_info=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _handle_message(self, raw: object) -> None:
        if raw is None:
            return
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("list_change_notifier: bad message payload: %r", raw)
            return
        agent_id = data.get("agent_id")
        changed = data.get("changed_fields") or []
        if not agent_id or "allowed_tools" not in changed:
            return

        slots = self._agents.get(agent_id)
        if not slots:
            logger.debug(
                "list_change_notifier: no live GET-SSE for agent=%s "
                "(next tools/list will still be filtered)", agent_id,
            )
            return

        # Queue the frame into every live flow. Actual delivery happens on
        # the next upstream chunk (typically an SSE keepalive within 15-30s).
        count = 0
        for slot in list(slots):
            try:
                async with slot.lock:
                    slot.queue(_list_changed_frame(slot.sep))
                count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "list_change_notifier: queue failed for agent=%s: %s",
                    agent_id, exc,
                )
        logger.info(
            "list_change_notifier: queued list_changed for agent=%s "
            "(%d live streams)", agent_id, count,
        )
