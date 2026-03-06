"""
aMaze Proxy — main entry point.

Runs mitmproxy in regular proxy mode on port 8080. All agent and MCP
containers route outbound HTTP/HTTPS traffic through this proxy via
HTTP_PROXY environment variable.

Addon pipeline (executed in order on every request):
  1. SessionIdentifier  — map source IP → session_id from Redis
  2. RequestClassifier  — detect call_type (llm_call / mcp_call / agent_call)
  3. GraphEnforcer      — validate + advance execution graph step
  4. PolicyEnforcer     — check token/loop/tool policy limits
  5. UpstreamRouter     — rewrite virtual amaze-gateway URLs to real upstreams

Response pipeline:
  6. TokenCounter       — extract usage from LLM responses, update Redis
  7. EventEmitter       — publish structured events to Redis pub/sub
"""

import asyncio
import logging
import signal
import sys

from mitmproxy.options import Options
from mitmproxy.tools.dump import DumpMaster

from proxy.addons.classifier import RequestClassifier
from proxy.addons.event_emitter import EventEmitter
from proxy.addons.graph_enforcer import GraphEnforcer
from proxy.addons.policy_enforcer import PolicyEnforcer
from proxy.addons.router import UpstreamRouter
from proxy.addons.session_id import SessionIdentifier
from proxy.addons.token_counter import TokenCounter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def amain() -> None:
    opts = Options(
        listen_host="0.0.0.0",
        listen_port=8080,
        # Regular proxy mode — agents set HTTP_PROXY=http://proxy:8080
        mode=["regular"],
        # SSL interception: mitmproxy auto-generates a CA cert at startup.
        # For HTTPS LLM APIs, agents need to trust this CA cert.
        # In dev, set ssl_insecure=True on the agent's httpx client to skip verification.
        ssl_insecure=False,
    )

    master = DumpMaster(opts, with_termlog=False, with_dumper=False)

    master.addons.add(SessionIdentifier())
    master.addons.add(RequestClassifier())
    master.addons.add(GraphEnforcer())
    master.addons.add(PolicyEnforcer())
    master.addons.add(UpstreamRouter())
    master.addons.add(TokenCounter())
    master.addons.add(EventEmitter())

    def _shutdown(sig, frame):
        logger.info("Shutting down proxy (signal %s)", sig)
        master.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("aMaze proxy starting on 0.0.0.0:8080")
    await master.run()


if __name__ == "__main__":
    asyncio.run(amain())
