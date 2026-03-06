# aMaze — Claude Code Instructions

## What is aMaze?

aMaze is a remote agent orchestration platform. Every agent runs in an isolated Docker container with restricted filesystem access. All outbound network traffic (LLM calls, MCP tool calls, agent-to-agent calls) is routed through a central proxy/gateway that enforces per-session policies and execution graphs.

Key security invariant: **agents have no direct internet access**. The proxy is the single egress point.

## Repository Structure

```
aMaze/
├── CLAUDE.md                    # This file
├── ROADMAP.md                   # Phased roadmap
├── docker-compose.yml           # Full dev environment
├── .env.example                 # Environment variable template
├── Makefile                     # Common dev commands
├── shared/                      # Shared Pydantic models + SQLAlchemy ORM
│   └── src/amaze_shared/
│       ├── models/              # agent.py, policy.py, session.py, registry.py, graph.py
│       └── events.py            # Redis pub/sub event schemas
├── services/
│   ├── api_gateway/             # FastAPI :8000 — UI-facing REST + WebSocket
│   ├── orchestrator/            # FastAPI :8001 — Docker container lifecycle
│   ├── proxy/                   # mitmproxy :8080 — policy + graph enforcement
│   ├── registry/                # FastAPI :8002 — capability registration & discovery
│   ├── policy_engine/           # FastAPI :8003 — stateless policy evaluation
│   └── ui/                      # React 19 + Vite :3000 — dashboard
├── agent_runtime/               # Base Docker image all agent containers extend
├── mcp_runtime/                 # Base Docker image all MCP containers extend
├── examples/
│   ├── agents/                  # Dummy agents: echo, summarizer, researcher, reviewer
│   └── mcp/                     # Dummy MCP tools: filesystem, websearch, calculator, counter
└── docker/
    ├── postgres/init.sql        # DB schema
    ├── postgres/seed.sql        # Seed data (example agents, graphs, policies)
    └── agent-seccomp.json       # Seccomp profile for agent containers
```

## Tech Stack

| Component | Technology |
|---|---|
| All backend services | Python 3.12, FastAPI, Pydantic v2 |
| Package manager | **uv** (not pip, not poetry) |
| ORM | SQLAlchemy 2.0 async + Alembic migrations |
| DB driver | asyncpg |
| Redis client | redis-py async |
| HTTP client | httpx async |
| Proxy engine | mitmproxy |
| Container management | docker-py (Docker SDK) |
| Linting | ruff |
| Type checking | mypy |
| Frontend | React 19, TypeScript, Vite |
| UI components | shadcn/ui + Tailwind CSS |
| Graph editor | React Flow |
| State / data fetching | Zustand + TanStack Query |
| Database | PostgreSQL 16 |
| Cache + pub/sub | Redis 7 |

## Running Locally

```bash
cp .env.example .env          # fill in LLM API keys
docker compose up             # starts everything
```

Services:
- UI: http://localhost:3000
- API Gateway: http://localhost:8000
- API docs: http://localhost:8000/docs

## Key Architectural Rules

1. **Never bypass the proxy.** All agent egress goes through `proxy:8080`. Do not add direct LLM or MCP calls from agent containers.

2. **IP-based session mapping.** The proxy identifies which session a request belongs to by container source IP, not headers. The orchestrator registers `agent_ip:{ip} → {session_id, agent_id}` in Redis when it spawns a container.

3. **Graph enforcement is stateful in Redis.** `session:{id}:current_step`, `session:{id}:step:{step_id}:loops`, and `session:{id}:step:{step_id}:tokens` are the authoritative counters. Always use atomic Redis operations (`INCR`, `INCRBY`) to update them.

4. **Policy Engine is stateless.** It receives counters from the proxy and evaluates rules — it does not read Redis or the DB itself.

5. **Shared models are the contract.** All services import from `amaze_shared`. Never duplicate model definitions.

6. **Agent containers are read-only root FS.** Writable paths are only `/tmp` (tmpfs) and explicitly mounted volumes.

7. **ExecutionGraph is optional.** Sessions without a graph still enforce Policy limits. Sessions with a graph enforce both.

## Per-Service Commands

Each service is a uv project. From the service directory:

```bash
uv sync                        # install deps
uv run fastapi dev src/<svc>/main.py   # dev server with hot reload
uv run pytest                  # run tests
uv run ruff check .            # lint
uv run mypy src/               # type check
```

For the UI (`services/ui/`):

```bash
npm install
npm run dev                    # Vite dev server :3000
npm run build                  # production build
npm run lint                   # ESLint
```

## Adding a New Agent (examples/)

1. Create `examples/agents/<name>/Dockerfile` extending `FROM amaze/agent-base:dev`
2. Create `examples/agents/<name>/src/main.py` — FastAPI app exposing `POST /invoke` on port 8090
3. Add the service to `docker-compose.yml` on network `amaze-agent-net`
4. Add a seed entry in `docker/postgres/seed.sql`

## Adding a New MCP Tool (examples/)

1. Create `examples/mcp/<name>/Dockerfile` extending `FROM amaze/mcp-base:dev`
2. Implement the MCP server — it self-registers via `mcp_runtime/registry_client.py` on startup
3. Add to `docker-compose.yml` on network `amaze-mcp-net`

## Redis Key Schema

| Key | Value | Set by |
|---|---|---|
| `agent_ip:{ip}` | `{"session_id": UUID, "agent_id": UUID}` | Orchestrator on container start |
| `session:{id}:policy` | Policy JSON | Orchestrator on session start |
| `session:{id}:graph` | ExecutionGraph JSON | Orchestrator on session start |
| `session:{id}:current_step` | int (step_id) | Proxy on each validated call |
| `session:{id}:step:{step_id}:loops` | int | Proxy, INCR on each call |
| `session:{id}:step:{step_id}:tokens` | int | Proxy, INCRBY on LLM response |
| `session:{id}:tokens_used` | int | Proxy, INCRBY on LLM response |
| `session:{id}:events` | Redis pub/sub channel | Proxy publishes; API Gateway subscribes |
