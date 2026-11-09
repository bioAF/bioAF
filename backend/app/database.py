import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger("bioaf.database")

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=5,
    max_overflow=10,
)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def swap_database(new_url: str) -> None:
    """Swap the connection pool to a different database URL.

    Creates a new engine and factory, then disposes the old engine. Code that reads
    ``app.database.async_session_factory`` after the swap (get_session(), anything that resolves
    the factory per call) uses the new database. Holders of the old factory (periodic jobs that
    bound it when they started, factories injected at startup) keep the old engine.

    ``dispose()`` closes only the old pool's checked-in connections. It does not wait for
    checked-out connections, which finish on the old engine and are then pooled there again, and
    it does not stop a holder of the old factory from opening new ones.
    """
    global engine, async_session_factory
    old_engine = engine
    logger.info("Swapping database connection to %s", new_url.split("@")[-1])
    engine = create_async_engine(
        new_url,
        echo=settings.debug,
        pool_size=5,
        max_overflow=10,
    )
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await old_engine.dispose()
