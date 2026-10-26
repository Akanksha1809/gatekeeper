"""Async SQLAlchemy engine/session management with pooling and startup checks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gatekeeper.settings import Settings

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: Settings):
    global _engine, _sessionmaker
    _engine = create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def check_connectivity() -> bool:
    """Used by the readiness probe. Returns False rather than raising."""
    if _sessionmaker is None:
        return False
    try:
        async with _sessionmaker() as session:
            from sqlalchemy import text

            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Database engine not initialized. Call init_engine() at startup.")
    async with _sessionmaker() as session:
        yield session


async def session_dependency() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency wrapper around get_session()."""
    async with get_session() as session:
        yield session
