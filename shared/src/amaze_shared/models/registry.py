from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from amaze_shared.db import Base


class CapabilityType(str, Enum):
    AGENT = "agent"
    MCP_TOOL = "mcp_tool"
    MCP_SERVER = "mcp_server"


class RegistryEntry(Base):
    __tablename__ = "registry_entries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    capability_type: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0.0")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Internal Docker DNS address — only reachable from within platform networks
    internal_host: Mapped[str] = mapped_column(String(255), nullable=False)
    internal_port: Mapped[int] = mapped_column(Integer, nullable=False)

    # JSON Schema for tool inputs/outputs (optional)
    input_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    is_healthy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_heartbeat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Set when this capability is owned by a running agent session
    owner_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
