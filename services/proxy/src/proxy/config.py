import os

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
POLICY_ENGINE_URL = os.environ.get("POLICY_ENGINE_URL", "http://policy_engine:8003")
REGISTRY_URL = os.environ.get("REGISTRY_URL", "http://registry:8002")
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8001")

# Virtual hostname agents use to reach aMaze services via the proxy
AMAZE_GATEWAY_HOST = "amaze-gateway"

# Hostnames/IP prefixes that indicate an LLM API call
LLM_API_HOSTS = {
    "api.openai.com",
    "api.anthropic.com",
    "openai.azure.com",
    "api.groq.com",
    "api.together.xyz",
    # Ollama and any OpenAI-compatible local proxy
}

# If host ends with these patterns it's also treated as LLM
LLM_HOST_SUFFIXES = ("openai.com", "anthropic.com", "azure.com")

# Seconds before a missing session_id causes a hard block
# (allows brief window for container startup)
SESSION_LOOKUP_TIMEOUT = 30
