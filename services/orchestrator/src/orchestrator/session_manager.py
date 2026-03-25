"""
Session lifecycle management.

Coordinates between DB, Redis, and Docker:
1. Create session record
2. Load agent/policy/graph from DB
3. Spawn container via container_manager
4. Register container IP → session mapping in Redis
5. Seed Redis counters and graph state
6. On teardown: stop container, flush counters to DB, clean Redis
"""

import json
import logging
import socket
from datetime import datetime, timezone
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amaze_shared.models.agent import AgentDefinition
from amaze_shared.models.graph import ExecutionGraph
from amaze_shared.models.policy import Policy
from amaze_shared.models.session import Session, SessionStatus
from orchestrator import container_manager

logger = logging.getLogger(__name__)

# Redis TTL for agent IP mapping (24 hours; cleaned up explicitly on session end)
AGENT_IP_TTL = 86400


async def start_session(
    *,
    db: AsyncSession,
    redis: aioredis.Redis,
    agent_id: UUID,
    policy_id: UUID,
    execution_graph_id: UUID | None,
    initial_prompt: str,
) -> Session:
    # ── Load agent definition ─────────────────────────────────────────────────
    agent = await db.get(AgentDefinition, agent_id)
    if not agent:
        raise ValueError(f"Agent {agent_id} not found")

    # ── Load policy ───────────────────────────────────────────────────────────
    policy = await db.get(Policy, policy_id)
    if not policy:
        raise ValueError(f"Policy {policy_id} not found")

    # ── Load graph (optional) ─────────────────────────────────────────────────
    graph: ExecutionGraph | None = None
    if execution_graph_id:
        graph = await db.get(ExecutionGraph, execution_graph_id)
        if not graph:
            raise ValueError(f"ExecutionGraph {execution_graph_id} not found")

    # ── Create session record ─────────────────────────────────────────────────
    session = Session(
        agent_id=agent_id,
        policy_id=policy_id,
        execution_graph_id=execution_graph_id,
        initial_prompt=initial_prompt,
        status=SessionStatus.PENDING,
    )
    db.add(session)
    await db.flush()  # get session.id

    session_id = str(session.id)

    # ── Resolve agent hostname to IP for proxy session mapping ────────────────
    try:
        container_ip = socket.gethostbyname(agent.name)
    except socket.gaierror as exc:
        session.status = SessionStatus.FAILED
        await db.commit()
        raise RuntimeError(f"Agent '{agent.name}' not reachable: {exc}") from exc

    # ── Register IP → session in Redis ────────────────────────────────────────
    ip_key = f"agent_ip:{container_ip}"
    await redis.setex(
        ip_key,
        AGENT_IP_TTL,
        json.dumps({"session_id": session_id, "agent_id": str(agent_id)}),
    )

    # ── Seed session state in Redis ───────────────────────────────────────────
    pipe = redis.pipeline()

    # Counters (expire after 24h; refreshed on activity)
    pipe.setex(f"session:{session_id}:tokens_used", AGENT_IP_TTL, 0)
    pipe.setex(f"session:{session_id}:iterations_completed", AGENT_IP_TTL, 0)
    pipe.setex(f"session:{session_id}:mcp_calls_made", AGENT_IP_TTL, 0)
    pipe.setex(f"session:{session_id}:agent_calls_made", AGENT_IP_TTL, 0)

    # Cache policy for proxy (avoid DB roundtrip on every request)
    policy_data = {
        "id": str(policy.id),
        "max_tokens_per_conversation": policy.max_tokens_per_conversation,
        "max_tokens_per_turn": policy.max_tokens_per_turn,
        "max_iterations": policy.max_iterations,
        "max_agent_calls": policy.max_agent_calls,
        "max_mcp_calls": policy.max_mcp_calls,
        "allowed_tools": policy.allowed_tools,
        "allowed_llm_providers": policy.allowed_llm_providers,
        "allowed_mcp_servers": policy.allowed_mcp_servers,
        "on_budget_exceeded": policy.on_budget_exceeded,
        "on_loop_exceeded": policy.on_loop_exceeded,
    }
    pipe.setex(f"session:{session_id}:policy", AGENT_IP_TTL, json.dumps(policy_data))

    # Cache graph + seed step state
    if graph:
        graph_data = {
            "id": str(graph.id),
            "start_step_id": graph.start_step_id,
            "on_violation": graph.on_violation,
            "steps": [
                {
                    "step_id": s.step_id,
                    "label": s.label,
                    "call_type": s.call_type,
                    "callee_id": s.callee_id,
                    "next_step_ids": s.next_step_ids,
                    "max_loops": s.max_loops,
                    "token_cap": s.token_cap,
                }
                for s in graph.steps
            ],
        }
        pipe.setex(f"session:{session_id}:graph", AGENT_IP_TTL, json.dumps(graph_data))
        pipe.setex(f"session:{session_id}:current_step", AGENT_IP_TTL, graph.start_step_id)

        # Seed per-step counters
        for step in graph.steps:
            pipe.setex(f"session:{session_id}:step:{step.step_id}:loops", AGENT_IP_TTL, 0)
            if step.token_cap is not None:
                pipe.setex(f"session:{session_id}:step:{step.step_id}:tokens", AGENT_IP_TTL, 0)

    await pipe.execute()

    # ── Update session in DB ──────────────────────────────────────────────────
    session.status = SessionStatus.RUNNING
    session.started_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(session)

    logger.info("Session %s started (agent=%s ip=%s)", session_id, agent.name, container_ip)
    return session


async def stop_session(
    *,
    db: AsyncSession,
    redis: aioredis.Redis,
    session: Session,
    new_status: SessionStatus = SessionStatus.ABORTED,
) -> None:
    session_id = str(session.id)

    # ── Stop container ────────────────────────────────────────────────────────
    if session.container_id:
        import asyncio
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: container_manager.stop_agent_container(session.container_id),  # type: ignore[arg-type]
        )

    # ── Flush Redis counters to DB ────────────────────────────────────────────
    tokens = await redis.get(f"session:{session_id}:tokens_used")
    iterations = await redis.get(f"session:{session_id}:iterations_completed")
    mcp_calls = await redis.get(f"session:{session_id}:mcp_calls_made")
    agent_calls = await redis.get(f"session:{session_id}:agent_calls_made")

    session.tokens_used = int(tokens or 0)
    session.iterations_completed = int(iterations or 0)
    session.mcp_calls_made = int(mcp_calls or 0)
    session.agent_calls_made = int(agent_calls or 0)
    session.status = new_status
    session.completed_at = datetime.now(timezone.utc)
    await db.commit()

    # ── Clean up Redis ────────────────────────────────────────────────────────
    # Remove remote token if this was a remote session
    remote_token = await redis.get(f"session:{session_id}:remote_token")
    if remote_token:
        await redis.delete(f"session_token:{remote_token}")

    # Gather all session keys to delete
    keys = await redis.keys(f"session:{session_id}:*")
    if keys:
        await redis.delete(*keys)

    # ── Clean workspace ───────────────────────────────────────────────────────
    import asyncio
    await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: container_manager.cleanup_workspace(session_id),
    )

    logger.info("Session %s stopped (status=%s)", session_id, new_status)
