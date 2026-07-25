"""S9.1 addon: framework-agnostic filter on LLM request tool arrays.

The `tool_list_filter` addon (v0.9) hides disallowed tools from an agent
at the MCP boundary — but if the agent's runtime cached the tool schema
before the policy tightened (or ignores `notifications/tools/list_changed`
entirely), the LLM will still receive a `tools[]` array containing
disallowed entries in the outbound chat-completion / messages request.
Every LangChain/LangGraph runtime today has this shape.

This addon closes that gap at the LLM boundary: on any outbound request
to a known LLM provider, parse the `tools[]` array in the JSON body and
drop entries whose name is not in `policy.allowed_tools`. Also patch
`tool_choice` if it points at a stripped tool, otherwise the provider
would 400 the request.

Provider abstraction
--------------------
The wire formats differ by provider — LiteLLM (the codebase's "unified
LLM interface" via config/litellm.yaml) has one-way OpenAI→provider
transformers but does NOT expose inverse ones we could use to normalize
outbound provider-native bodies back to a common shape. So we abstract
per-provider ourselves via `_PROVIDER_SHAPES`: keyed by canonical
provider name (matches `host_to_provider` in policy.py), each entry has
four small lambdas that fully describe how tools live in that provider's
request body. Adding a new provider = one entry, no new filter function.

Providers today (per `LLM_HOSTS` in `services/proxy/policy.py`):

- OpenAI chat/completions
    tools: [{"type": "function",
             "function": {"name": "<n>", "description": ..., "parameters": ...}}]
    tool_choice: "auto" | "none" | "required" |
                 {"type": "function", "function": {"name": "<n>"}}
- Anthropic messages
    tools: [{"name": "<n>", "description": ..., "input_schema": ...}]
    tool_choice: {"type": "auto"} | {"type": "any"} | {"type": "none"} |
                 {"type": "tool", "name": "<n>"}

Chain position: right after StreamBlocker (which mutates the LLM request
body to inject `stream: false`), before ToolListFilter (which only
touches MCP responses). Both are LLM-request mutators; running them
adjacent keeps the request body munging localized.

Fail-closed: on any Redis error while fetching the policy, deny 503
`redis-unavailable` — an attacker who DoSes Redis must not be able to
leak the full tool surface at the LLM boundary. Consistent with
enforcer.py and tool_list_filter.py.

Audit: sets `flow.metadata["amaze_llm_tools_stripped"] = {"before":N,
"after":M}` on the flow; `audit_log.py` serializes this into an
`llm_tools_stripped` field on the audit record so the trace UI can
render a chip identical in shape to `tools_filtered` for MCP tools/list.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

import redis.asyncio.client as redis
from mitmproxy import http

from services.proxy.deny import deny
from services.proxy import policy_store
from services.proxy.policy import host_to_provider, is_llm_host

logger = logging.getLogger("amaze.proxy.llm_tool_stripper")


@dataclass(frozen=True)
class _ProviderShape:
    """How one LLM provider represents tools in a request body.

    - `name_of(entry)` extracts the tool name from one item in `body.tools`.
    - `pin_of(tc)` returns the tool name a specific `tool_choice` pins at,
      or None if the choice doesn't pin a specific tool.
    - `downgrade_pin` is the tool_choice value we substitute when the
      pinned tool was stripped ("auto"-equivalent for the provider).
    """
    name_of: Callable[[dict], str | None]
    pin_of:  Callable[[Any], str | None]
    downgrade_pin: Any


_openai = _ProviderShape(
    name_of=lambda e: (
        e.get("function", {}).get("name")
        if isinstance(e, dict) and isinstance(e.get("function"), dict)
        else None
    ),
    pin_of=lambda tc: (
        tc.get("function", {}).get("name")
        if isinstance(tc, dict) and tc.get("type") == "function"
        and isinstance(tc.get("function"), dict)
        else None
    ),
    downgrade_pin="auto",
)


_anthropic = _ProviderShape(
    name_of=lambda e: e.get("name") if isinstance(e, dict) else None,
    pin_of=lambda tc: (
        tc.get("name")
        if isinstance(tc, dict) and tc.get("type") == "tool"
        else None
    ),
    downgrade_pin={"type": "auto"},
)


_PROVIDER_SHAPES: dict[str, _ProviderShape] = {
    "openai":    _openai,
    "anthropic": _anthropic,
}


def _filter_body(
    body: dict, allowed: set[str], shape: _ProviderShape,
) -> tuple[dict, int, int]:
    """Return (rewritten_body, before, after).

    - Drops any entry whose `shape.name_of(entry)` is not in `allowed`.
    - If tool_choice pins a stripped tool, downgrades it via
      `shape.downgrade_pin`.
    - If `tools` becomes empty, drops both `tools` and `tool_choice`
      (providers 400 on non-"none" tool_choice with empty tools).
    - If nothing changes, returns the original body (no allocation).
    """
    tools = body.get("tools")
    if not isinstance(tools, list):
        return body, 0, 0
    before = len(tools)
    kept = [e for e in tools if shape.name_of(e) in allowed]
    after = len(kept)
    if after == before:
        return body, before, after

    new_body = dict(body)
    if not kept:
        new_body.pop("tools", None)
        new_body.pop("tool_choice", None)
    else:
        new_body["tools"] = kept
        pin = shape.pin_of(body.get("tool_choice"))
        if pin is not None and pin not in allowed:
            new_body["tool_choice"] = shape.downgrade_pin
    return new_body, before, after


class LLMToolStripper:
    """See module docstring."""

    async def request(self, flow: http.HTTPFlow) -> None:
        if flow.response is not None:
            return
        if flow.metadata.get("amaze_bypass"):
            return
        # Only LLM POSTs carry a tools[] we care about.
        if flow.metadata.get("amaze_kind") != "llm":
            return
        if flow.request.method != "POST":
            return

        host = flow.request.pretty_host
        if not is_llm_host(host):
            return
        provider = host_to_provider(host)
        if provider is None:
            return
        shape = _PROVIDER_SHAPES.get(provider)
        if shape is None:
            # Known LLM host, but no wire-format registered yet. Log
            # once at warning so an operator notices, then pass through.
            logger.warning(
                "llm_tool_stripper: provider=%s host=%s has no registered "
                "shape — pass-through (add an entry to _PROVIDER_SHAPES)",
                provider, host,
            )
            return

        raw = flow.request.content or b""
        if not raw:
            return
        try:
            body = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(body, dict):
            return

        agent_id = flow.metadata.get("amaze_agent")
        if not agent_id:
            return

        try:
            policy = await policy_store.get_policy(agent_id)
        except redis.RedisError as exc:
            logger.error(
                "llm_tool_stripper: policy fetch failed agent=%s: %s — "
                "denying LLM request (fail-closed)", agent_id, exc,
            )
            deny(flow, "redis-unavailable", status=503)
            return

        if policy is None:
            # No policy row — pass-through, matching enforcer.py's
            # discovery pass-through for unpolicied agents.
            return

        allowed = set(policy.allowed_tools)
        new_body, before, after = _filter_body(body, allowed, shape)
        if before == 0:
            return  # request carried no tools to strip
        if before != after:
            flow.request.content = json.dumps(
                new_body, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")
            # Content-Length is refreshed automatically by mitmproxy when
            # `flow.request.content` is reassigned.
            logger.info(
                "llm_tool_stripper: agent=%s provider=%s stripped %d → %d tools",
                agent_id, provider, before, after,
            )
        flow.metadata["amaze_llm_tools_stripped"] = {
            "before": before, "after": after,
        }
