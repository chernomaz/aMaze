"""Sprint S9 system tests — MCP tool visibility + live cache invalidation.

Runs against the LIVE ``amaze-platform`` stack (orchestrator :8001,
Redis :6379). Same convention as tests/test_s7_*.py and test_s8_pii.py —
the whole module skips if the stack is not up.

Endpoint slice of ST-TV.*:

  ST-TV.1  Filter runs on tools/list (via /tool-visibility endpoint —
           same filter logic path as the proxy addon).
  ST-TV.2  No-policy pass-through (endpoint returns full tool list).
  ST-TV.4  No-op update ⇒ PUT returns empty changed_fields (nothing to
           publish).
  ST-TV.6  Rapid successive updates ⇒ final state matches final PUT
           (idempotence of the diff-and-publish path).
  ST-TV.7  Tool-visibility endpoint basic call shape.
  ST-TV.8  Owner gate 403 on cross-user access.

Traffic-level slice (ST-TV.3 mid-session notification within 500 ms,
ST-TV.5 Redis-down fail-closed via real proxy) requires driving a live
agent through a mock MCP server AND a way to kill Redis mid-flight —
deferred to a follow-up harness. The addon-internal paths are exercised
here through the equivalent orchestrator endpoint, and the S9 addons'
same code paths are documented in ADR-001.

Fixtures from conftest.py: admin_client, user_a_client, user_b_client.
"""
from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest
import redis


ORCH = os.environ.get("AMAZE_ORCHESTRATOR_S7", "http://localhost:8001")
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

# The demo-mcp server registered in Redis at mcp:demo-mcp (docker-compose
# seed). Tests that need a real MCP server target this name; if the entry
# is missing, individual tests skip so partial stacks still run the parts
# that don't need it.
DEMO_MCP_SERVER = os.environ.get("AMAZE_DEMO_MCP", "demo-mcp")


@pytest.fixture
def rc() -> redis.Redis:
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    try:
        r.ping()
    except redis.RedisError as e:
        pytest.skip(f"redis unreachable at {REDIS_HOST}:{REDIS_PORT}: {e}")
    yield r
    r.close()


def _user_id(rc: redis.Redis, username: str) -> str:
    uid = rc.get(f"username:{username}")
    assert uid, f"username:{username} missing"
    return uid


def _cleanup_agent(rc: redis.Redis, agent_id: str, *usernames: str) -> None:
    for username in usernames:
        uid = rc.get(f"username:{username}")
        if uid:
            rc.srem(f"user:{uid}:agents", agent_id)
    rc.delete(
        f"agent:{agent_id}:owner",
        f"agent:{agent_id}:claim",
        f"policy:{agent_id}",
    )


def _seed_policy_owned_by(
    rc: redis.Redis,
    agent_id: str,
    owner_username: str,
    allowed_tools: list[str],
) -> None:
    owner_uid = _user_id(rc, owner_username)
    rc.set(f"agent:{agent_id}:claim", owner_uid)
    rc.set(f"agent:{agent_id}:owner", owner_uid)
    rc.sadd(f"user:{owner_uid}:agents", agent_id)
    rc.set(f"policy:{agent_id}", json.dumps({
        "name": agent_id,
        "max_tokens_per_turn": 0,
        "max_tool_calls_per_turn": 0,
        "max_agent_calls_per_turn": 0,
        "allowed_llm_providers": [],
        "token_rate_limits": [],
        "on_budget_exceeded": "block",
        "on_violation": "block",
        "mode": "flexible",
        "allowed_tools": allowed_tools,
        "allowed_agents": [],
        "graph": None,
    }))


def _require_demo_mcp(rc: redis.Redis) -> None:
    if not rc.get(f"mcp:{DEMO_MCP_SERVER}"):
        pytest.skip(
            f"mcp:{DEMO_MCP_SERVER} not registered in Redis — start the "
            f"docker-compose demo-mcp service or set AMAZE_DEMO_MCP."
        )


# ---------------------------------------------------------------------------
# ST-TV.1 — filter runs on tools/list (via /tool-visibility)
# ---------------------------------------------------------------------------

def test_sttv1_filter_applies_to_tools_list(
    rc, user_a_client: httpx.Client,
) -> None:
    """With `allowed_tools=[<one_tool>]` the endpoint returns exactly that
    one tool as visible, all others as hidden.
    """
    _require_demo_mcp(rc)
    agent = f"t-tv-{uuid.uuid4().hex[:8]}"
    try:
        # Discover what tools the server publishes; pick the first name.
        _seed_policy_owned_by(
            rc, agent, "s7-user-a",
            allowed_tools=[],  # temporarily allow nothing to enumerate
        )
        r = user_a_client.get(
            f"{ORCH}/policy/{agent}/tool-visibility?server={DEMO_MCP_SERVER}"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        all_tools = body["visible"] + body["hidden"]
        assert all_tools, "demo-mcp published no tools — cannot run ST-TV.1"
        pick = all_tools[0]["name"]

        # Now allow just that one tool and re-check.
        _seed_policy_owned_by(rc, agent, "s7-user-a", allowed_tools=[pick])
        r = user_a_client.get(
            f"{ORCH}/policy/{agent}/tool-visibility?server={DEMO_MCP_SERVER}"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        visible_names = {t["name"] for t in body["visible"]}
        hidden_names = {t["name"] for t in body["hidden"]}
        assert visible_names == {pick}
        assert pick not in hidden_names
        assert len(hidden_names) == len(all_tools) - 1
    finally:
        _cleanup_agent(rc, agent, "s7-user-a")


# ---------------------------------------------------------------------------
# ST-TV.2 — no-policy pass-through
# ---------------------------------------------------------------------------

def test_sttv2_no_policy_passthrough(
    rc, user_a_client: httpx.Client,
) -> None:
    """An agent that owns a slot but has no policy row returns the full
    tool list (nothing hidden). Matches the enforcer's discovery
    pass-through for unpolicied agents.
    """
    _require_demo_mcp(rc)
    agent = f"t-tv-{uuid.uuid4().hex[:8]}"
    try:
        owner_uid = _user_id(rc, "s7-user-a")
        rc.set(f"agent:{agent}:owner", owner_uid)
        rc.sadd(f"user:{owner_uid}:agents", agent)
        # NOTE: intentionally do NOT set policy:{agent}

        r = user_a_client.get(
            f"{ORCH}/policy/{agent}/tool-visibility?server={DEMO_MCP_SERVER}"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["hidden"] == []
        assert body["visible"], "expected non-empty tool list from demo-mcp"
    finally:
        _cleanup_agent(rc, agent, "s7-user-a")


# ---------------------------------------------------------------------------
# ST-TV.4 — no-op PUT returns empty changed_fields
# ---------------------------------------------------------------------------

def test_sttv4_noop_put_no_changed_fields(
    rc, user_a_client: httpx.Client,
) -> None:
    """Two identical PUTs in a row: the second returns changed_fields == []
    so list_change_notifier does NOT publish. Verifies the diff logic.
    """
    agent = f"t-tv-{uuid.uuid4().hex[:8]}"
    try:
        _seed_policy_owned_by(rc, agent, "s7-user-a", allowed_tools=["foo"])
        payload = {
            "name": agent,
            "max_tokens_per_turn": 0,
            "max_tool_calls_per_turn": 0,
            "max_agent_calls_per_turn": 0,
            "allowed_llm_providers": [],
            "token_rate_limits": [],
            "on_budget_exceeded": "block",
            "on_violation": "block",
            "mode": "flexible",
            "allowed_tools": ["foo"],
            "allowed_agents": [],
            "graph": None,
        }
        # First PUT (may or may not diff vs seeded — either is fine).
        r = user_a_client.put(f"{ORCH}/policy/{agent}", json=payload)
        assert r.status_code == 200, r.text

        # Second PUT with the same body: MUST report changed_fields == [].
        r = user_a_client.put(f"{ORCH}/policy/{agent}", json=payload)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["updated"] is True
        assert body["agent_id"] == agent
        assert body["changed_fields"] == [], (
            f"no-op PUT should report empty changed_fields; got {body}"
        )
    finally:
        _cleanup_agent(rc, agent, "s7-user-a")


# ---------------------------------------------------------------------------
# ST-TV.6 — rapid successive updates leave final state consistent
# ---------------------------------------------------------------------------

def test_sttv6_rapid_updates_final_state_wins(
    rc, user_a_client: httpx.Client,
) -> None:
    """Two rapid PUTs with different allowed_tools: the second wins; the
    persisted state matches the second exactly. changed_fields reflects
    the diff on each hop.
    """
    agent = f"t-tv-{uuid.uuid4().hex[:8]}"
    try:
        _seed_policy_owned_by(rc, agent, "s7-user-a", allowed_tools=["a"])
        base = {
            "name": agent,
            "max_tokens_per_turn": 0,
            "max_tool_calls_per_turn": 0,
            "max_agent_calls_per_turn": 0,
            "allowed_llm_providers": [],
            "token_rate_limits": [],
            "on_budget_exceeded": "block",
            "on_violation": "block",
            "mode": "flexible",
            "allowed_agents": [],
            "graph": None,
        }
        first = {**base, "allowed_tools": ["a", "b"]}
        second = {**base, "allowed_tools": ["c"]}

        r1 = user_a_client.put(f"{ORCH}/policy/{agent}", json=first)
        assert r1.status_code == 200
        assert "allowed_tools" in r1.json()["changed_fields"]

        r2 = user_a_client.put(f"{ORCH}/policy/{agent}", json=second)
        assert r2.status_code == 200
        assert "allowed_tools" in r2.json()["changed_fields"]

        # Persisted state matches the last write.
        rget = user_a_client.get(f"{ORCH}/policy/{agent}")
        assert rget.status_code == 200
        assert rget.json()["allowed_tools"] == ["c"]
    finally:
        _cleanup_agent(rc, agent, "s7-user-a")


# ---------------------------------------------------------------------------
# ST-TV.7 — tool-visibility endpoint basic shape
# ---------------------------------------------------------------------------

def test_sttv7_tool_visibility_shape(
    rc, user_a_client: httpx.Client,
) -> None:
    """Endpoint returns {server, visible[], hidden[]} where each tool has
    a `name`. Server dispatch works.
    """
    _require_demo_mcp(rc)
    agent = f"t-tv-{uuid.uuid4().hex[:8]}"
    try:
        _seed_policy_owned_by(rc, agent, "s7-user-a", allowed_tools=[])
        r = user_a_client.get(
            f"{ORCH}/policy/{agent}/tool-visibility?server={DEMO_MCP_SERVER}"
        )
        assert r.status_code == 200
        body = r.json()
        assert body["server"] == DEMO_MCP_SERVER
        assert isinstance(body["visible"], list)
        assert isinstance(body["hidden"], list)
        for t in body["visible"] + body["hidden"]:
            assert isinstance(t.get("name"), str) and t["name"], t

        # 404 on an unknown server
        r = user_a_client.get(
            f"{ORCH}/policy/{agent}/tool-visibility?server=no-such-server"
        )
        assert r.status_code == 404
    finally:
        _cleanup_agent(rc, agent, "s7-user-a")


# ---------------------------------------------------------------------------
# ST-TV.8 — cross-user 403 on the endpoint
# ---------------------------------------------------------------------------

def test_sttv8_cross_user_denied(
    rc,
    user_a_client: httpx.Client,
    user_b_client: httpx.Client,
) -> None:
    """User B (owner: user A) → 403 on the tool-visibility endpoint."""
    agent = f"t-tv-{uuid.uuid4().hex[:8]}"
    try:
        _seed_policy_owned_by(rc, agent, "s7-user-a", allowed_tools=["x"])
        r = user_b_client.get(
            f"{ORCH}/policy/{agent}/tool-visibility?server={DEMO_MCP_SERVER}"
        )
        assert r.status_code == 403, r.text
    finally:
        _cleanup_agent(rc, agent, "s7-user-a", "s7-user-b")
