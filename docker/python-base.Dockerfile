# Shared base layer for all Python services.
# Used as a build ARG in each service Dockerfile:
#   docker build --build-arg SERVICE=registry ...
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Disable Python output buffering and .pyc files
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
