FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml ./
COPY services/proxy/ ./services/proxy/

RUN uv sync --package amaze-proxy --no-dev

EXPOSE 8080

CMD ["uv", "run", "--package", "amaze-proxy", \
     "python", "-m", "proxy.main"]
