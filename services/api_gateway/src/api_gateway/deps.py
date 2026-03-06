"""Shared FastAPI dependencies."""

import os
from typing import Annotated, AsyncGenerator

import redis.asyncio as aioredis
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)

redis_pool = aioredis.ConnectionPool.from_url(
    os.environ.get("REDIS_URL", "redis://redis:6379"),
    decode_responses=True,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionFactory() as session:
        yield session


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    client = aioredis.Redis(connection_pool=redis_pool)
    try:
        yield client
    finally:
        await client.aclose()


DB = Annotated[AsyncSession, Depends(get_db)]
Redis = Annotated[aioredis.Redis, Depends(get_redis)]
