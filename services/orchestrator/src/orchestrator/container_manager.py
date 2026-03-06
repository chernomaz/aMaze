"""
Docker container lifecycle management.

Spawns agent containers with strict isolation:
- Attached only to amaze-agent-net (no direct internet)
- HTTP_PROXY set to the internal proxy (the only egress point)
- Read-only root filesystem with /tmp tmpfs only
- All Linux capabilities dropped
- Per-session workspace bind-mounted from host
"""

import logging
import os
import time

import docker
import docker.errors
from docker.models.containers import Container

logger = logging.getLogger(__name__)

PROXY_URL = os.environ.get("PROXY_INTERNAL_URL", "http://proxy:8080")
AGENT_NETWORK = os.environ.get("AMAZE_DOCKER_NETWORK", "amaze_amaze-agent-net")
WORKSPACE_HOST_PATH = os.environ.get("AGENT_WORKSPACE_HOST_PATH", "/tmp/amaze-workspaces")
WORKSPACE_CONTAINER_PATH = os.environ.get("AGENT_WORKSPACE_CONTAINER_PATH", "/agent-workspaces")


def _docker_client() -> docker.DockerClient:
    return docker.from_env()


def create_workspace(session_id: str) -> str:
    """Create per-session workspace directory inside the orchestrator container.
    Returns the HOST path that will be bind-mounted into the agent container."""
    container_path = f"{WORKSPACE_CONTAINER_PATH}/{session_id}"
    os.makedirs(container_path, exist_ok=True)
    host_path = f"{WORKSPACE_HOST_PATH}/{session_id}"
    return host_path


def spawn_agent_container(
    *,
    session_id: str,
    agent_id: str,
    image: str,
    env_vars: dict[str, str],
    mounts: list[dict],   # list of {host_path, container_path, read_only}
    mem_limit: str,
    cpu_quota: int,
) -> tuple[str, str, str]:
    """
    Spawn a new agent container.

    Returns (container_id, container_name, container_ip).
    """
    workspace_host = create_workspace(session_id)

    volumes: dict[str, dict] = {
        workspace_host: {"bind": "/workspace", "mode": "rw"},
    }
    for m in mounts:
        volumes[m["host_path"]] = {
            "bind": m["container_path"],
            "mode": "ro" if m.get("read_only") else "rw",
        }

    environment = {
        "HTTP_PROXY": PROXY_URL,
        "HTTPS_PROXY": PROXY_URL,
        "NO_PROXY": "",
        "AMAZE_SESSION_ID": session_id,
        "AMAZE_AGENT_ID": agent_id,
        "AMAZE_PROXY_URL": PROXY_URL,
        **env_vars,
    }

    container_name = f"amaze-agent-{session_id[:8]}"

    client = _docker_client()
    container: Container = client.containers.run(
        image=image,
        name=container_name,
        detach=True,
        network=AGENT_NETWORK,
        environment=environment,
        volumes=volumes,
        # ── Security hardening ───────────────────────────────────────────────
        security_opt=["no-new-privileges:true"],
        cap_drop=["ALL"],
        read_only=True,
        tmpfs={"/tmp": "size=256m,noexec,nosuid"},
        mem_limit=mem_limit,
        pids_limit=512,
        cpu_quota=cpu_quota,
        cpu_period=100000,
        # Prevent privilege escalation via setuid binaries
        userns_mode="",
    )

    # Wait briefly and reload to get the assigned IP
    time.sleep(0.3)
    container.reload()

    ip = _get_container_ip(container)
    logger.info(
        "Spawned agent container %s (session=%s ip=%s)", container_name, session_id, ip
    )
    return container.id, container_name, ip


def _get_container_ip(container: Container) -> str:
    networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
    for net_name, net_info in networks.items():
        ip = net_info.get("IPAddress", "")
        if ip:
            return ip
    raise RuntimeError(
        f"Could not determine IP for container {container.name}. "
        f"Networks: {list(networks.keys())}"
    )


def stop_agent_container(container_id: str) -> None:
    """Stop and remove an agent container. Idempotent — does nothing if already gone."""
    client = _docker_client()
    try:
        container = client.containers.get(container_id)
        container.stop(timeout=10)
        container.remove(force=True)
        logger.info("Removed container %s", container_id[:12])
    except docker.errors.NotFound:
        logger.debug("Container %s already removed", container_id[:12])


def get_container_status(container_id: str) -> str:
    """Returns Docker container status string or 'not_found'."""
    client = _docker_client()
    try:
        container = client.containers.get(container_id)
        container.reload()
        return container.status  # running | exited | dead | ...
    except docker.errors.NotFound:
        return "not_found"


def cleanup_workspace(session_id: str) -> None:
    """Remove the session workspace directory."""
    import shutil
    path = f"{WORKSPACE_CONTAINER_PATH}/{session_id}"
    try:
        shutil.rmtree(path, ignore_errors=True)
        logger.info("Cleaned workspace for session %s", session_id)
    except Exception as exc:
        logger.warning("Failed to clean workspace %s: %s", session_id, exc)
