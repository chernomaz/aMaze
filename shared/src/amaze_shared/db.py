import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def get_engine(database_url: str | None = None):
    url = database_url or os.environ["DATABASE_URL"]
    return create_async_engine(url, echo=False, pool_pre_ping=True)


def get_session_factory(database_url: str | None = None) -> async_sessionmaker[AsyncSession]:
    engine = get_engine(database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        yield session
