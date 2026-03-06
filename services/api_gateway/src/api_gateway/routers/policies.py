from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from amaze_shared.models.policy import Policy
from api_gateway.deps import DB

router = APIRouter(prefix="/policies", tags=["policies"])


class ToolPermissionSchema(BaseModel):
    tool_name: str
    allowed: bool = True
    params_allowlist: dict | None = None


class PolicyCreate(BaseModel):
    name: str
    description: str = ""
    max_tokens_per_conversation: int = 100000
    max_tokens_per_turn: int = 10000
    max_iterations: int = 20
    max_agent_calls: int = 10
    max_mcp_calls: int = 50
    allowed_tools: list[ToolPermissionSchema] = []
    allowed_llm_providers: list[str] = []
    allowed_mcp_servers: list[str] = []
    on_budget_exceeded: str = "block"
    on_loop_exceeded: str = "block"


class PolicyUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    max_tokens_per_conversation: int | None = None
    max_tokens_per_turn: int | None = None
    max_iterations: int | None = None
    max_agent_calls: int | None = None
    max_mcp_calls: int | None = None
    allowed_tools: list[ToolPermissionSchema] | None = None
    allowed_llm_providers: list[str] | None = None
    allowed_mcp_servers: list[str] | None = None
    on_budget_exceeded: str | None = None
    on_loop_exceeded: str | None = None


class PolicyResponse(BaseModel):
    id: UUID
    name: str
    description: str
    max_tokens_per_conversation: int
    max_tokens_per_turn: int
    max_iterations: int
    max_agent_calls: int
    max_mcp_calls: int
    allowed_tools: list[dict]
    allowed_llm_providers: list[str]
    allowed_mcp_servers: list[str]
    on_budget_exceeded: str
    on_loop_exceeded: str

    model_config = {"from_attributes": True}


@router.get("", response_model=list[PolicyResponse])
async def list_policies(db: DB):
    result = await db.execute(select(Policy))
    return result.scalars().all()


@router.get("/{policy_id}", response_model=PolicyResponse)
async def get_policy(policy_id: UUID, db: DB):
    policy = await db.get(Policy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


@router.post("", response_model=PolicyResponse, status_code=201)
async def create_policy(body: PolicyCreate, db: DB):
    policy = Policy(
        name=body.name,
        description=body.description,
        max_tokens_per_conversation=body.max_tokens_per_conversation,
        max_tokens_per_turn=body.max_tokens_per_turn,
        max_iterations=body.max_iterations,
        max_agent_calls=body.max_agent_calls,
        max_mcp_calls=body.max_mcp_calls,
        allowed_tools=[t.model_dump() for t in body.allowed_tools],
        allowed_llm_providers=body.allowed_llm_providers,
        allowed_mcp_servers=body.allowed_mcp_servers,
        on_budget_exceeded=body.on_budget_exceeded,
        on_loop_exceeded=body.on_loop_exceeded,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy


@router.put("/{policy_id}", response_model=PolicyResponse)
async def update_policy(policy_id: UUID, body: PolicyUpdate, db: DB):
    policy = await db.get(Policy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    data = body.model_dump(exclude_none=True)
    if "allowed_tools" in data:
        data["allowed_tools"] = [t.model_dump() for t in body.allowed_tools]  # type: ignore[union-attr]
    for field, value in data.items():
        setattr(policy, field, value)

    await db.commit()
    await db.refresh(policy)
    return policy


@router.delete("/{policy_id}", status_code=204)
async def delete_policy(policy_id: UUID, db: DB):
    policy = await db.get(Policy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    await db.delete(policy)
    await db.commit()
