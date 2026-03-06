import os
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from amaze_shared.models.session import Session, SessionEvent
from api_gateway.deps import DB

router = APIRouter(prefix="/sessions", tags=["sessions"])

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8001")


# ─── Schemas ──────────────────────────────────────────────────────────────────


class SessionCreate(BaseModel):
    agent_id: UUID
    policy_id: UUID
    execution_graph_id: UUID | None = None
    initial_prompt: str = ""


class SessionEventResponse(BaseModel):
    id: UUID
    event_type: str
    payload: dict
    tokens_delta: int
    step_id: int | None
    timestamp: str

    model_config = {"from_attributes": True}


class SessionResponse(BaseModel):
    id: UUID
    agent_id: UUID
    policy_id: UUID
    execution_graph_id: UUID | None
    container_id: str | None
    status: str
    initial_prompt: str
    final_output: str | None
    tokens_used: int
    iterations_completed: int
    mcp_calls_made: int
    agent_calls_made: int
    created_at: str

    model_config = {"from_attributes": True}


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("", response_model=list[SessionResponse])
async def list_sessions(db: DB, status: str | None = None, agent_id: UUID | None = None):
    stmt = select(Session).order_by(Session.created_at.desc())
    if status:
        stmt = stmt.where(Session.status == status)
    if agent_id:
        stmt = stmt.where(Session.agent_id == agent_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: UUID, db: DB):
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/{session_id}/events", response_model=list[SessionEventResponse])
async def get_session_events(session_id: UUID, db: DB):
    result = await db.execute(
        select(SessionEvent)
        .where(SessionEvent.session_id == session_id)
        .order_by(SessionEvent.timestamp.asc())
    )
    return result.scalars().all()


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(body: SessionCreate, db: DB):
    """Delegates to the Orchestrator, which spawns the container."""
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{ORCHESTRATOR_URL}/sessions",
                json=body.model_dump(mode="json"),
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"Orchestrator unavailable: {e}")

    session_id = resp.json()["id"]
    session = await db.get(Session, UUID(session_id))
    if not session:
        raise HTTPException(status_code=500, detail="Session created but not found in DB")
    return session


@router.delete("/{session_id}", status_code=204)
async def abort_session(session_id: UUID, db: DB):
    """Delegates to the Orchestrator to stop the container."""
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.delete(f"{ORCHESTRATOR_URL}/sessions/{session_id}")
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"Orchestrator unavailable: {e}")
