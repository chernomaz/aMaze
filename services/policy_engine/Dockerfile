FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml ./
COPY shared/ ./shared/
COPY services/policy_engine/ ./services/policy_engine/

RUN uv sync --package amaze-policy-engine --no-dev

EXPOSE 8003

CMD ["uv", "run", "--package", "amaze-policy-engine", \
     "fastapi", "run", "services/policy_engine/src/policy_engine/main.py", \
     "--host", "0.0.0.0", "--port", "8003"]
