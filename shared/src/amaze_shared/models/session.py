from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from amaze_shared.db import Base


class SessionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    policy_id: Mapped[UUID] = mapped_column(
        ForeignKey("policies.id", ondelete="RESTRICT"), nullable=False
    )
    execution_graph_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("execution_graphs.id", ondelete="SET NULL"), nullable=True
    )
    container_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    container_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=SessionStatus.PENDING)
    initial_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    final_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Runtime counters — mirrored in Redis for live access, flushed to DB on session end
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    iterations_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mcp_calls_made: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_calls_made: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SessionEvent(Base):
    """
    Immutable event log for a session. Also published to Redis pub/sub in real time.
    event_type: llm_call | mcp_call | agent_call | policy_violation | graph_violation |
                edge_loop_exceeded | edge_token_cap_exceeded | output | status_change
    """

    __tablename__ = "session_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    tokens_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    step_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
