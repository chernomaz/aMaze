"""
RequestClassifier addon.

Inspects the request host + path to determine call_type and callee_id.
Stores results in flow.metadata for downstream addons.

call_type values:
  llm_call    — request to an LLM API (OpenAI-format, Ollama, etc.)
  mcp_call    — request to http://amaze-gateway/mcp/{tool_name}
  agent_call  — request to http://amaze-gateway/agents/{name}/invoke
  registry    — request to http://amaze-gateway/registry/... (pass-through)
"""

import logging

from mitmproxy import http

from proxy.config import AMAZE_GATEWAY_HOST, LLM_API_HOSTS, LLM_HOST_SUFFIXES

logger = logging.getLogger(__name__)


def _is_llm_host(host: str) -> bool:
    if host in LLM_API_HOSTS:
        return True
    return any(host.endswith(s) for s in LLM_HOST_SUFFIXES)


class RequestClassifier:
    def request(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get("blocked"):
            return

        host = flow.request.pretty_host
        path = flow.request.path

        # --- LLM API call ---
        if _is_llm_host(host):
            flow.metadata["call_type"] = "llm_call"
            flow.metadata["callee_id"] = host  # e.g. "api.openai.com"

            # Detect LLM provider name for policy enforcement
            if "openai" in host:
                flow.metadata["llm_provider"] = "openai"
            elif "anthropic" in host:
                flow.metadata["llm_provider"] = "anthropic"
            elif "groq" in host:
                flow.metadata["llm_provider"] = "groq"
            else:
                flow.metadata["llm_provider"] = host
            return

        # Ollama / local LLM (HTTP, identified by path)
        if path.startswith("/api/chat") or path.startswith("/api/generate") or path.startswith("/v1/"):
            # Could be Ollama or any local OpenAI-compatible server
            flow.metadata["call_type"] = "llm_call"
            flow.metadata["callee_id"] = host
            flow.metadata["llm_provider"] = "ollama"
            return

        # --- amaze-gateway virtual routes ---
        if host == AMAZE_GATEWAY_HOST:
            if path.startswith("/mcp/"):
                # /mcp/filesystem-mcp.read_file → mcp_call
                tool_name = path[len("/mcp/"):].split("?")[0]
                flow.metadata["call_type"] = "mcp_call"
                flow.metadata["callee_id"] = tool_name
                flow.metadata["tool_name"] = tool_name
                return

            if path.startswith("/agents/") and path.endswith("/invoke"):
                # /agents/summarizer-agent/invoke → agent_call
                parts = path.strip("/").split("/")
                agent_name = parts[1] if len(parts) >= 3 else "unknown"
                flow.metadata["call_type"] = "agent_call"
                flow.metadata["callee_id"] = agent_name
                return

            if path.startswith("/registry/"):
                flow.metadata["call_type"] = "registry"
                return

        # Unknown destination — log and allow (don't block unknown traffic silently)
        logger.debug("Unclassified request: %s %s", host, path)
        flow.metadata["call_type"] = "unknown"
