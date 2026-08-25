from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session, session.begin():
            yield session

    async def create_schema_for_tests(self) -> None:
        from app.persistence.models import Base

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()
