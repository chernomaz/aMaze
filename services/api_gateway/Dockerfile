FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml ./
COPY shared/ ./shared/
COPY services/api_gateway/ ./services/api_gateway/

RUN uv sync --package amaze-api-gateway --no-dev

EXPOSE 8000

CMD ["uv", "run", "--package", "amaze-api-gateway", \
     "fastapi", "run", "services/api_gateway/src/api_gateway/main.py", \
     "--host", "0.0.0.0", "--port", "8000"]
