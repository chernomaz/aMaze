# ADR-001: Per-agent MCP tool visibility with live cache invalidation

**Status:** Accepted
**Date:** 2026-07-17
**Deciders:** @chernomaz
**Sprint:** S9 (v0.9)

## Context

The proxy today filters at *call* time (`tools/call` → `tool-not-allowed`)
but every agent sees the *full* `tools/list` from upstream MCP servers.
Three consequences:

1. **Prompt-injection surface.** Tool descriptions the agent isn't
   authorised for still enter the model's context. A crafted description
   of a disallowed tool can influence the model's plan even though the
   call itself would be blocked at `tools/call`.
2. **Wasted tokens and noisy audit.** The planner LLM keeps proposing
   disallowed tools; each attempt is a round-trip and a `tool-not-allowed`
   denial row.
3. **No live policy signal.** When an admin edits `allowed_tools` via
   `PUT /policy/{agent_id}`, the change takes effect for the *next*
   `tools/call` (proxy refetches per request — CLAUDE.md §1) but the
   agent's cached tool schema is stale until it restarts. There is no
   mechanism to push a re-list.

The MCP transport is streamable-HTTP: POST bodies can return inline JSON
or SSE; a separate long-lived GET-SSE per session carries server-pushed
messages. The spec defines `notifications/tools/list_changed` for exactly
this "please re-list" flow — but only the *server* can send it, and today
our proxy is a passthrough for that channel.

Constraints:

- Two-container topology (`amaze-platform` + `amaze-redis`); no Postgres.
- Existing addon chain order and stream-ownership contracts (PII
  redactor, audit_log, tracer) must not shift — see
  `services/proxy/audit_log.py` and CHANGELOG §0.8.
- Fail-closed convention on Redis errors (`services/proxy/enforcer.py`
  around line 88).
- Must ship as one Sprint slice, demo-first (CLAUDE.md §12).

## Decision

Add **two new proxy addons** and a **short publisher in the orchestrator's
policy write path**.

1. **`tool_list_filter`** — request/response addon that correlates
   `tools/list` requests by `(mcp_session_id, jsonrpc_id)` in Redis
   (`mcp_pending_list:{sid}:{id}`, 120 s TTL), and on the response
   rewrites `result.tools` down to the intersection with
   `policy.allowed_tools`. Handles POST-inline, POST-SSE, and GET-SSE
   response shapes by reusing frame helpers already in
   `services/proxy/audit_log.py`.
2. **`list_change_notifier`** — startup addon that `SUBSCRIBE`s to a new
   Redis Pub/Sub channel `policy:changed`; maintains an in-process
   registry `agent_sse_streams: dict[str, set[HTTPFlow]]` of live MCP
   GET-SSE flows; on message, writes
   `data: {"jsonrpc":"2.0","method":"notifications/tools/list_changed"}\n\n`
   (respecting the flow's pinned LF/CRLF separator) into each affected
   stream.
3. **Orchestrator publisher** — `PUT /policy/{agent_id}` diffs old vs new
   and `PUBLISH`es `{agent_id, changed_fields}` on `policy:changed` only
   if `allowed_tools` changed.

No new datastore. No external API surface change. Rollout in two phases:
`tool_list_filter` alone (Phase 1, safe by itself), then
`list_change_notifier` + publisher (Phase 2) bundled into the same S9
release per user direction.

## Options considered

### Option A: Filter at the proxy, notify via Pub/Sub + SSE frame injection *(chosen)*

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium — two small addons; reuses existing SSE frame helpers |
| Cost | ~1 Redis GET on `tools/list` (rare); 1 Pub/Sub broadcast per policy edit |
| Scalability | Trivial (list-scan on discovery methods only); per-proxy memory ~MB at 10k agents |
| Team familiarity | High — same shape as PII redactor addon shipped in v0.8 |

**Pros**

- Proxy already holds the flow, the agent identity, and the policy —
  zero extra hops.
- SSE frame injection is native to MCP; agent code (LangChain) already
  handles `list_changed`.
- Multi-proxy safe: every proxy sees the Pub/Sub message; only the one
  holding the flow acts.

**Cons**

- Second addon owns a background asyncio task (Pub/Sub consumer +
  reconnect loop).
- Frame-write races with upstream writes on the same stream → needs a
  per-flow `asyncio.Lock`.

### Option B: Filter in the orchestrator, cache filtered lists in Redis

**Rejected.** Duplicates the proxy's Redis/policy read path; introduces a
staleness window (cache TTL vs live policy); doesn't help with Problem B
(the "invalidate agent-side cache" leg) at all — you still need a push
channel to the agent. Solves the smaller half of the problem with more
moving parts.

### Option C: Force a proxy-initiated SSE reconnect instead of frame injection

**Rejected.** Works but is heavier: full MCP session teardown + reinit +
re-list on every policy edit. Interrupts any in-flight tool call.
`notifications/tools/list_changed` is the exact primitive the MCP spec
defined so that we don't have to reconnect.

### Option D: Redis Streams instead of Pub/Sub for `policy:changed`

**Rejected.** Replay is anti-value here — a proxy that missed a change
during downtime will refetch the policy from Redis on its next request
anyway (CLAUDE.md §1); the notification is only useful to a *live*
stream, and a live stream by definition has a live proxy. Streams add
consumer-group state, ack semantics, and backlog management for zero
payoff.

### Option E: Fail-open on Redis error during list filter

**Rejected.** Contradicts §9 of CLAUDE.md ("Redis unavailable → DENY
503"). An attacker who can DoS Redis would see the full tool surface —
the exact hole this ADR closes.

## Trade-off analysis

The central tension is **where to filter** and **how to invalidate**.
Filtering in the proxy wins on locality — everything the filter needs
(identity, policy, flow) is already in the addon's hands, and the cost
is bounded to a rare method. Invalidating via Pub/Sub + frame injection
wins on protocol fidelity — MCP's own spec expects this exact push.

The main cost is frame-write concurrency in `list_change_notifier`.
Managed with a per-flow `asyncio.Lock` and the LF/CRLF pinning we already
implemented for PII SSE. Nothing else in the design is novel to this
codebase.

## Consequences

**Easier**

- Agents stop being tempted by disallowed tools — cleaner planner
  behaviour, cleaner audit.
- Policy edits become live for the agent, not just for the proxy — closes
  the "why is the agent still trying `dangerous_tool`?" surprise.
- Same pattern extends to `resources/list` / `prompts/list` / A2A tool
  visibility with almost no new code.

**Harder**

- One more addon in the chain to reason about during PII/tracer/debugger
  changes. Chain-order comment at top of `services/proxy/main.py` must
  be updated.
- Debugger's SSE recording (`services/proxy/debug_pauser.py` around
  line 274) needs to know that proxy-injected `list_changed` frames
  didn't originate upstream — either whitelisted out or tagged.

**To revisit**

- If a runtime cache lives *above* the MCP client (e.g. LangChain
  persists the tool schema across sessions), we may need runtime-side
  plumbing. Out of scope for S9.
- Extending to per-tool description *rewriting* (redact/truncate rather
  than binary hide) is a straightforward extension of the same addon,
  driven by a new `tool_descriptions` policy field.
- `resources/list` and `prompts/list` need matching call-time
  enforcement first — deferred until we have an MCP server exposing
  those surfaces.

## Action items

1. [ ] T9-2: implement `tool_list_filter` addon request hook.
2. [ ] T9-3: implement `list_change_notifier` skeleton + Pub/Sub startup.
3. [ ] T9-4: orchestrator diff + PUBLISH on `PUT /policy/{agent_id}`.
4. [ ] T9-5: `tool_list_filter` response hook across three SSE shapes.
5. [ ] T9-6: `list_change_notifier` frame injection.
6. [ ] T9-7: register both addons in `main.py`.
7. [ ] T9-8: audit surfaces `amaze_tool_list_filtered`.
8. [ ] T9-9: `debug_pauser` tags injected frames.
9. [ ] T9-10: `GET /policy/{id}/tool-visibility` endpoint.
10. [ ] T9-11/12/13: UI panel + trace chip.
11. [ ] T9-14: ST-TV.1–8 system tests.
12. [ ] T9-15: CHANGELOG v0.9 + `/code-reviewer` pass.
