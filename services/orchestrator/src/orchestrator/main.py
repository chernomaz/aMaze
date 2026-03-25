import asyncio
import logging
import os
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Request

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from amaze_shared.models.session import Session, SessionStatus
from orchestrator import session_manager

logger = logging.getLogger(__name__)

# ─── DB + Redis setup ─────────────────────────────────────────────────────────

engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)

redis_pool = aioredis.ConnectionPool.from_url(
    os.environ.get("REDIS_URL", "redis://redis:6379"), decode_responses=True
)


def get_redis() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=redis_pool)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure workspace root exists
    workspace = os.environ.get("AGENT_WORKSPACE_CONTAINER_PATH", "/agent-workspaces")
    os.makedirs(workspace, exist_ok=True)
    yield


app = FastAPI(title="aMaze Orchestrator", version="0.1.0", lifespan=lifespan)


# ─── Schemas ──────────────────────────────────────────────────────────────────


class SessionCreateRequest(BaseModel):
    agent_id: UUID
    policy_id: UUID
    execution_graph_id: UUID | None = None
    initial_prompt: str = ""


class SessionResponse(BaseModel):
    id: UUID
    agent_id: UUID
    policy_id: UUID
    execution_graph_id: UUID | None
    container_id: str | None
    container_name: str | None
    status: str
    initial_prompt: str

    model_config = {"from_attributes": True}


# ─── Routes ───────────────────────────────────────────────────────────────────


async def _run_agent(agent_name: str, session_id: UUID, initial_prompt: str) -> None:
    """Background task: invoke the pre-running agent container and finalize the session."""
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"http://{agent_name}:8090/invoke",
                json={"task": initial_prompt},
            )
            resp.raise_for_status()
            result = resp.json()
            final_output = result.get("output", "")
            new_status = SessionStatus.COMPLETED
    except Exception as exc:
        logger.error("Agent invocation failed for session %s: %s", session_id, exc)
        final_output = str(exc)
        new_status = SessionStatus.FAILED

    # Finalize session in DB
    redis = get_redis()
    async with SessionFactory() as db:
        try:
            session = await db.get(Session, session_id)
            if session and session.status == SessionStatus.RUNNING:
                await session_manager.stop_session(
                    db=db,
                    redis=redis,
                    session=session,
                    new_status=new_status,
                )
                session.final_output = final_output
                await db.commit()
        finally:
            await redis.aclose()


@app.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(body: SessionCreateRequest):
    from amaze_shared.models.agent import AgentDefinition
    redis = get_redis()
    async with SessionFactory() as db:
        agent = await db.get(AgentDefinition, body.agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        agent_name = agent.name
        try:
            session = await session_manager.start_session(
                db=db,
                redis=redis,
                agent_id=body.agent_id,
                policy_id=body.policy_id,
                execution_graph_id=body.execution_graph_id,
                initial_prompt=body.initial_prompt,
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            await redis.aclose()

    # Fire-and-forget: send prompt to the pre-running agent
    asyncio.create_task(
        _run_agent(agent_name, session.id, body.initial_prompt)
    )
    return session


@app.delete("/sessions/{session_id}", status_code=204)
async def abort_session(session_id: UUID):
    redis = get_redis()
    async with SessionFactory() as db:
        session = await db.get(Session, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.status not in (SessionStatus.PENDING, SessionStatus.RUNNING):
            raise HTTPException(
                status_code=409,
                detail=f"Session already in terminal state: {session.status}",
            )
        try:
            await session_manager.stop_session(
                db=db,
                redis=redis,
                session=session,
                new_status=SessionStatus.ABORTED,
            )
        finally:
            await redis.aclose()


@app.get("/sessions/{session_id}/container-status")
async def container_status(session_id: UUID):
    async with SessionFactory() as db:
        session = await db.get(Session, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if not session.container_id:
            return {"status": "no_container"}
        from orchestrator.container_manager import get_container_status
        status = get_container_status(session.container_id)
        return {"container_id": session.container_id, "status": status}


@app.post("/sessions/{parent_session_id}/invoke-agent")
async def invoke_agent(parent_session_id: UUID, request: Request):
    """
    Called by the proxy when an agent makes an agent-to-agent call.
    Spawns a child session for the target agent and forwards the invoke request.
    """
    from fastapi import Request as _Request
    import httpx as _httpx

    target_agent_name = request.headers.get("X-Amaze-Target-Agent")
    if not target_agent_name:
        raise HTTPException(status_code=400, detail="X-Amaze-Target-Agent header required")

    body = await request.json()

    # Look up target agent definition
    redis = get_redis()
    async with SessionFactory() as db:
        from sqlalchemy import select as _select
        from amaze_shared.models.agent import AgentDefinition
        result = await db.execute(
            _select(AgentDefinition).where(AgentDefinition.name == target_agent_name)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent '{target_agent_name}' not found")

        # Load parent session to inherit policy/graph
        parent = await db.get(Session, parent_session_id)
        if not parent:
            raise HTTPException(status_code=404, detail="Parent session not found")

        try:
            child_session = await session_manager.start_session(
                db=db,
                redis=redis,
                agent_id=agent.id,
                policy_id=parent.policy_id,
                execution_graph_id=None,  # Child sessions run without graph constraints
                initial_prompt=body.get("task", ""),
            )
        finally:
            await redis.aclose()

    # Forward the invoke request to the child agent container
    async with _httpx.AsyncClient(timeout=body.get("timeout_seconds", 300)) as client:
        try:
            resp = await client.post(
                f"http://{child_session.container_name}:8090/invoke",
                json=body,
            )
            resp.raise_for_status()
            return resp.json()
        except _httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"Child agent unreachable: {e}")


class RemoteSessionCreateRequest(BaseModel):
    agent_id: UUID
    policy_id: UUID
    execution_graph_id: UUID | None = None
    initial_prompt: str = ""


class RemoteSessionResponse(BaseModel):
    session_id: UUID
    token: str


@app.post("/sessions/remote", response_model=RemoteSessionResponse, status_code=201)
async def create_remote_session(body: RemoteSessionCreateRequest):
    """
    Create a session for an agent running on a remote host (not spawned as a local container).
    Generates a token the agent must send as X-Amaze-Session-Token on every proxied request.
    """
    import secrets
    import json

    redis = get_redis()
    async with SessionFactory() as db:
        try:
            from amaze_shared.models.agent import AgentDefinition
            from amaze_shared.models.policy import Policy
            from amaze_shared.models.session import Session, SessionStatus
            from datetime import datetime, timezone

            agent = await db.get(AgentDefinition, body.agent_id)
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")

            policy = await db.get(Policy, body.policy_id)
            if not policy:
                raise HTTPException(status_code=404, detail="Policy not found")

            session = Session(
                agent_id=body.agent_id,
                policy_id=body.policy_id,
                execution_graph_id=body.execution_graph_id,
                initial_prompt=body.initial_prompt,
                status=SessionStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )
            db.add(session)
            await db.flush()

            session_id = str(session.id)
            token = secrets.token_urlsafe(32)

            TTL = 86400
            pipe = redis.pipeline()

            pipe.setex(
                f"session_token:{token}",
                TTL,
                json.dumps({"session_id": session_id, "agent_id": str(body.agent_id)}),
            )
            pipe.setex(f"session:{session_id}:remote_token", TTL, token)

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
            pipe.setex(f"session:{session_id}:policy", TTL, json.dumps(policy_data))

            for counter in ("tokens_used", "iterations_completed", "mcp_calls_made", "agent_calls_made"):
                pipe.setex(f"session:{session_id}:{counter}", TTL, 0)

            await pipe.execute()
            await db.commit()

            return RemoteSessionResponse(session_id=session.id, token=token)
        finally:
            await redis.aclose()


@app.get("/health")
async def health():
    return {"status": "ok"}
