from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from amaze_shared.db import Base


class ToolPermission(BaseModel):
    """Pydantic schema for individual tool permission entries stored in Policy.allowed_tools."""

    tool_name: str
    allowed: bool = True
    params_allowlist: dict | None = None


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Token limits
    max_tokens_per_conversation: Mapped[int] = mapped_column(Integer, nullable=False, default=100000)
    max_tokens_per_turn: Mapped[int] = mapped_column(Integer, nullable=False, default=10000)

    # Loop / call limits
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    max_agent_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    max_mcp_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    # Tool and provider allowlists
    # Stored as list of ToolPermission dicts: [{"tool_name": "...", "allowed": true, ...}]
    allowed_tools: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    allowed_llm_providers: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    allowed_mcp_servers: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )

    # Enforcement modes
    on_budget_exceeded: Mapped[str] = mapped_column(String(10), nullable=False, default="block")
    on_loop_exceeded: Mapped[str] = mapped_column(String(10), nullable=False, default="block")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
