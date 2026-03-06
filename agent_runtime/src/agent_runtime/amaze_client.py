"""
AmazeClient — optional helper for agent-to-agent calls and registry discovery.

All HTTP calls go through HTTP_PROXY automatically (set by the Orchestrator).
The proxy enforces graph step validation and policy limits transparently.

Usage:
    from agent_runtime import AmazeClient

    amaze = AmazeClient()
    result = amaze.call_agent("summarizer-agent", task="Summarize this text", context={})
    tools = amaze.list_tools(tag="filesystem")
"""

import os

import httpx


class AmazeClient:
    def __init__(self) -> None:
        self._session_id = os.environ.get("AMAZE_SESSION_ID", "")
        self._agent_id = os.environ.get("AMAZE_AGENT_ID", "")
        proxy_url = os.environ.get("AMAZE_PROXY_URL", "http://proxy:8080")

        # All requests go through the proxy — policy and graph enforcement happen there
        self._client = httpx.Client(
            proxy=proxy_url,
            headers={
                "X-Amaze-Session-ID": self._session_id,
                "X-Amaze-Agent-ID": self._agent_id,
                "X-Amaze-Call-Type": "agent_call",
            },
            timeout=300,
        )

    def call_agent(
        self,
        agent_name: str,
        *,
        task: str,
        context: dict | None = None,
        input_files: list[dict] | None = None,
        timeout_seconds: int = 300,
    ) -> dict:
        """
        Invoke another registered agent by name.

        The proxy resolves the agent's internal address via the Registry and
        enforces graph step ordering before forwarding the request.
        """
        payload = {
            "task": task,
            "context": context or {},
            "input_files": input_files or [],
            "timeout_seconds": timeout_seconds,
        }
        # The proxy intercepts this URL pattern and routes to the correct agent container
        resp = self._client.post(
            f"http://amaze-gateway/agents/{agent_name}/invoke",
            json=payload,
            headers={"X-Amaze-Call-Type": "agent_call", "X-Amaze-Callee-ID": agent_name},
        )
        resp.raise_for_status()
        return resp.json()

    def list_tools(self, *, tag: str | None = None, capability_type: str = "mcp_tool") -> list[dict]:
        """Discover registered MCP tools via the Registry (proxied)."""
        params: dict = {"capability_type": capability_type}
        if tag:
            params["tag"] = tag
        resp = self._client.get(
            "http://amaze-gateway/registry/capabilities",
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    def declare_next_step(self, step_id: int) -> None:
        """
        For branching graphs: declare which next step this agent intends to take.
        Must be called before making the outbound call for that step.
        Sets X-Amaze-Next-Step header on subsequent requests in this session.
        """
        self._client.headers["X-Amaze-Next-Step"] = str(step_id)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AmazeClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()
