import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from amaze_shared.models.registry import RegistryEntry
from registry.schemas import HeartbeatResponse, RegisterRequest, RegistryEntryResponse

logger = logging.getLogger(__name__)

# ─── Database setup ───────────────────────────────────────────────────────────

engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)

HEARTBEAT_TTL_SECONDS = 60
HEALTH_CHECK_INTERVAL_SECONDS = 30


async def mark_stale_unhealthy() -> None:
    """Background task: mark entries unhealthy if no heartbeat within TTL."""
    while True:
        await asyncio.sleep(HEALTH_CHECK_INTERVAL_SECONDS)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=HEARTBEAT_TTL_SECONDS)
        async with SessionFactory() as session:
            await session.execute(
                update(RegistryEntry)
                .where(RegistryEntry.last_heartbeat < cutoff, RegistryEntry.is_healthy.is_(True))
                .values(is_healthy=False)
            )
            await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(mark_stale_unhealthy())
    yield
    task.cancel()


app = FastAPI(title="aMaze Registry", version="0.1.0", lifespan=lifespan)


# ─── Routes ───────────────────────────────────────────────────────────────────


@app.get("/capabilities", response_model=list[RegistryEntryResponse])
async def list_capabilities(
    capability_type: str | None = Query(None),
    tag: str | None = Query(None),
    is_healthy: bool | None = Query(None),
    name: str | None = Query(None),
):
    async with SessionFactory() as session:
        stmt = select(RegistryEntry)
        if capability_type:
            stmt = stmt.where(RegistryEntry.capability_type == capability_type)
        if tag:
            stmt = stmt.where(RegistryEntry.tags.contains([tag]))
        if is_healthy is not None:
            stmt = stmt.where(RegistryEntry.is_healthy == is_healthy)
        if name:
            stmt = stmt.where(RegistryEntry.name.ilike(f"%{name}%"))
        result = await session.execute(stmt)
        return result.scalars().all()


@app.get("/capabilities/{name}", response_model=RegistryEntryResponse)
async def get_capability(name: str):
    async with SessionFactory() as session:
        result = await session.execute(
            select(RegistryEntry).where(RegistryEntry.name == name)
        )
        entry = result.scalar_one_or_none()
        if not entry:
            raise HTTPException(status_code=404, detail=f"Capability '{name}' not found")
        return entry


@app.post("/register", response_model=RegistryEntryResponse, status_code=201)
async def register(req: RegisterRequest):
    async with SessionFactory() as session:
        # Upsert: update if exists, create if not
        result = await session.execute(
            select(RegistryEntry).where(RegistryEntry.name == req.name)
        )
        entry = result.scalar_one_or_none()

        now = datetime.now(timezone.utc)
        if entry:
            entry.capability_type = req.capability_type
            entry.version = req.version
            entry.description = req.description
            entry.internal_host = req.internal_host
            entry.internal_port = req.internal_port
            entry.input_schema = req.input_schema
            entry.output_schema = req.output_schema
            entry.tags = req.tags
            entry.is_healthy = True
            entry.last_heartbeat = now
            entry.owner_agent_id = req.owner_agent_id
        else:
            entry = RegistryEntry(
                name=req.name,
                capability_type=req.capability_type,
                version=req.version,
                description=req.description,
                internal_host=req.internal_host,
                internal_port=req.internal_port,
                input_schema=req.input_schema,
                output_schema=req.output_schema,
                tags=req.tags,
                is_healthy=True,
                last_heartbeat=now,
                registered_at=now,
                owner_agent_id=req.owner_agent_id,
            )
            session.add(entry)

        await session.commit()
        await session.refresh(entry)
        logger.info("Registered capability: %s (%s)", entry.name, entry.capability_type)
        return entry


@app.post("/heartbeat/{name}", response_model=HeartbeatResponse)
async def heartbeat(name: str):
    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        result = await session.execute(
            update(RegistryEntry)
            .where(RegistryEntry.name == name)
            .values(last_heartbeat=now, is_healthy=True)
            .returning(RegistryEntry.name, RegistryEntry.last_heartbeat)
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail=f"Capability '{name}' not found")
        await session.commit()
        return HeartbeatResponse(name=row.name, last_heartbeat=row.last_heartbeat)


@app.delete("/capabilities/{name}", status_code=204)
async def deregister(name: str):
    async with SessionFactory() as session:
        result = await session.execute(
            select(RegistryEntry).where(RegistryEntry.name == name)
        )
        entry = result.scalar_one_or_none()
        if not entry:
            raise HTTPException(status_code=404, detail=f"Capability '{name}' not found")
        await session.delete(entry)
        await session.commit()


@app.get("/health")
async def health():
    return {"status": "ok"}
