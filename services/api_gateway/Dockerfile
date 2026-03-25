FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Root metadata
COPY pyproject.toml ./
COPY uv.lock ./

# Copy ALL workspace member metadata so --locked can validate the workspace
COPY shared/pyproject.toml ./shared/pyproject.toml
COPY services/api_gateway/pyproject.toml ./services/api_gateway/pyproject.toml
COPY services/orchestrator/pyproject.toml ./services/orchestrator/pyproject.toml
COPY services/proxy/pyproject.toml ./services/proxy/pyproject.toml
COPY services/registry/pyproject.toml ./services/registry/pyproject.toml
COPY services/policy_engine/pyproject.toml ./services/policy_engine/pyproject.toml
COPY agent_runtime/pyproject.toml ./agent_runtime/pyproject.toml
COPY mcp_runtime/pyproject.toml ./mcp_runtime/pyproject.toml

# First sync: install dependencies only (no source code yet)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package amaze-api-gateway --no-dev --no-install-project

# Copy only the code needed for this service
COPY shared/ ./shared/
COPY services/api_gateway/ ./services/api_gateway/

# Final sync: install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package amaze-api-gateway --no-dev

EXPOSE 8000

CMD ["/app/.venv/bin/fastapi", "run", "services/api_gateway/src/api_gateway/main.py", "--host", "0.0.0.0", "--port", "8000"]
