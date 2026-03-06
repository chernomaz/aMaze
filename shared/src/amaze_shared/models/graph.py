from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amaze_shared.db import Base


class ExecutionGraph(Base):
    __tablename__ = "execution_graphs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    start_step_id: Mapped[int] = mapped_column(Integer, nullable=False)
    on_violation: Mapped[str] = mapped_column(String(10), nullable=False, default="block")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    steps: Mapped[list["ExecutionStep"]] = relationship(
        back_populates="graph",
        cascade="all, delete-orphan",
        order_by="ExecutionStep.step_id",
        lazy="selectin",
    )


class ExecutionStep(Base):
    """
    One node/edge in an ExecutionGraph.

    step_id is the logical step number within the graph (not the DB primary key).
    next_step_ids lists which step_ids are valid to transition to after this step fires.
    An empty next_step_ids means this is a terminal step.

    Per-edge enforcement counters are tracked in Redis:
      session:{session_id}:step:{step_id}:loops   — incremented each time edge fires
      session:{session_id}:step:{step_id}:tokens  — incremented by LLM token usage
    """

    __tablename__ = "execution_steps"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    graph_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_graphs.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # call_type: llm_call | mcp_call | agent_call | terminal
    call_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # callee_id: tool name, agent name, or LLM provider. None means any.
    callee_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # List of step_ids valid after this step. Empty = terminal.
    next_step_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False, default=list)
    # Per-edge enforcement limits
    max_loops: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    token_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)

    graph: Mapped["ExecutionGraph"] = relationship(back_populates="steps")
