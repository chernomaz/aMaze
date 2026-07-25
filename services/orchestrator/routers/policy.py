"""GET /policy/{agent_id}, PUT /policy/{agent_id}, and
GET /policy/{agent_id}/tool-visibility — Redis-backed policy CRUD.

S4-T2.2: Redis is the source of truth. YAML at `config/policies.yaml` is
read once at boot via `policy_store.bootstrap_from_yaml()` (wired in
main.py's lifespan) and ONLY for entries not yet present in Redis.

After PUT returns 200, the very next proxy request from that agent will
see the new policy — the proxy enforcer refetches per-request, so no
cache invalidation step is needed.

S9: PUT also PUBLISHes to the Redis Pub/Sub channel `policy:changed` when
`allowed_tools` diffs, so live agent SSE streams get pushed a
`notifications/tools/list_changed` frame by the proxy's
`list_change_notifier` addon. See ADR-001.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from services.orchestrator.auth import User, current_user, require_agent_owner
from services.orchestrator.mcp_probe import ProbeError, probe_tools
from services.proxy import policy_store
from services.proxy.policy import Policy

logger = logging.getLogger(__name__)

POLICY_CHANGED_CHANNEL = "policy:changed"

router = APIRouter()


async def _assemble_tools_payload(r: redis.Redis, allowed: list[str]) -> list[dict]:
    """Return `[{name, description, inputSchema}, ...]` for each name in
    `allowed`, drawing schemas from every approved `mcp:{name}` cache.

    Silently skips names we can't resolve to a cached MCP tool — those
    are either typos in the policy or tools whose server was removed.
    The SDK on the receiving side treats an empty list as "the current
    policy allows nothing".
    """
    if not allowed:
        return []
    allowed_set = set(allowed)
    payload: list[dict] = []
    seen: set[str] = set()
    try:
        async for key in r.scan_iter(match="mcp:*", count=200):
            key_str = key if isinstance(key, str) else key.decode("utf-8")
            # Skip bare `mcp:{name}:approved` / `mcp:{name}:refresh_*` markers.
            if key_str.count(":") != 1:
                continue
            raw = await r.get(key_str)
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except (ValueError, TypeError):
                continue
            for t in entry.get("tools", []) or []:
                name = t.get("name") if isinstance(t, dict) else None
                if not isinstance(name, str) or name in seen:
                    continue
                if name not in allowed_set:
                    continue
                seen.add(name)
                # Trim the entry to the fields the SDK contract documents;
                # servers can attach extra metadata (_meta, outputSchema)
                # that isn't part of the push contract.
                payload.append({
                    "name": name,
                    "description": t.get("description") or "",
                    "inputSchema": t.get("inputSchema") or {},
                })
    except redis.RedisError as e:
        logger.warning("tools payload assembly: redis scan failed: %s", e)
    # Preserve `allowed`'s ordering if the caller cares (deterministic
    # for tests + human-readable trace logs).
    order = {n: i for i, n in enumerate(allowed)}
    payload.sort(key=lambda t: order.get(t["name"], len(order)))
    return payload


async def _push_tools_changed(
    r: redis.Redis, agent_id: str, allowed: list[str],
) -> None:
    """Best-effort POST of the pushed payload to the agent's chat endpoint.
    Failures are logged; the PUT succeeds regardless (the S9 tool_list_filter
    and LLMToolStripper still enforce at the wire, and the SDK can also poll
    /agents/self/allowed-tools if the push is missed).
    """
    # Prefer the A2A endpoint — every agent registers one. chat_endpoint is
    # optional (only user-facing agents like agent-sdk have it).
    try:
        endpoint = await r.get(f"agent:{agent_id}:endpoint")
        if not endpoint:
            endpoint = await r.get(f"agent:{agent_id}:chat_endpoint")
        bearer = await r.get(f"agent:{agent_id}:bearer_token")
    except redis.RedisError as e:
        logger.warning(
            "tools push: redis lookup failed for agent=%s: %s", agent_id, e,
        )
        return
    if not endpoint or not bearer:
        logger.info(
            "tools push: agent=%s has no endpoint / bearer_token — "
            "skipping (agent hasn't registered yet or entry expired)",
            agent_id,
        )
        return

    if isinstance(endpoint, bytes):
        endpoint = endpoint.decode("utf-8")
    if isinstance(bearer, bytes):
        bearer = bearer.decode("utf-8")

    payload = {"allowed_tools": await _assemble_tools_payload(r, allowed)}
    url = f"{endpoint.rstrip('/')}/_amaze/tools_changed"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"X-Amaze-Bearer": bearer},
            )
        logger.info(
            "tools push: agent=%s → %s status=%d (payload=%d tools)",
            agent_id, url, resp.status_code, len(payload["allowed_tools"]),
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "tools push: agent=%s → %s failed: %s", agent_id, url, exc,
        )


def _diff_fields(old: Policy | None, new: Policy) -> set[str]:
    """Return the set of top-level field names whose values differ.

    Called on PUT; if `old` is None (agent had no policy before) every
    field is treated as changed. The addon that consumes the pub/sub
    message only cares about `allowed_tools` today, but we ship the full
    diff so future consumers (PII UI, graph editor) can react too.
    """
    new_dump = new.model_dump(mode="json")
    if old is None:
        return set(new_dump.keys())
    old_dump = old.model_dump(mode="json")
    return {k for k in new_dump if old_dump.get(k) != new_dump.get(k)}


@router.get("/policy/{agent_id}")
async def get_policy_endpoint(
    agent_id: str,
    request: Request,
    user: User = Depends(current_user),
) -> dict:
    """Return the full policy JSON for `agent_id`. 404 if absent in Redis
    AND in the YAML fallback. S7: caller must own the agent.
    """
    await require_agent_owner(request.app.state.redis, agent_id, user)
    try:
        policy = await policy_store.get_policy(agent_id)
    except redis.RedisError as e:
        logger.error("policy GET: redis unreachable for %s: %s", agent_id, e)
        raise HTTPException(status_code=503, detail="redis-unavailable") from e

    if policy is None:
        raise HTTPException(status_code=404, detail="policy-not-found")
    return policy.model_dump(mode="json")


@router.put("/policy/{agent_id}")
async def put_policy_endpoint(
    agent_id: str,
    policy: Policy,
    request: Request,
    user: User = Depends(current_user),
) -> dict:
    """Persist the full policy in Redis.

    FastAPI validates the body against the `Policy` Pydantic model — a
    malformed body returns 422 automatically. The `name` field is taken
    from the body; we do NOT enforce it equals `agent_id` (the YAML
    invariant) because the path is the canonical key here.

    S7: caller must own the agent.
    S9: after a successful write, diff old vs new and PUBLISH
    `policy:changed` if any field diverged. Publish is best-effort —
    Redis Pub/Sub failure does not fail the PUT (the change is already
    live via the per-request refetch); we log and move on.
    """
    await require_agent_owner(request.app.state.redis, agent_id, user)

    try:
        old = await policy_store.get_policy(agent_id)
    except redis.RedisError as e:
        logger.error("policy PUT: pre-diff GET redis unreachable for %s: %s",
                     agent_id, e)
        raise HTTPException(status_code=503, detail="redis-unavailable") from e

    try:
        await policy_store.put_policy(agent_id, policy)
    except redis.RedisError as e:
        logger.error("policy PUT: redis unreachable for %s: %s", agent_id, e)
        raise HTTPException(status_code=503, detail="redis-unavailable") from e
    logger.info("policy PUT: agent_id=%s name=%s mode=%s",
                agent_id, policy.name, policy.mode)

    changed = _diff_fields(old, policy)
    if changed:
        payload = json.dumps({
            "agent_id": agent_id,
            "changed_fields": sorted(changed),
        })
        try:
            await request.app.state.redis.publish(
                POLICY_CHANGED_CHANNEL, payload,
            )
            logger.info(
                "policy PUT: published %s for agent=%s fields=%s",
                POLICY_CHANGED_CHANNEL, agent_id, sorted(changed),
            )
        except redis.RedisError as e:
            # Best-effort — the policy is already written and the next
            # per-request refetch will pick it up. Live-session
            # notifications will simply be missed until agent re-lists
            # organically.
            logger.warning(
                "policy PUT: publish to %s failed for agent=%s: %s",
                POLICY_CHANGED_CHANNEL, agent_id, e,
            )

        # S9.3: push the assembled tools payload directly to the agent
        # so the SDK's amaze.is_tools_changed() / amaze.current_tools()
        # can surface it before the next inbound message. Fire and
        # forget — a failed push doesn't fail the PUT.
        if "allowed_tools" in changed:
            asyncio.create_task(
                _push_tools_changed(
                    request.app.state.redis,
                    agent_id,
                    list(policy.allowed_tools),
                )
            )

    return {
        "updated": True,
        "agent_id": agent_id,
        "changed_fields": sorted(changed),
    }


@router.get("/agents/self/allowed-tools")
async def agent_self_allowed_tools(request: Request) -> dict:
    """Return the calling agent's own `allowed_tools`.

    Agent-bearer authenticated. The SDK injects `X-Amaze-Bearer: <token>`
    on every outbound request; we resolve `session_token:{token}` → agent_id
    the same way the proxy does. Used by the SDK's opt-in
    `on_tools_changed` hook to detect policy drift and trigger a rebuild
    of the agent's compiled LangChain / LangGraph — the runtime cache
    invalidation path that the MCP-level `notifications/tools/list_changed`
    frame cannot deliver reliably (mitmproxy stream handler is
    per-upstream-chunk, and langchain-mcp-adapters has no callback slot
    for it anyway). See ADR-001 §"Delivery model".

    401 if bearer is missing/unknown, 404 if the agent has no policy row.
    """
    bearer = request.headers.get("X-Amaze-Bearer") or ""
    if not bearer:
        raise HTTPException(status_code=401, detail="bearer-missing")
    try:
        agent_id_raw = await request.app.state.redis.get(f"session_token:{bearer}")
    except redis.RedisError as e:
        logger.error("agent_self_allowed_tools: redis unreachable: %s", e)
        raise HTTPException(status_code=503, detail="redis-unavailable") from e
    if not agent_id_raw:
        raise HTTPException(status_code=401, detail="bearer-invalid")
    agent_id = agent_id_raw if isinstance(agent_id_raw, str) else agent_id_raw.decode("utf-8")

    try:
        policy = await policy_store.get_policy(agent_id)
    except redis.RedisError as e:
        logger.error("agent_self_allowed_tools: policy fetch redis failed for %s: %s",
                     agent_id, e)
        raise HTTPException(status_code=503, detail="redis-unavailable") from e
    if policy is None:
        raise HTTPException(status_code=404, detail="policy-not-found")
    return {
        "agent_id": agent_id,
        "allowed_tools": list(policy.allowed_tools),
    }


@router.get("/policy/{agent_id}/tool-visibility")
async def tool_visibility_endpoint(
    agent_id: str,
    request: Request,
    server: str = Query(..., description="MCP server name registered in Redis mcp:{name}"),
    user: User = Depends(current_user),
) -> dict:
    """Return the tools this agent would see for `server` after S9 filtering.

    Shape:
        {
          "server": "<name>",
          "visible": [{name, description, inputSchema}, ...],
          "hidden":  [{name, description, inputSchema}, ...]
        }

    Powers the "Tool visibility" preview panel on the AgentPolicy page.
    Owner-gated (S7).
    """
    await require_agent_owner(request.app.state.redis, agent_id, user)

    r = request.app.state.redis
    try:
        raw = await r.get(f"mcp:{server}")
    except redis.RedisError as e:
        logger.error(
            "tool_visibility: redis GET mcp:%s failed: %s", server, e,
        )
        raise HTTPException(status_code=503, detail="redis-unavailable") from e
    if not raw:
        raise HTTPException(status_code=404, detail=f"mcp-server-not-found:{server}")
    try:
        entry = json.loads(raw)
    except (ValueError, TypeError) as e:
        logger.error("tool_visibility: mcp:%s malformed json: %s", server, e)
        raise HTTPException(status_code=500, detail="mcp-server-malformed") from e

    url = entry.get("url")
    if not url:
        raise HTTPException(status_code=500, detail="mcp-server-no-url")

    try:
        tools = await probe_tools(url)
    except ProbeError as e:
        logger.warning(
            "tool_visibility: probe %s (%s) failed: %s", server, url, e,
        )
        raise HTTPException(status_code=502, detail=f"mcp-probe-failed:{e}") from e

    try:
        policy = await policy_store.get_policy(agent_id)
    except redis.RedisError as e:
        logger.error(
            "tool_visibility: policy GET redis unreachable for %s: %s",
            agent_id, e,
        )
        raise HTTPException(status_code=503, detail="redis-unavailable") from e

    if policy is None:
        # No policy row → discovery pass-through. Match proxy behaviour: all
        # tools are visible.
        visible = tools
        hidden: list[dict] = []
    else:
        allowed = set(policy.allowed_tools)
        visible = [t for t in tools if t.get("name") in allowed]
        hidden = [t for t in tools if t.get("name") not in allowed]

    return {
        "server": server,
        "visible": visible,
        "hidden": hidden,
    }
