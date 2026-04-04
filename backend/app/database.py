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

    Disposes the old engine and creates a new one. All subsequent calls to
    get_session() and background loop imports of async_session_factory will
    use the new connection. In-flight requests on the old engine will complete
    normally as dispose() waits for connections to be returned.
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
