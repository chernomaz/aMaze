-- aMaze seed data — loaded after init.sql on first docker compose up.
-- Provides a ready-to-use demo environment.

-- ─── Default Policy ──────────────────────────────────────────────────────────

INSERT INTO policies (
    id, name, description,
    max_tokens_per_conversation, max_tokens_per_turn,
    max_iterations, max_agent_calls, max_mcp_calls,
    allowed_tools, allowed_llm_providers, allowed_mcp_servers,
    on_budget_exceeded, on_loop_exceeded
) VALUES (
    '00000000-0000-0000-0000-000000000001',
    'default',
    'Default permissive policy for development',
    200000, 20000, 50, 20, 100,
    '[]',
    ARRAY['openai', 'ollama'],
    ARRAY['filesystem-mcp', 'websearch-mcp', 'calculator-mcp', 'counter-mcp'],
    'block', 'block'
);

INSERT INTO policies (
    id, name, description,
    max_tokens_per_conversation, max_tokens_per_turn,
    max_iterations, max_agent_calls, max_mcp_calls,
    allowed_tools, allowed_llm_providers, allowed_mcp_servers,
    on_budget_exceeded, on_loop_exceeded
) VALUES (
    '00000000-0000-0000-0000-000000000002',
    'strict',
    'Strict policy for testing budget enforcement',
    5000, 2000, 5, 2, 10,
    '[]',
    ARRAY['openai', 'ollama'],
    ARRAY['calculator-mcp'],
    'block', 'block'
);

-- ─── Example Execution Graphs ─────────────────────────────────────────────────

-- Graph 1: Simple LLM call then return
INSERT INTO execution_graphs (id, name, description, start_step_id, on_violation)
VALUES (
    '00000000-0000-0000-0001-000000000001',
    'simple-llm',
    'Agent calls LLM once then returns',
    1, 'block'
);

INSERT INTO execution_steps (graph_id, step_id, label, call_type, callee_id, next_step_ids, max_loops, token_cap)
VALUES
    ('00000000-0000-0000-0001-000000000001', 1, 'agent → LLM',    'llm_call',  NULL,      ARRAY[2], 1, 5000),
    ('00000000-0000-0000-0001-000000000001', 2, 'agent → output',  'terminal',  NULL,      ARRAY[]::INTEGER[], 1, NULL);

-- Graph 2: Summarize and Review (agent → LLM → filesystem → LLM → output)
INSERT INTO execution_graphs (id, name, description, start_step_id, on_violation)
VALUES (
    '00000000-0000-0000-0001-000000000002',
    'summarize-and-review',
    'Agent calls LLM, reads a file, calls LLM again to review, then returns',
    1, 'block'
);

INSERT INTO execution_steps (graph_id, step_id, label, call_type, callee_id, next_step_ids, max_loops, token_cap)
VALUES
    ('00000000-0000-0000-0001-000000000002', 1, 'agent → LLM (plan)',     'llm_call',  NULL,                      ARRAY[2], 1, 2000),
    ('00000000-0000-0000-0001-000000000002', 2, 'agent → filesystem',     'mcp_call',  'filesystem-mcp.read_file', ARRAY[3], 3, NULL),
    ('00000000-0000-0000-0001-000000000002', 3, 'agent → LLM (review)',   'llm_call',  NULL,                      ARRAY[4], 1, 3000),
    ('00000000-0000-0000-0001-000000000002', 4, 'agent → output',         'terminal',  NULL,                      ARRAY[]::INTEGER[], 1, NULL);

-- Graph 3: Research loop (LLM → search (up to 3x) → LLM → output)
INSERT INTO execution_graphs (id, name, description, start_step_id, on_violation)
VALUES (
    '00000000-0000-0000-0001-000000000003',
    'research-loop',
    'Agent queries LLM, searches up to 3 times, synthesizes with LLM, returns',
    1, 'block'
);

INSERT INTO execution_steps (graph_id, step_id, label, call_type, callee_id, next_step_ids, max_loops, token_cap)
VALUES
    ('00000000-0000-0000-0001-000000000003', 1, 'agent → LLM (query)',     'llm_call',  NULL,                    ARRAY[2], 1, 2000),
    ('00000000-0000-0000-0001-000000000003', 2, 'agent → web search',      'mcp_call',  'websearch-mcp.search',  ARRAY[3], 3, NULL),
    ('00000000-0000-0000-0001-000000000003', 3, 'agent → LLM (synthesize)','llm_call',  NULL,                    ARRAY[4], 1, 4000),
    ('00000000-0000-0000-0001-000000000003', 4, 'agent → output',          'terminal',  NULL,                    ARRAY[]::INTEGER[], 1, NULL);

-- ─── Dummy Agents ─────────────────────────────────────────────────────────────

INSERT INTO agents (id, name, description, image, version, capabilities, env_vars, status, policy_id, mem_limit)
VALUES
    (
        '00000000-0000-0000-0002-000000000001',
        'echo-agent',
        'Returns input task verbatim. No LLM calls. Use for routing and graph enforcement tests.',
        'amaze/echo-agent:dev',
        'dev',
        ARRAY['echo'],
        '{}',
        'active',
        '00000000-0000-0000-0000-000000000001',
        '512m'
    ),
    (
        '00000000-0000-0000-0002-000000000002',
        'summarizer-agent',
        'Calls LLM once to summarize input text. Tests agent→LLM→output flow.',
        'amaze/summarizer-agent:dev',
        'dev',
        ARRAY['summarize', 'llm'],
        '{"LLM_BASE_URL": "http://95.173.102.50:8000/v1", "LLM_API_KEY": "RBAC-LLM", "LLM_MODEL": "Qwen/Qwen2.5-32B-Instruct"}',
        'active',
        '00000000-0000-0000-0000-000000000001',
        '1g'
    ),
    (
        '00000000-0000-0000-0002-000000000003',
        'researcher-agent',
        'Calls LLM, then web-search MCP tool, then LLM again. Tests research-loop graph.',
        'amaze/researcher-agent:dev',
        'dev',
        ARRAY['research', 'llm', 'search'],
        '{"LLM_BASE_URL": "http://95.173.102.50:8000/v1", "LLM_API_KEY": "RBAC-LLM", "LLM_MODEL": "Qwen/Qwen2.5-32B-Instruct"}',
        'active',
        '00000000-0000-0000-0000-000000000001',
        '1g'
    ),
    (
        '00000000-0000-0000-0002-000000000004',
        'reviewer-agent',
        'Reads a file via filesystem MCP tool then calls LLM to review it. Tests tool→LLM flow.',
        'amaze/reviewer-agent:dev',
        'dev',
        ARRAY['review', 'llm', 'filesystem'],
        '{"LLM_BASE_URL": "http://95.173.102.50:8000/v1", "LLM_API_KEY": "RBAC-LLM", "LLM_MODEL": "Qwen/Qwen2.5-32B-Instruct"}',
        'active',
        '00000000-0000-0000-0000-000000000001',
        '1g'
    );

-- ─── Dummy MCP Tool Registry Entries ──────────────────────────────────────────
-- These are pre-registered since the MCP containers self-register on startup,
-- but we seed them so the UI shows them even before any container has started.

INSERT INTO registry_entries (name, capability_type, version, description, internal_host, internal_port, tags, is_healthy)
VALUES
    (
        'filesystem-mcp',
        'mcp_server',
        '1.0.0',
        'File system operations (read_file, write_file, list_dir) scoped to /workspace',
        'mcp-filesystem',
        8090,
        ARRAY['filesystem', 'files', 'io'],
        true
    ),
    (
        'filesystem-mcp.read_file',
        'mcp_tool',
        '1.0.0',
        'Read a file from /workspace',
        'mcp-filesystem',
        8090,
        ARRAY['filesystem', 'read'],
        true
    ),
    (
        'filesystem-mcp.write_file',
        'mcp_tool',
        '1.0.0',
        'Write a file to /workspace',
        'mcp-filesystem',
        8090,
        ARRAY['filesystem', 'write'],
        true
    ),
    (
        'filesystem-mcp.list_dir',
        'mcp_tool',
        '1.0.0',
        'List directory contents under /workspace',
        'mcp-filesystem',
        8090,
        ARRAY['filesystem', 'list'],
        true
    ),
    (
        'websearch-mcp',
        'mcp_server',
        '1.0.0',
        'Web search (returns hardcoded fake results — offline-safe for testing)',
        'mcp-websearch',
        8090,
        ARRAY['search', 'web', 'fake'],
        true
    ),
    (
        'websearch-mcp.search',
        'mcp_tool',
        '1.0.0',
        'Search the web (fake results, offline-safe)',
        'mcp-websearch',
        8090,
        ARRAY['search', 'web'],
        true
    ),
    (
        'calculator-mcp',
        'mcp_server',
        '1.0.0',
        'Safe math expression evaluator',
        'mcp-calculator',
        8090,
        ARRAY['math', 'calculator'],
        true
    ),
    (
        'calculator-mcp.calculate',
        'mcp_tool',
        '1.0.0',
        'Evaluate a math expression safely (no eval, uses AST)',
        'mcp-calculator',
        8090,
        ARRAY['math'],
        true
    ),
    (
        'counter-mcp',
        'mcp_server',
        '1.0.0',
        'Stateful in-memory counter — ideal for testing max_loops enforcement',
        'mcp-counter',
        8090,
        ARRAY['counter', 'stateful', 'test'],
        true
    ),
    (
        'counter-mcp.increment',
        'mcp_tool',
        '1.0.0',
        'Increment the named counter by 1',
        'mcp-counter',
        8090,
        ARRAY['counter'],
        true
    ),
    (
        'counter-mcp.get',
        'mcp_tool',
        '1.0.0',
        'Get the current value of the named counter',
        'mcp-counter',
        8090,
        ARRAY['counter'],
        true
    );
