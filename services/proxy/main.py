"""
mitmproxy addon loader.

Launched by supervisord as:

    mitmdump --listen-host 0.0.0.0 --listen-port 8080 \
             --set confdir=/opt/mitmproxy \
             -s services/proxy/main.py

Addon chain (in order):
  1. SessionIdentity   — bearer → agent_id, strips spoofed x-amaze-caller.
  2. Tracer            — opens OTel span (request) BEFORE PolicyEnforcer so
                         even early denials carry a trace_id in the audit
                         log. Closes + exports the span on response.
  3. PolicyEnforcer    — allowlist checks + per-turn limit pre-checks.
                         Sets amaze_kind/amaze_target/amaze_mcp_tool that
                         downstream addons rely on.
  4. GraphEnforcer     — strict step ordering + atomic loop-limit reservation
                         (request hook); finalize / release reservation
                         (response hook based on 2xx vs non-2xx).
  5. DebugPauser       — per-user step-through gate (S6).
  6. StreamBlocker     — injects "stream": false into LLM request bodies.
  7. LLMToolStripper   — (S9.1) framework-agnostic filter on LLM request
                         tools[] arrays. Closes the gap left by
                         ToolListFilter for runtimes (LangChain/LangGraph
                         today) that cache the tool schema and don't honor
                         notifications/tools/list_changed. Runs adjacent to
                         StreamBlocker because both mutate LLM request bodies.
  8. ToolListFilter    — (S9) intercepts MCP tools/list. Request hook records
                         mcp_pending_list:{sid}:{id} → agent_id (120 s TTL).
                         Response hook rewrites result.tools ∩ allowed_tools
                         across POST-inline / POST-SSE / GET-SSE. Sits before
                         PiiRedactor because it only touches tools/list, which
                         PII doesn't intersect (PII is tools/call only).
  8. PiiRedactor       — per-tool, per-parameter PII redaction on tools/call
                         (S8). See ADR notes above.
  9. Counters          — RTS time-series metrics + per-turn integer counters.
 10. ListChangeNotifier — (S9) startup-only: subscribes to Redis Pub/Sub
                         channel policy:changed. Tracks live MCP GET-SSE
                         flows via responseheaders/error hooks in
                         agent_sse_streams: dict[agent_id, set[HTTPFlow]].
                         On policy:changed with allowed_tools diff, writes a
                         `data: {"jsonrpc":"2.0","method":
                         "notifications/tools/list_changed"}\\n\\n` frame
                         into each matching flow.response.stream, guarded by
                         a per-flow asyncio.Lock and the pinned LF/CRLF
                         separator (same trick as PII SSE).
 11. AuditLog          — XADD one record per call to Redis Streams (with
                         trace_id, alert, indirect, has_tool_calls_input,
                         pii_redacted, tools_filtered).
 12. Router            — resolve logical target name → registered host:port
                         from Redis; rewrite flow.request.host + port before
                         mitmproxy opens the upstream connection. LLM flows
                         are a no-op (forwarded to real provider as-is).

FailClosed wraps every addon: if any `request` coroutine raises, the
wrapper turns the flow into a 403. Without this, mitmproxy passes through
on exception — the fail-open bug we are fixing.
"""
from __future__ import annotations

import logging
import sys
import traceback
from typing import Any

from mitmproxy import http

from services.proxy.audit_log import AuditLog
from services.proxy.counters import Counters
from services.proxy.debug_pauser import DebugPauser
from services.proxy.deny import deny
from services.proxy.enforcer import PolicyEnforcer
from services.proxy.graph_enforcer import GraphEnforcer
from services.proxy.list_change_notifier import ListChangeNotifier
from services.proxy.llm_tool_stripper import LLMToolStripper
from services.proxy.pii_redactor import PiiRedactor
from services.proxy.router import Router
from services.proxy.session import SessionIdentity
from services.proxy.stream_blocker import StreamBlocker
from services.proxy.tool_list_filter import ToolListFilter
from services.proxy.tracer import Tracer

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("amaze.proxy")


class FailClosed:
    """Wraps a single addon so exceptions → 403 instead of pass-through."""

    def __init__(self, inner: Any, name: str) -> None:
        self._inner = inner
        self._name = name

    async def request(self, flow: http.HTTPFlow) -> None:
        if flow.response is not None:
            # A previous wrapped addon already short-circuited. Skip.
            return
        method = getattr(self._inner, "request", None)
        if method is None:
            return
        try:
            await method(flow)
        except Exception:  # noqa: BLE001 — we explicitly fail closed
            logger.error(
                "addon %s raised on request — fail closed\n%s",
                self._name, traceback.format_exc(),
            )
            deny(flow, "internal-error", status=403)

    async def responseheaders(self, flow: http.HTTPFlow) -> None:
        method = getattr(self._inner, "responseheaders", None)
        if method is None:
            return
        try:
            await method(flow)
        except Exception:  # noqa: BLE001
            logger.error(
                "addon %s raised on responseheaders\n%s",
                self._name, traceback.format_exc(),
            )

    async def response(self, flow: http.HTTPFlow) -> None:
        method = getattr(self._inner, "response", None)
        if method is None:
            return
        try:
            await method(flow)
        except Exception:  # noqa: BLE001
            # Response-side failures do NOT deny — the upstream already
            # answered. But they are logged so bugs surface.
            logger.error(
                "addon %s raised on response\n%s",
                self._name, traceback.format_exc(),
            )


addons = [
    # Order matters. SessionIdentity resolves the bearer first so every
    # downstream addon has agent_id + session_id available.
    FailClosed(SessionIdentity(), "session"),
    # Tracer runs BEFORE PolicyEnforcer so that even when the enforcer
    # denies (short-circuiting the chain), the span has already been opened
    # and the audit record can be tagged with the conversation's trace_id.
    # Without this, denied records had empty trace_ids and were invisible
    # in the traces UI when users tried to debug "why did this fail?".
    FailClosed(Tracer(), "tracer"),
    FailClosed(PolicyEnforcer(), "enforcer"),
    FailClosed(GraphEnforcer(), "graph"),
    FailClosed(DebugPauser(), "debug_pauser"),
    FailClosed(StreamBlocker(), "stream_blocker"),
    # LLMToolStripper (S9.1) mutates outbound LLM request bodies: filters
    # tools[] against policy.allowed_tools regardless of what the agent
    # runtime thinks its tool schema is. Complements ToolListFilter for
    # runtimes that cache the tool set (LangChain/LangGraph today).
    # Fail-closed on Redis via 503, same as the other enforcement addons.
    FailClosed(LLMToolStripper(), "llm_tool_stripper"),
    # ToolListFilter (S9) sits before PiiRedactor. It only touches
    # `tools/list` responses (POST-inline / POST-SSE / GET-SSE frames);
    # PII only touches `tools/call`. They don't intersect. Placing the
    # filter first keeps disallowed tool descriptions out of any
    # downstream state (audit records, PII cache) unconditionally.
    FailClosed(ToolListFilter(), "tool_list_filter"),
    # PiiRedactor sits BEFORE AuditLog for two reasons:
    # (1) request hook: input redaction happens before AuditLog stores the
    #     pending Redis key, so PII never lands in mcp_pending:*.
    # (2) responseheaders: for POST-SSE tool responses PiiRedactor sets its
    #     own flow.response.stream and marks amaze_pii_owned_stream=True.
    #     AuditLog's responseheaders (which runs next) sees the flag and
    #     leaves the stream alone; PiiRedactor writes the audit record
    #     itself from inside the stream handler.
    # For buffered responses PiiRedactor just mutates flow.response.content
    # and sets amaze_pii_redacted=True; AuditLog writes the record normally
    # with the already-redacted content.
    FailClosed(PiiRedactor(), "pii_redactor"),
    FailClosed(Counters(), "counters"),
    FailClosed(AuditLog(), "audit_log"),
    # ListChangeNotifier (S9) runs AFTER AuditLog because AuditLog's
    # `responseheaders` sets `flow.response.stream` for MCP GET-SSE; the
    # notifier wraps that handler so both keep working. Subscribes to
    # Redis Pub/Sub channel `policy:changed` on the `running` hook,
    # queues `notifications/tools/list_changed` frames into live GET-SSE
    # streams when `allowed_tools` diffs. Delivery latency is bounded by
    # the MCP server's SSE keepalive cadence (typically 15-30 s) — see
    # ADR-001 §"Delivery model".
    #
    # Registered UNWRAPPED: FailClosed proxies only request/responseheaders/
    # response, not `load`/`running`/`done`. The notifier needs `running`
    # to start its Pub/Sub background task. Not being on the deny path is
    # fine — its responseheaders only annotates flow.response.stream; a
    # raise would just skip injection for that flow (no security impact).
    ListChangeNotifier(),
    # Router always runs last — after all enforcement and audit. If any
    # earlier addon denied the request, the FailClosed guard above skips
    # Router. The audit record therefore always records the logical name
    # (e.g. "agent-sdk1"), never the resolved IP.
    FailClosed(Router(), "router"),
]
