from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    name: str
    capability_type: str  # agent | mcp_tool | mcp_server
    version: str = "1.0.0"
    description: str = ""
    internal_host: str
    internal_port: int
    input_schema: dict | None = None
    output_schema: dict | None = None
    tags: list[str] = Field(default_factory=list)
    owner_agent_id: UUID | None = None


class RegistryEntryResponse(BaseModel):
    id: UUID
    name: str
    capability_type: str
    version: str
    description: str
    internal_host: str
    internal_port: int
    input_schema: dict | None
    output_schema: dict | None
    tags: list[str]
    is_healthy: bool
    last_heartbeat: datetime
    registered_at: datetime
    owner_agent_id: UUID | None

    model_config = {"from_attributes": True}


class HeartbeatResponse(BaseModel):
    name: str
    last_heartbeat: datetime
