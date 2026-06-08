"""Tests for the EncryptedString SQLAlchemy TypeDecorator.

The TypeDecorator transparently encrypts on write and decrypts on read so
model code never sees ciphertext. Tests exercise this at two levels:
the raw type machinery and an end-to-end ORM round-trip against a real
PostgreSQL row.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import Integer, Text, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.platform import encryption_service
from app.types import EncryptedString


def test_process_bind_param_encrypts():
    et = EncryptedString()
    bound = et.process_bind_param("plaintext", dialect=None)
    assert bound is not None
    assert bound != "plaintext"
    assert bound.startswith("gAAAA")
    assert encryption_service.decrypt(bound) == "plaintext"


def test_process_bind_param_none_passthrough():
    et = EncryptedString()
    assert et.process_bind_param(None, dialect=None) is None


def test_process_result_value_decrypts():
    et = EncryptedString()
    cipher = encryption_service.encrypt("secret")
    assert et.process_result_value(cipher, dialect=None) == "secret"


def test_process_result_value_none_passthrough():
    et = EncryptedString()
    assert et.process_result_value(None, dialect=None) is None


def test_impl_is_text_for_unbounded_ciphertext():
    # SQLAlchemy instantiates impl on the TypeDecorator instance, so check
    # the class-level declaration and the runtime instance type separately.
    assert EncryptedString.impl is Text
    assert isinstance(EncryptedString().impl, Text)


def test_cache_ok_is_true():
    # cache_ok lets SQLAlchemy reuse compiled statements; without it every
    # query against an EncryptedString column emits a noisy warning.
    assert EncryptedString.cache_ok is True


class _Base(DeclarativeBase):
    pass


class _Secret(_Base):
    __tablename__ = "test_encrypted_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)


@pytest_asyncio.fixture
async def secrets_engine(db_engine):
    """Reuse the per-worker test engine but with an isolated table."""
    async with db_engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    yield db_engine
    async with db_engine.begin() as conn:
        await conn.run_sync(_Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_orm_round_trip_returns_plaintext(secrets_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(secrets_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(_Secret(token="hunter2"))
        await session.commit()

        result = await session.execute(select(_Secret))
        row = result.scalar_one()
        assert row.token == "hunter2"


@pytest.mark.asyncio
async def test_raw_column_is_ciphertext(secrets_engine):
    """Direct SQL must see ciphertext; only ORM decrypts."""
    from sqlalchemy import text as sa_text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(secrets_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(_Secret(token="hunter2"))
        await session.commit()

        raw = (await session.execute(sa_text("SELECT token FROM test_encrypted_secrets LIMIT 1"))).scalar_one()
        assert raw != "hunter2"
        assert raw.startswith("gAAAA")
        assert encryption_service.decrypt(raw) == "hunter2"


@pytest.mark.asyncio
async def test_orm_null_round_trip(secrets_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(secrets_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(_Secret(token=None))
        await session.commit()

        row = (await session.execute(select(_Secret))).scalar_one()
        assert row.token is None
