# aMaze Roadmap

Remote agent orchestration platform — secure, policy-enforced, graph-driven.

---

## Phase 1 — Foundation

> Goal: runnable skeleton with all services wired together

- [x] `shared/` package — Pydantic v2 models + SQLAlchemy 2.0 ORM for all entities
  - `AgentDefinition`, `AgentFilesystemMount`
  - `Policy`, `ToolPermission`
  - `Session`, `SessionEvent`
  - `RegistryEntry`
  - `ExecutionGraph`, `ExecutionStep`
- [x] `docker/postgres/init.sql` — full DB schema
- [x] `docker/postgres/seed.sql` — example agents, policies, graphs, MCP tools
- [x] `docker-compose.yml` — all services, Docker networks, volumes
- [x] `.env.example` — all required environment variables documented
- [x] `Makefile` — `make dev`, `make test`, `make lint`, `make build`
- [x] Alembic migration setup in `shared/`

---

## Phase 2 — Core Services

> Goal: registry, policy evaluation, and REST API working

- [x] **Registry** (`services/registry/` :8002)
  - CRUD for `RegistryEntry`
  - `POST /register` — capability self-registration
  - `GET /capabilities` — discovery with filters (type, tag, name)
  - Heartbeat endpoint + health staleness detection
- [x] **Policy Engine** (`services/policy_engine/` :8003)
  - `POST /evaluate` — stateless evaluation of token_budget, loop_limit, tool_allowlist
  - Returns `allow | block | warn` with reason
- [x] **API Gateway** (`services/api_gateway/` :8000)
  - REST: `GET/POST/PUT/DELETE /agents`, `/policies`, `/graphs`, `/sessions`, `/registry`
  - WebSocket: `WS /sessions/{id}/stream` — relays Redis pub/sub to UI
  - OpenAPI docs at `/docs`

---

## Phase 3 — Orchestration & Isolation

> Goal: agents run in real Docker containers with enforced isolation

- [x] **Orchestrator** (`services/orchestrator/` :8001)
  - `POST /sessions` — spawn agent container via Docker SDK
  - Inject `HTTP_PROXY`, `AMAZE_SESSION_ID`, `AMAZE_AGENT_ID` into container env
  - Register `agent_ip:{ip} → {session_id, agent_id}` in Redis
  - Mount only approved paths + per-session `/workspace`
  - Apply seccomp profile, cap_drop ALL, read-only root FS, pids_limit
  - `DELETE /sessions/{id}` — stop and remove container, clean up Redis keys
  - Session workspace cleanup on termination
- [x] **agent_runtime base image** (`agent_runtime/`)
  - `Dockerfile.base` — Python 3.12 slim base
  - `bootstrap.py` — verifies `HTTP_PROXY` is set, configures httpx default headers
  - `amaze_client.py` — registry lookup + agent-to-agent call helper
  - `mcp_client.py` — MCP tool call helper (routes through proxy)
- [x] **mcp_runtime base image** (`mcp_runtime/`)
  - `Dockerfile.base`
  - `bootstrap.py` — startup self-registration via `registry_client.py`
  - `registry_client.py` — `POST /register` to Registry on startup, heartbeat loop

---

## Phase 4 — Proxy & Graph Enforcement

> Goal: all agent traffic policy-checked and graph-validated before it leaves the platform

- [x] **Proxy** (`services/proxy/` :8080) — mitmproxy explicit proxy mode
  - `SessionIdentifier` addon — source IP → session lookup from Redis
  - `RequestClassifier` addon — classify as `llm_call | mcp_call | agent_call`
  - `GraphEnforcer` addon:
    - Read `session:{id}:current_step` from Redis
    - Validate (call_type, callee_id) against `ExecutionStep`
    - Check `session:{id}:step:{step_id}:loops` vs `step.max_loops`
    - Check `session:{id}:step:{step_id}:tokens` vs `step.token_cap`
    - Advance step on success; return HTTP 403/429 on violation
  - `PolicyEnforcer` addon — call Policy Engine, block on violation
  - `TokenCounter` addon — extract `usage` from OpenAI-format response, INCRBY Redis
  - `EventEmitter` addon — PUBLISH to `session:{id}:events`
  - `UpstreamRouter` addon — route to correct upstream (LLM / MCP container / agent container)
- [x] Agent-to-agent routing: proxy → Orchestrator to spawn child session → child container `/invoke`

---

## Phase 5 — UI Dashboard

> Goal: full web interface for managing and monitoring agents

- [ ] React 19 + Vite + TypeScript scaffolding (`services/ui/`)
- [ ] **Agent List page** — CRUD, Docker image config, capability tags
- [ ] **Agent Detail page** — filesystem mounts editor, policy assignment
- [ ] **Policy Editor page** — token budgets, loop limits, tool allowlist toggles
- [ ] **Graph Editor page** (React Flow)
  - Drag `Agent`, `LLM`, `Tool`, `Output` nodes onto canvas
  - Draw directed edges to define step order (auto-numbered)
  - Click edge to configure: `label`, `callee_id`, `max_loops`, `token_cap`
  - Supports branching (multiple outgoing edges from one node)
  - Save → serializes to `ExecutionGraph` JSON via API
- [ ] **Session View page**
  - Start session: pick agent + graph + policy overrides
  - Live graph visualization with active step highlighted (WebSocket)
  - Per-edge loop counter and token usage displayed on edges
  - Full event log (llm_call, mcp_call, policy_violation, graph_violation, output)
  - Token budget meter (global + per-edge)
- [ ] **Registry page** — browse all registered agents and MCP tools, health status badges

---

## Phase 6 — Examples & Seed Data

> Goal: runnable demo out of the box with `docker compose up`

- [ ] **Dummy agents** (`examples/agents/`)
  - `echo` — returns input verbatim, no LLM (routing test)
  - `summarizer` — one LLM call to summarize input
  - `researcher` — LLM → web-search MCP → LLM
  - `reviewer` — filesystem MCP → LLM
- [ ] **Dummy MCP tools** (`examples/mcp/`)
  - `filesystem` — `read_file`, `write_file`, `list_dir` (workspace-scoped)
  - `websearch` — `search` returns hardcoded fake results (offline-safe)
  - `calculator` — `calculate` using safe AST evaluation
  - `counter` — `increment`, `get` (stateful; ideal for testing `max_loops`)
- [ ] Pre-seeded `ExecutionGraph` ("Summarize and Review") in `seed.sql`
- [ ] Pre-seeded policies and agent definitions in `seed.sql`

---

## Future

- **Authentication & multi-tenancy** — API key or OAuth2, workspace isolation per user/org
- **Production hardening** — HashiCorp Vault for secrets, TLS everywhere, non-root containers
- **Async sessions** — long-running agents with webhook callbacks
- **Observability** — OpenTelemetry traces across proxy → services → containers → Jaeger
- **Agent marketplace** — publish and discover community agents via registry
- **Conditional graph branching** — LLM-driven branch selection (agent declares intent via header)
- **Resource quotas** — CPU/memory limits per agent configurable in UI
- **Audit log** — immutable append-only log of all policy decisions and graph transitions
