"""
Redis pub/sub event schemas.

All events are published to channel: session:{session_id}:events
The API Gateway subscribes and relays them over WebSocket to the UI.
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from datetime import datetime


class BaseEvent(BaseModel):
    session_id: UUID
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    step_id: int | None = None


class LLMCallEvent(BaseEvent):
    event_type: Literal["llm_call"] = "llm_call"
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class MCPCallEvent(BaseEvent):
    event_type: Literal["mcp_call"] = "mcp_call"
    tool_name: str
    mcp_server: str
    success: bool


class AgentCallEvent(BaseEvent):
    event_type: Literal["agent_call"] = "agent_call"
    target_agent: str
    child_session_id: UUID | None = None


class PolicyViolationEvent(BaseEvent):
    event_type: Literal["policy_violation"] = "policy_violation"
    violation_type: str  # token_budget | loop_limit | tool_not_allowed
    limit: int | None = None
    current: int | None = None
    reason: str


class GraphViolationEvent(BaseEvent):
    event_type: Literal["graph_violation"] = "graph_violation"
    expected_call_type: str
    expected_callee_id: str | None
    got_call_type: str
    got_callee_id: str | None


class EdgeLoopExceededEvent(BaseEvent):
    event_type: Literal["edge_loop_exceeded"] = "edge_loop_exceeded"
    limit: int
    current: int


class EdgeTokenCapExceededEvent(BaseEvent):
    event_type: Literal["edge_token_cap_exceeded"] = "edge_token_cap_exceeded"
    cap: int
    current: int


class OutputEvent(BaseEvent):
    event_type: Literal["output"] = "output"
    output: str


class StatusChangeEvent(BaseEvent):
    event_type: Literal["status_change"] = "status_change"
    old_status: str
    new_status: str


class StepAdvancedEvent(BaseEvent):
    event_type: Literal["step_advanced"] = "step_advanced"
    from_step_id: int
    to_step_id: int
    loops_on_step: int
    tokens_on_step: int


AnyEvent = (
    LLMCallEvent
    | MCPCallEvent
    | AgentCallEvent
    | PolicyViolationEvent
    | GraphViolationEvent
    | EdgeLoopExceededEvent
    | EdgeTokenCapExceededEvent
    | OutputEvent
    | StatusChangeEvent
    | StepAdvancedEvent
)
