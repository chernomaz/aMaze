"""
Bootstrap helpers for agent containers.

Reads environment variables injected by the Orchestrator and validates that the
proxy is configured. Import at the top of any agent's main.py to ensure the
runtime is correctly configured before any HTTP calls are made.

Usage:
    from agent_runtime.bootstrap import session_id, agent_id, proxy_url
"""

import os
import sys


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"[amaze-runtime] ERROR: Required environment variable '{name}' is not set. "
            "This container must be launched by the aMaze Orchestrator.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


# These are set by the Orchestrator at container launch
session_id: str = _require_env("AMAZE_SESSION_ID")
agent_id: str = _require_env("AMAZE_AGENT_ID")
proxy_url: str = _require_env("AMAZE_PROXY_URL")

# Verify HTTP_PROXY is set (used transparently by httpx and requests)
http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
if not http_proxy:
    print(
        "[amaze-runtime] WARNING: HTTP_PROXY is not set. "
        "Outbound HTTP calls will bypass the aMaze policy gateway.",
        file=sys.stderr,
    )
