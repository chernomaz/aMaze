# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9] - 2026-07-17

Per-agent MCP tool visibility with live cache invalidation. Two new proxy
addons and a small orchestrator publisher: agents now see only tool
descriptions their policy allows, and live sessions are pushed a
`notifications/tools/list_changed` frame when `allowed_tools` is edited.
See `docs/adr/001-mcp-tool-visibility.md`.

### Added

- **`ToolListFilter` proxy addon** (`services/proxy/tool_list_filter.py`).
  Request hook records `mcp_pending_list:{sid}:{jsonrpc_id}` → `agent_id`
  (120 s TTL) on every MCP `tools/list` request. Response hook correlates
  the reply — across POST-inline JSON, POST-SSE frames, and GET-SSE frames
  — and rewrites `result.tools` down to `result.tools ∩
  policy.allowed_tools`. Runs between `stream_blocker` and `pii_redactor`
  in the chain. Fail-closed on Redis error during response filtering
  (503 `redis-unavailable`, matching enforcer convention).
- **`ListChangeNotifier` proxy addon**
  (`services/proxy/list_change_notifier.py`). Startup task subscribes to
  Redis Pub/Sub channel `policy:changed`; tracks live MCP GET-SSE flows in
  an in-process `agent_sse_streams` registry populated in
  `responseheaders` and cleared via weakref on flow end. On a message
  whose `changed_fields` includes `allowed_tools`, queues a
  `data: {"jsonrpc":"2.0","method":"notifications/tools/list_changed"}\n\n`
  frame into each affected flow. Delivery is coupled to the MCP server's
  SSE keepalive cadence (typically 15-30 s) — trade-off documented in
  ADR-001 vs the forced-reconnect alternative. Runs after `AuditLog` in
  the chain so it wraps AuditLog's existing GET-SSE stream handler
  instead of overriding it.
- **Orchestrator publisher.** `PUT /policy/{agent_id}` now diffs old vs
  new and `PUBLISH`es `{"agent_id", "changed_fields"}` on `policy:changed`
  when any field differs. Publish is best-effort — Redis errors do not
  fail the PUT (the policy is already persisted; the per-request refetch
  picks it up). Response body gains a `changed_fields` array.
- **`GET /policy/{agent_id}/tool-visibility?server=<name>` endpoint.**
  Orchestrator calls the MCP server via the existing `mcp_probe` helper,
  applies the same `allowed_tools` filter, returns
  `{server, visible: MCPTool[], hidden: MCPTool[]}`. Owner-gated. Powers
  the AgentPolicy Tool Visibility panel and the ST-TV.7 system test.
- **Tool Visibility preview panel** (`services/ui/src/pages/AgentPolicy.tsx`).
  New card in flexible mode below Allowed Capabilities. MCP server
  dropdown; two live-updating columns (visible / hidden) driven from the
  `mcp_servers` React Query cache and the draft's `allowed_tools`. No
  extra round-trip per toggle.
- **"N tools hidden" chip** on tools/list spans in the Trace Detail table
  (`services/ui/src/pages/TraceDetail.tsx`). Backed by a new
  `tools_filtered` audit field ("N/M" — before/after) written by
  `AuditLog` from `flow.metadata["amaze_tool_list_filtered"]`.
- **System tests** (`tests/test_s9_tool_visibility.py`). Endpoint slice
  of ST-TV.*: ST-TV.1 filter applies, ST-TV.2 no-policy pass-through,
  ST-TV.4 no-op PUT reports empty `changed_fields`, ST-TV.6 rapid
  successive PUTs leave persisted state matching the final write,
  ST-TV.7 endpoint shape + 404 on unknown server, ST-TV.8 cross-user
  403. Real stack, no mocks.
- **`LLMToolStripper` proxy addon**
  (`services/proxy/llm_tool_stripper.py`). Framework-agnostic backstop at
  the LLM boundary: parses the `tools[]` array on every outbound
  chat-completion / messages request and drops entries not in
  `policy.allowed_tools`, patching `tool_choice` when it pinned a stripped
  tool. Closes the gap left by `ToolListFilter` for runtimes that cached
  their tool schema before the policy tightened — every LangChain /
  LangGraph agent has this shape, because `create_agent(tools=...)` bakes
  the binding at construction. Wire formats live in a `_PROVIDER_SHAPES`
  registry keyed by canonical provider name (`openai`, `anthropic`
  today); adding a provider is one entry, not a new filter function.
  Runs adjacent to `StreamBlocker` since both mutate LLM request bodies.
  Fail-closed on Redis error (503 `redis-unavailable`).
- **Push-based tool-set signalling in the SDK.** `PUT /policy/{agent_id}`
  now assembles `{allowed_tools: [{name, description, inputSchema}, ...]}`
  from the approved `mcp:{name}` schema caches and POSTs it to the
  agent's `/_amaze/tools_changed` endpoint (mounted on both the chat and
  A2A apps, so A2A-only agents are covered). Authenticated by bearer echo
  — the orchestrator replays the agent's own token, which only it and the
  agent hold. Best-effort: a failed push never fails the PUT, because
  `ToolListFilter` and `LLMToolStripper` still enforce at the wire.
- **`amaze.is_tools_changed()` / `amaze.current_tools()`.** Two sync
  functions (threading-locked, callable from sync or async handlers) that
  surface the pushed payload to author code. `is_tools_changed()` is an
  atomic read-and-clear; `current_tools()` returns a defensive copy of
  the current authoritative set with schemas. The SDK holds no opinion
  about what happens next — the author decides whether to rebuild their
  agent, swap a filter set behind LangChain `wrap_model_call` middleware,
  or ignore the signal entirely. Deliberately *not* a callback: reusing
  `on_startup` as an implicit rebuild hook would silently re-run
  one-shot author code.
- **`GET /agents/self/allowed-tools`.** Agent-bearer-authenticated
  self-lookup returning the caller's current `allowed_tools`. Resolves
  `session_token:{token}` → agent_id the same way the proxy does.
  Available as a poll-based fallback when a push is missed.
- **`agent:{agent_id}:bearer_token` Redis key**, written at registration
  so the orchestrator can echo the agent's bearer on the outbound push.

### Changed

- **`services/proxy/main.py` addon chain.** Three new addons inserted;
  chain-order docstring updated. Chain is now
  `session → tracer → enforcer → graph → debug_pauser → stream_blocker →
  llm_tool_stripper → tool_list_filter → pii_redactor → counters →
  audit_log → list_change_notifier → router`. `ListChangeNotifier` is
  registered UNWRAPPED (not inside `FailClosed`) because the wrapper does
  not forward `load`/`running`/`done` hooks; it needs `running` to start
  its Pub/Sub task. Its `responseheaders` is not on the deny path, so the
  missing wrapper has no security impact. Forwarding lifecycle hooks
  through `FailClosed` is filed as follow-up work — the same gap
  currently prevents `PiiRedactor.load()` from firing, so the spaCy
  preload it was meant to do happens lazily on first request instead.
- **`services/proxy/audit_log.py`.** MCP audit records now carry a
  `tools_filtered` field ("N/M") on tools/list spans, and LLM records
  carry `llm_tools_stripped` ("N/M") when the stripper reduced the
  outbound tool array. Both absent on records where they don't apply.
- **Trace detail UI.** Two chips: cyan "N tools hidden" on `tools/list`
  spans (MCP-boundary filtering), amber "N tools stripped" on LLM spans
  (LLM-boundary stripping). The colour split is deliberate — hidden means
  the filter worked at build time, stripped means the runtime's cache was
  stale and the wire-level backstop caught it.
- **`PUT /policy/{agent_id}` response body.** Now includes
  `"changed_fields": [...]` reporting which top-level `Policy` fields
  diverged from the previous value. Empty on no-op writes.
- **Demo agents** (`examples/agents/agent_sdk{,1,2,3}.py`) check
  `amaze.is_tools_changed()` at the top of their message handlers and
  rebuild via their existing `_build_agent()`. ~5 lines each; the rebuild
  pattern was chosen over LangChain `wrap_model_call` middleware for the
  canonical example because it works for any framework.
- **`SDK version`.** `sdk/pyproject.toml` bumped to `0.9.0`.

### Fixed

- **`ListChangeNotifier` crashed on every MCP GET-SSE flow.** `_FlowSlot`
  declared `__slots__` without `__weakref__`, so adding a slot to the
  `WeakSet` registry raised `TypeError: cannot create weak reference`.
  The registry stayed empty and no notification could ever be delivered.

### Notes

- The addon adopts the discovery pass-through invariant already
  established by `enforcer.py`: an agent with no policy row sees the
  full tool list.
- **Three enforcement layers, deliberately redundant.** `ToolListFilter`
  keeps disallowed descriptions out of the agent at MCP discovery time;
  `LLMToolStripper` strips them from the wire if the runtime's cache went
  stale; `PolicyEnforcer` still denies the `tools/call` itself. The SDK
  push is a UX layer on top — it lets a cooperating agent converge fast,
  but an agent that ignores it is still correctly constrained.
- **`notifications/tools/list_changed` delivery is best-effort.**
  mitmproxy's stream handler is a per-upstream-chunk callback and cannot
  push independently, so an injected frame only reaches the agent when
  the MCP server next writes to the SSE stream. Servers with sparse
  keepalives may never flush it. This is why the SDK push exists — the
  MCP frame is kept for non-SDK clients that honour it, but it is not the
  mechanism the demo agents rely on. Full detail in ADR-001
  §"Delivery model".
- **`langchain-mcp-adapters` has no `list_changed` callback slot.** Its
  `Callbacks` dataclass exposes only `on_logging_message`, `on_progress`
  and `on_elicitation`, and `create_agent()` binds tools at construction
  — so even a perfectly delivered MCP notification has nowhere to go in a
  LangChain runtime. This is the concrete reason the design landed on a
  push to the SDK plus an author-controlled reaction, rather than relying
  on the MCP-native path.
- Traffic-level ST-TV.3 (mid-session notification within 500 ms) and
  ST-TV.5 (Redis-down fail-closed observed through the real proxy) need
  a live-agent + mock-MCP harness with a way to kill Redis mid-flight;
  deferred to a follow-up harness, mirroring the S8 endpoint-slice split.

## [0.8] - 2026-07-11

Per-tool PII redaction for MCP tool calls: a new proxy addon that redacts
configurable entity types out of tool inputs and outputs — powered by
Microsoft Presidio + spaCy NER (`en_core_web_lg`), with a regex-only
fallback for low-footprint deployments.

### Added

- **PII redaction addon.** New `PiiRedactor` proxy addon between
  `stream_blocker` and `counters`. Redacts per-parameter PII on MCP
  `tools/call` request bodies before they leave the proxy; redacts result
  bodies (both buffered JSON and streamed SSE — POST-inline and GET-async)
  before they reach the agent. Covered entities: `EMAIL_ADDRESS`,
  `CREDIT_CARD`, `PHONE_NUMBER`, `US_SSN`, `IP_ADDRESS`, `PERSON`, `LOCATION`,
  `URL`, `IBAN_CODE`. Presidio errors do not deny — the affected field is
  replaced with `<PII_REDACTION_ERROR>` and the tool call proceeds.
- **spaCy NER backend** (default). `PII_NLP_MODE=ner` uses Presidio's
  `AnalyzerEngine` backed by spaCy `en_core_web_lg`. Reliably catches
  single-token proper nouns (Nashville, Portland, Boston) and disambiguates
  PERSON vs LOCATION by context. Custom recognizers stay for US_SSN and
  structured entities. Image grows ~1.5 GB to bake in the model.
  `PII_NLP_MODE=regex` keeps the small-image, regex-only path for
  low-footprint deployments.
- **JSON-in-string parsing.** When a string leaf is itself a serialized JSON
  document (e.g. the `text` field of an MCP tool result whose tool returns
  pure JSON), `redact_json_text_fields` parses it, walks the parsed
  structure, and re-serializes so the analyzer sees bare values —
  `"Grace Hall"` not `"{\"name\":\"Grace Hall\"}"`. Per-leaf analyzer calls
  give spaCy the isolation it needs for reliable NER; trades ~2-3 s on 30-row
  responses for consistent name-recall accuracy.
- **Policy schema.** `Policy` gains a `pii_config` sub-object of shape
  `{enabled, tools: {tool_name: {input: {param: {entities}}, output: {entities}}}}`.
  Absent or `null` = no redaction (fully backwards compatible with older
  policies). Unknown entity labels are rejected at model-validation time.
- **Orchestrator endpoints.** `GET /policy/{agent_id}/pii`,
  `PUT /policy/{agent_id}/pii` (surgical sub-object write; other Policy fields
  preserved), and `POST /policy/{agent_id}/pii/preview` (stateless dry-run
  that feeds the UI preview panel). All owner-gated via `require_agent_owner`.
- **PII Redaction UI tab.** New `AgentPiiRedaction` page slotted between
  Policy and Debugger. Lists tools this agent is authorized to use (union of
  `allowed_tools` and graph tool steps), pulls parameter names + types from
  the MCP server's `inputSchema`, and exposes a pill-picker per parameter and
  per response-body entity list. Non-string parameters are visibly disabled.
  A live "Redaction preview" panel calls the `/preview` endpoint. Sticky
  Save/Reset footer with dirty-state tracking.
- **Trace log.** Every audit record now carries a `pii_redacted` field, and
  the trace-detail table shows a small "PII redacted" chip next to tool
  spans where it's true.
- **AuditLog coordination.** `AuditLog` gains a `responseheaders`
  short-circuit on `flow.metadata["amaze_pii_owned_stream"]` — when
  PiiRedactor takes over the response-side stream (POST inline SSE with an
  output rule) AuditLog leaves `flow.response.stream` untouched and
  PiiRedactor writes the audit record directly. A new in-process cache
  `_pii_entities_cache`, mirrored from the pending Redis payload, lets the
  GET-SSE stream handler redact frames synchronously without a blocking
  Redis GET on the event-loop thread. Cache has a TTL sweep bound to
  `MCP_PENDING_TTL` (120 s) to prevent unbounded growth on non-happy
  termination paths.
- **Eager spaCy preload** at proxy startup — first request no longer blocks
  ~3-5 s on model init.
- **Recursion depth cap** (`_MAX_JSON_DEPTH = 32`) and **64 KB parse cap**
  on the JSON walk — bounds stack size and worst-case parse cost against
  hostile / misbehaving MCP servers.
- **Nested-argument walking.** Input redaction recurses into dict/list-
  shaped argument values via `redact_json_text_fields`, not just top-level
  string params. A payload like `{contact: {email: "..."}}` is now covered.
- **Canonical `safe_redact_json` helper.** Single shape-preserving wrapper
  used by both `audit_log` and `pii_redactor` — same failure semantic in
  both places (return input unchanged on error, log at WARNING). Preserves
  dict/list shape so callers doing `json.dumps(that)` don't get their tool
  response silently rewritten to a bare string on a Presidio exception.
- **Honest `pii_redacted` audit field.** Set to `"true"` only when an actual
  span was replaced, not merely when a rule was configured. Aligns the
  trace-detail "PII redacted" chip with what it says.
- **Pinned SSE frame separator** on first sighting in both PiiRedactor's
  POST-SSE handler and AuditLog's GET-SSE handler — long-lived streams that
  switch between LF and CRLF mid-stream no longer glue frames together.
- **Demo enhancements.** `sql_query` tool now returns pure JSON (dropped
  the `"N row(s):\n"` prefix); demo `users` table gains `phone` (US area
  codes in 5 realistic formats) and Luhn-valid `credit_card` columns. Two
  more entities to exercise the redactor end-to-end.

### Dependencies

- Added `presidio-analyzer>=2.2,<3` and `spacy>=3.8,<4`.
  `presidio-anonymizer` was deliberately NOT added — its `cryptography>=46`
  constraint conflicts with `mitmproxy 11`'s `cryptography<44.1`. Text
  replacement is done inline in `pii_engine`, which also gives us direct
  control over the `<ENTITY_LABEL>` placeholder format.
- Docker image adds `python -m spacy download en_core_web_lg`
  (~600 MB model). Overridable via `PII_SPACY_MODEL` env var.
- `docker-compose.yml` sets `PII_NLP_MODE: ner` on the `amaze` service.

### Notes

- LLM chat bodies and A2A traffic are out of scope for this release; the
  redactor bypasses non-MCP flows.
- Presidio's built-in `UsSsnRecognizer` needs surrounding-word context;
  in NER mode it is removed from the registry and replaced by our custom
  standalone SSN regex.

[0.9]: https://github.com/chernomaz/aMazeControlPlane/releases/tag/v0.9
[0.8]: https://github.com/chernomaz/aMazeControlPlane/releases/tag/v0.8

## [0.7] - 2026-06-29

Multi-user access control: real session auth, per-user agent ownership, and an
admin user-management console.

### Added

- **Session authentication.** Cookie-based login/logout (`POST /auth/login`,
  `/auth/logout`, `GET /auth/me`) with bcrypt-hashed credentials and opaque,
  TTL-refreshed session tokens in Redis. All control-plane routes are now
  gated; the SDK/proxy agent-bearer path is untouched — human identity and
  agent identity stay separate seams. `auth.py` is the single OIDC-swap point.
- **Per-user agent ownership.** Every agent has exactly one owner. A user
  *claims* an agent id before it registers; on self-registration ownership
  binds to the claimer automatically. Unclaimed registrations are quarantined
  (owner-less) until an admin adopts them. Policy, debugger, messaging, stats,
  and audit routes are owner-gated (admins bypass).
- **Admin user-management console (Users tab).** Admins create accounts with a
  role (`admin` or regular), list and delete them, and assign or reassign any
  agent to a single owner — `GET/POST /auth/users`, `DELETE /auth/users/{id}`,
  `POST /agents/{id}/assign`. Regular users see only the agents they own;
  admins see all.
- **Default bootstrap admin** `admin` / `admin`, configurable via
  `AMAZE_ADMIN_USER` / `AMAZE_ADMIN_PASSWORD`.
- Full-stack system tests: `tests/test_s7_auth.py`, `test_s7_ownership.py`,
  `test_s7_debug_authz.py`, and `test_s8_users.py` (admin-only user CRUD,
  single-owner reassignment, agent-orphaned-on-delete) — real stack, no mocks.

### Fixed

- **Stale agent list after switching users.** The dashboard kept the previous
  session's React Query cache across login/logout, so a newly logged-in user
  saw the prior user's agents until a manual browser refresh. Login and logout
  now clear the query cache (and prime `me` from the login response).

[0.7]: https://github.com/chernomaz/aMazeControlPlane/releases/tag/v0.7

## [0.6.0] - 2026-06-27

First tagged release. Headlines the multi-user live debugger.

### Added

- **Multi-user live debugger.** Multiple users can step through the **same**
  agent concurrently and independently — each browser sees only its own paused
  steps, advances only its own queue, and never affects anyone else's session.
  A per-browser debug-user id propagates from the UI through the agent runtime
  onto every outbound call, so the proxy parks each intercepted step under that
  user's namespace (`debug:{agent}:{user}:*`). Untagged traffic is never parked.
- Full-stack isolation tests (`tests/test_s6_debug_multiuser.py`, ST-MU.1–5):
  independent queues, independent Next, per-user dead-man's-switch expiry, A2A
  peer attribution, and untagged-bypass — run against the real stack, no mocks.

### Fixed

- **UI `apiFetch` dropped `Content-Type` when a caller passed custom headers**,
  so JSON requests (enable-debug `PUT`, send-message `POST`) went out as
  `text/plain` and were rejected with `422`. Header merge order corrected, so
  every caller keeps `application/json`.
- `index.html` is now served with `Cache-Control: no-cache` (hashed assets stay
  `immutable`), so UI redeploys are picked up without a manual hard refresh.

### Changed

- demo-mcp persists its MySQL data across restarts (named volume + idempotent
  init/seed guard) — the database is initialized and seeded only on first boot;
  later restarts come up fast.

[0.6.0]: https://github.com/chernomaz/aMazeControlPlane/releases/tag/v0.6.0
</content>
