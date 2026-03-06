from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from amaze_shared.models.agent import AgentDefinition, AgentFilesystemMount
from api_gateway.deps import DB

router = APIRouter(prefix="/agents", tags=["agents"])


# ─── Schemas ──────────────────────────────────────────────────────────────────


class MountSchema(BaseModel):
    host_path: str
    container_path: str
    read_only: bool = False


class AgentCreate(BaseModel):
    name: str
    description: str = ""
    image: str
    version: str = "latest"
    capabilities: list[str] = []
    required_capabilities: list[str] = []
    env_vars: dict[str, str] = {}
    secret_refs: list[str] = []
    policy_id: UUID | None = None
    mem_limit: str = "2g"
    cpu_quota: int = 100000
    mounts: list[MountSchema] = []


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    image: str | None = None
    version: str | None = None
    capabilities: list[str] | None = None
    required_capabilities: list[str] | None = None
    env_vars: dict[str, str] | None = None
    policy_id: UUID | None = None
    mem_limit: str | None = None
    mounts: list[MountSchema] | None = None


class MountResponse(BaseModel):
    id: UUID
    host_path: str
    container_path: str
    read_only: bool

    model_config = {"from_attributes": True}


class AgentResponse(BaseModel):
    id: UUID
    name: str
    description: str
    image: str
    version: str
    capabilities: list[str]
    required_capabilities: list[str]
    env_vars: dict
    secret_refs: list[str]
    status: str
    policy_id: UUID | None
    mem_limit: str
    cpu_quota: int
    mounts: list[MountResponse]

    model_config = {"from_attributes": True}


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("", response_model=list[AgentResponse])
async def list_agents(db: DB):
    result = await db.execute(select(AgentDefinition))
    return result.scalars().all()


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: UUID, db: DB):
    agent = await db.get(AgentDefinition, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(body: AgentCreate, db: DB):
    agent = AgentDefinition(
        name=body.name,
        description=body.description,
        image=body.image,
        version=body.version,
        capabilities=body.capabilities,
        required_capabilities=body.required_capabilities,
        env_vars=body.env_vars,
        secret_refs=body.secret_refs,
        policy_id=body.policy_id,
        mem_limit=body.mem_limit,
        cpu_quota=body.cpu_quota,
    )
    db.add(agent)
    await db.flush()

    for m in body.mounts:
        db.add(AgentFilesystemMount(
            agent_id=agent.id,
            host_path=m.host_path,
            container_path=m.container_path,
            read_only=m.read_only,
        ))

    await db.commit()
    await db.refresh(agent)
    return agent


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: UUID, body: AgentUpdate, db: DB):
    agent = await db.get(AgentDefinition, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    for field, value in body.model_dump(exclude_none=True, exclude={"mounts"}).items():
        setattr(agent, field, value)

    if body.mounts is not None:
        # Replace all mounts
        for m in agent.mounts:
            await db.delete(m)
        for m in body.mounts:
            db.add(AgentFilesystemMount(
                agent_id=agent.id,
                host_path=m.host_path,
                container_path=m.container_path,
                read_only=m.read_only,
            ))

    await db.commit()
    await db.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: UUID, db: DB):
    agent = await db.get(AgentDefinition, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    await db.delete(agent)
    await db.commit()
