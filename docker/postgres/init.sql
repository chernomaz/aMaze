-- aMaze platform schema
-- Runs automatically when PostgreSQL container starts for the first time.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─── Policies ────────────────────────────────────────────────────────────────

CREATE TABLE policies (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                        VARCHAR(255) NOT NULL UNIQUE,
    description                 TEXT NOT NULL DEFAULT '',
    max_tokens_per_conversation  INTEGER NOT NULL DEFAULT 100000,
    max_tokens_per_turn         INTEGER NOT NULL DEFAULT 10000,
    max_iterations              INTEGER NOT NULL DEFAULT 20,
    max_agent_calls             INTEGER NOT NULL DEFAULT 10,
    max_mcp_calls               INTEGER NOT NULL DEFAULT 50,
    allowed_tools               JSONB NOT NULL DEFAULT '[]',
    allowed_llm_providers       TEXT[] NOT NULL DEFAULT '{}',
    allowed_mcp_servers         TEXT[] NOT NULL DEFAULT '{}',
    on_budget_exceeded          VARCHAR(10) NOT NULL DEFAULT 'block',
    on_loop_exceeded            VARCHAR(10) NOT NULL DEFAULT 'block',
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── Execution Graphs ────────────────────────────────────────────────────────

CREATE TABLE execution_graphs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL UNIQUE,
    description     TEXT NOT NULL DEFAULT '',
    start_step_id   INTEGER NOT NULL,
    on_violation    VARCHAR(10) NOT NULL DEFAULT 'block',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE execution_steps (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    graph_id        UUID NOT NULL REFERENCES execution_graphs(id) ON DELETE CASCADE,
    step_id         INTEGER NOT NULL,               -- logical step number within graph
    label           VARCHAR(255) NOT NULL DEFAULT '',
    call_type       VARCHAR(20) NOT NULL,           -- llm_call | mcp_call | agent_call | terminal
    callee_id       VARCHAR(255),                   -- specific tool/agent/provider; NULL = any
    next_step_ids   INTEGER[] NOT NULL DEFAULT '{}', -- empty = terminal step
    max_loops       INTEGER NOT NULL DEFAULT 1,
    token_cap       INTEGER,                        -- NULL = no cap
    UNIQUE (graph_id, step_id)
);

CREATE INDEX idx_execution_steps_graph_id ON execution_steps(graph_id);

-- ─── Agents ──────────────────────────────────────────────────────────────────

CREATE TABLE agents (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    VARCHAR(255) NOT NULL UNIQUE,
    description             TEXT NOT NULL DEFAULT '',
    image                   VARCHAR(500) NOT NULL,
    version                 VARCHAR(50) NOT NULL DEFAULT 'latest',
    capabilities            TEXT[] NOT NULL DEFAULT '{}',
    required_capabilities   TEXT[] NOT NULL DEFAULT '{}',
    env_vars                JSONB NOT NULL DEFAULT '{}',
    secret_refs             TEXT[] NOT NULL DEFAULT '{}',
    status                  VARCHAR(20) NOT NULL DEFAULT 'draft',
    policy_id               UUID REFERENCES policies(id) ON DELETE SET NULL,
    mem_limit               VARCHAR(20) NOT NULL DEFAULT '2g',
    cpu_quota               INTEGER NOT NULL DEFAULT 100000,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_mounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    host_path       VARCHAR(1000) NOT NULL,
    container_path  VARCHAR(1000) NOT NULL,
    read_only       BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX idx_agent_mounts_agent_id ON agent_mounts(agent_id);

-- ─── Registry ────────────────────────────────────────────────────────────────

CREATE TABLE registry_entries (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                VARCHAR(255) NOT NULL UNIQUE,
    capability_type     VARCHAR(20) NOT NULL,   -- agent | mcp_tool | mcp_server
    version             VARCHAR(50) NOT NULL DEFAULT '1.0.0',
    description         TEXT NOT NULL DEFAULT '',
    internal_host       VARCHAR(255) NOT NULL,
    internal_port       INTEGER NOT NULL,
    input_schema        JSONB,
    output_schema       JSONB,
    tags                TEXT[] NOT NULL DEFAULT '{}',
    is_healthy          BOOLEAN NOT NULL DEFAULT true,
    last_heartbeat      TIMESTAMPTZ NOT NULL DEFAULT now(),
    registered_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_agent_id      UUID REFERENCES agents(id) ON DELETE SET NULL
);

CREATE INDEX idx_registry_entries_capability_type ON registry_entries(capability_type);
CREATE INDEX idx_registry_entries_is_healthy ON registry_entries(is_healthy);

-- ─── Sessions ────────────────────────────────────────────────────────────────

CREATE TABLE sessions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id                UUID NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    policy_id               UUID NOT NULL REFERENCES policies(id) ON DELETE RESTRICT,
    execution_graph_id      UUID REFERENCES execution_graphs(id) ON DELETE SET NULL,
    container_id            VARCHAR(100),
    container_name          VARCHAR(255),
    status                  VARCHAR(20) NOT NULL DEFAULT 'pending',
    initial_prompt          TEXT NOT NULL DEFAULT '',
    final_output            TEXT,
    tokens_used             INTEGER NOT NULL DEFAULT 0,
    iterations_completed    INTEGER NOT NULL DEFAULT 0,
    mcp_calls_made          INTEGER NOT NULL DEFAULT 0,
    agent_calls_made        INTEGER NOT NULL DEFAULT 0,
    started_at              TIMESTAMPTZ,
    completed_at            TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sessions_agent_id ON sessions(agent_id);
CREATE INDEX idx_sessions_status ON sessions(status);
CREATE INDEX idx_sessions_created_at ON sessions(created_at DESC);

CREATE TABLE session_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    event_type      VARCHAR(50) NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}',
    tokens_delta    INTEGER NOT NULL DEFAULT 0,
    step_id         INTEGER,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_session_events_session_id ON session_events(session_id);
CREATE INDEX idx_session_events_timestamp ON session_events(timestamp DESC);

-- ─── Auto-update updated_at ──────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_agents_updated_at
    BEFORE UPDATE ON agents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_policies_updated_at
    BEFORE UPDATE ON policies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_execution_graphs_updated_at
    BEFORE UPDATE ON execution_graphs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
