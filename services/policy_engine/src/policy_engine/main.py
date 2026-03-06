"""
Policy Engine — stateless policy evaluation.

Receives the full policy object + current session counters from the proxy,
evaluates all rules, and returns allow/block/warn. Never reads from DB or Redis.
"""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="aMaze Policy Engine", version="0.1.0")


# ─── Request / Response schemas ───────────────────────────────────────────────


class PolicyData(BaseModel):
    max_tokens_per_conversation: int
    max_tokens_per_turn: int
    max_iterations: int
    max_agent_calls: int
    max_mcp_calls: int
    allowed_tools: list[dict]          # list of {tool_name, allowed, params_allowlist}
    allowed_llm_providers: list[str]
    allowed_mcp_servers: list[str]
    on_budget_exceeded: Literal["block", "warn"]
    on_loop_exceeded: Literal["block", "warn"]


class SessionCounters(BaseModel):
    tokens_used: int = 0
    tokens_this_turn: int = 0          # tokens in the current request being evaluated
    iterations_completed: int = 0
    mcp_calls_made: int = 0
    agent_calls_made: int = 0


class EvaluateRequest(BaseModel):
    policy: PolicyData
    request_type: Literal["llm_call", "mcp_call", "agent_call"]
    tool_name: str | None = None       # MCP tool name or agent name
    provider: str | None = None        # LLM provider (openai, ollama, etc.)
    estimated_tokens: int | None = None
    current_counters: SessionCounters


class EvaluateResponse(BaseModel):
    decision: Literal["allow", "block", "warn"]
    reason: str | None = None
    violation_type: str | None = None  # token_budget | loop_limit | tool_not_allowed |
                                       # provider_not_allowed | mcp_server_not_allowed


# ─── Evaluators ───────────────────────────────────────────────────────────────


def _check_token_budget(req: EvaluateRequest) -> EvaluateResponse | None:
    p = req.policy
    c = req.current_counters
    est = req.estimated_tokens or 0

    if c.tokens_used + est > p.max_tokens_per_conversation:
        return EvaluateResponse(
            decision=p.on_budget_exceeded,
            reason=(
                f"Conversation token budget exceeded: "
                f"{c.tokens_used + est} > {p.max_tokens_per_conversation}"
            ),
            violation_type="token_budget",
        )

    if req.request_type == "llm_call" and est > p.max_tokens_per_turn:
        return EvaluateResponse(
            decision=p.on_budget_exceeded,
            reason=f"Per-turn token limit exceeded: {est} > {p.max_tokens_per_turn}",
            violation_type="token_budget",
        )

    return None


def _check_loop_limits(req: EvaluateRequest) -> EvaluateResponse | None:
    p = req.policy
    c = req.current_counters

    if c.iterations_completed >= p.max_iterations:
        return EvaluateResponse(
            decision=p.on_loop_exceeded,
            reason=f"Max iterations reached: {c.iterations_completed} >= {p.max_iterations}",
            violation_type="loop_limit",
        )

    if req.request_type == "mcp_call" and c.mcp_calls_made >= p.max_mcp_calls:
        return EvaluateResponse(
            decision=p.on_loop_exceeded,
            reason=f"Max MCP calls reached: {c.mcp_calls_made} >= {p.max_mcp_calls}",
            violation_type="loop_limit",
        )

    if req.request_type == "agent_call" and c.agent_calls_made >= p.max_agent_calls:
        return EvaluateResponse(
            decision=p.on_loop_exceeded,
            reason=f"Max agent calls reached: {c.agent_calls_made} >= {p.max_agent_calls}",
            violation_type="loop_limit",
        )

    return None


def _check_provider_allowlist(req: EvaluateRequest) -> EvaluateResponse | None:
    if req.request_type != "llm_call":
        return None
    if not req.policy.allowed_llm_providers:
        return None  # empty list = allow all
    provider = req.provider or ""
    if provider not in req.policy.allowed_llm_providers:
        return EvaluateResponse(
            decision="block",
            reason=f"LLM provider '{provider}' not in allowlist",
            violation_type="provider_not_allowed",
        )
    return None


def _check_mcp_server_allowlist(req: EvaluateRequest) -> EvaluateResponse | None:
    if req.request_type != "mcp_call":
        return None
    if not req.policy.allowed_mcp_servers:
        return None  # empty list = allow all
    tool = req.tool_name or ""
    # tool_name format: "server-name.tool-name" — extract server prefix
    server = tool.split(".")[0] if "." in tool else tool
    if server not in req.policy.allowed_mcp_servers:
        return EvaluateResponse(
            decision="block",
            reason=f"MCP server '{server}' not in allowlist",
            violation_type="mcp_server_not_allowed",
        )
    return None


def _check_tool_allowlist(req: EvaluateRequest) -> EvaluateResponse | None:
    if not req.policy.allowed_tools:
        return None  # empty list = allow all tools
    tool = req.tool_name
    if not tool:
        return None

    for entry in req.policy.allowed_tools:
        if entry.get("tool_name") == tool:
            if not entry.get("allowed", True):
                return EvaluateResponse(
                    decision="block",
                    reason=f"Tool '{tool}' is explicitly denied",
                    violation_type="tool_not_allowed",
                )
            return None  # found and allowed

    # Tool not listed — default allow (opt-in deny model via allowed_tools entries)
    return None


# ─── Main endpoint ────────────────────────────────────────────────────────────


@app.post("/evaluate", response_model=EvaluateResponse)
async def evaluate(req: EvaluateRequest) -> EvaluateResponse:
    # Run all evaluators in priority order; first violation wins
    evaluators = [
        _check_provider_allowlist,
        _check_mcp_server_allowlist,
        _check_tool_allowlist,
        _check_token_budget,
        _check_loop_limits,
    ]
    for evaluator in evaluators:
        result = evaluator(req)
        if result is not None:
            return result

    return EvaluateResponse(decision="allow")


@app.get("/health")
async def health():
    return {"status": "ok"}
