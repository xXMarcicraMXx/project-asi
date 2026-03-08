"""
Async SQLAlchemy session factory using asyncpg.
DATABASE_URL must use the postgresql+asyncpg:// scheme.
"""

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from db.models import Base

DATABASE_URL = os.environ["DATABASE_URL"]

# NullPool is safer for short-lived CLI/script runs;
# swap to AsyncAdaptedQueuePool in the long-running scheduler service.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncSession:
    """Dependency-style session getter for use in agent calls."""
    async with AsyncSessionLocal() as session:
        yield session


async def create_tables() -> None:
    """Create all tables — used only in tests or initial setup scripts."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
