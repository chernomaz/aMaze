"""
Registry client for MCP server containers.

Called automatically by bootstrap.py on container startup to register
this MCP server's capabilities with the aMaze Registry.
"""

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

# The proxy exposes the registry at this URL (routed from amaze-mcp-net)
REGISTRY_URL = os.environ.get("AMAZE_REGISTRY_URL", "http://proxy:8080/registry")


class RegistryClient:
    def __init__(
        self,
        name: str,
        capability_type: str,
        description: str,
        internal_host: str,
        internal_port: int,
        version: str = "1.0.0",
        tags: list[str] | None = None,
        input_schema: dict | None = None,
        output_schema: dict | None = None,
    ) -> None:
        self.name = name
        self.payload = {
            "name": name,
            "capability_type": capability_type,
            "version": version,
            "description": description,
            "internal_host": internal_host,
            "internal_port": internal_port,
            "tags": tags or [],
            "input_schema": input_schema,
            "output_schema": output_schema,
        }
        self._registry_url = REGISTRY_URL

    def register(self, retries: int = 5, backoff: float = 2.0) -> None:
        """Register with the Registry. Retries on failure (registry may not be up yet)."""
        for attempt in range(1, retries + 1):
            try:
                resp = httpx.post(
                    f"{self._registry_url}/register",
                    json=self.payload,
                    timeout=10,
                )
                resp.raise_for_status()
                logger.info("Registered '%s' with aMaze Registry", self.name)
                return
            except Exception as exc:
                logger.warning(
                    "Registry registration attempt %d/%d failed: %s", attempt, retries, exc
                )
                if attempt < retries:
                    time.sleep(backoff * attempt)

        logger.error("Failed to register '%s' after %d attempts", self.name, retries)

    def heartbeat(self) -> None:
        """Send a heartbeat to keep the entry marked healthy."""
        try:
            httpx.post(
                f"{self._registry_url}/heartbeat/{self.name}",
                timeout=5,
            )
        except Exception as exc:
            logger.warning("Heartbeat failed for '%s': %s", self.name, exc)

    def start_heartbeat_loop(self, interval: int = 30) -> None:
        """Run heartbeat in a background thread."""
        import threading

        def _loop():
            while True:
                time.sleep(interval)
                self.heartbeat()

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        logger.debug("Started heartbeat loop for '%s' (interval=%ds)", self.name, interval)
