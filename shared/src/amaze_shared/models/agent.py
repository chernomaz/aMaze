from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amaze_shared.db import Base


class AgentStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


class AgentDefinition(Base):
    __tablename__ = "agents"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image: Mapped[str] = mapped_column(String(500), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="latest")
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    required_capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    env_vars: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    secret_refs: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AgentStatus.DRAFT)
    policy_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("policies.id", ondelete="SET NULL"), nullable=True
    )
    mem_limit: Mapped[str] = mapped_column(String(20), nullable=False, default="2g")
    cpu_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=100000)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    mounts: Mapped[list["AgentFilesystemMount"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan", lazy="selectin"
    )


class AgentFilesystemMount(Base):
    __tablename__ = "agent_mounts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    host_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    container_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    agent: Mapped["AgentDefinition"] = relationship(back_populates="mounts")
