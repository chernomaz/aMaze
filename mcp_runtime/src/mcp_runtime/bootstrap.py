"""
MCP container bootstrap.

Call auto_register() at the top of your MCP server's main() to self-register
all capabilities and start the heartbeat loop.

Usage:
    from mcp_runtime.bootstrap import auto_register

    auto_register(
        capabilities=[
            {
                "name": "filesystem-mcp.read_file",
                "capability_type": "mcp_tool",
                "description": "Read a file",
                "tags": ["filesystem"],
            },
        ]
    )
"""

import logging
import os
import sys

from mcp_runtime.registry_client import RegistryClient

logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"[mcp-runtime] ERROR: Required env var '{name}' is not set.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def auto_register(capabilities: list[dict]) -> list[RegistryClient]:
    """
    Register all capabilities for this MCP server.

    Each entry in capabilities is passed to RegistryClient. The internal_host
    and internal_port default to the MCP_SERVER_HOST / MCP_SERVER_PORT env vars.
    """
    host = _require_env("MCP_SERVER_HOST")
    port = int(_require_env("MCP_SERVER_PORT"))

    clients = []
    for cap in capabilities:
        client = RegistryClient(
            name=cap["name"],
            capability_type=cap.get("capability_type", "mcp_tool"),
            description=cap.get("description", ""),
            internal_host=cap.get("internal_host", host),
            internal_port=cap.get("internal_port", port),
            version=cap.get("version", "1.0.0"),
            tags=cap.get("tags", []),
            input_schema=cap.get("input_schema"),
            output_schema=cap.get("output_schema"),
        )
        client.register()
        client.start_heartbeat_loop()
        clients.append(client)

    return clients
