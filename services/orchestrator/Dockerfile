FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml ./
COPY shared/ ./shared/
COPY services/orchestrator/ ./services/orchestrator/

RUN uv sync --package amaze-orchestrator --no-dev

EXPOSE 8001

CMD ["uv", "run", "--package", "amaze-orchestrator", \
     "fastapi", "run", "services/orchestrator/src/orchestrator/main.py", \
     "--host", "0.0.0.0", "--port", "8001"]
