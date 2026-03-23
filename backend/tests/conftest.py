import os
from contextlib import asynccontextmanager

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text as sa_text

from app.adapters import registry as adapter_registry
from app.services.auth_service import AuthService

# Ensure adapters run in local/mock mode for all tests
os.environ.setdefault("BIOAF_COMPUTE_MODE", "local")

TEST_DATABASE_URL = os.environ.get(
    "BIOAF_TEST_DATABASE_URL",
    "postgresql+asyncpg://bioaf_app:devpassword@localhost:5432/bioaf_test",
)


def _worker_schema(worker_id: str) -> str:
    """Return a per-worker schema name for pytest-xdist isolation."""
    if worker_id == "master":
        return "public"
    return f"test_{worker_id}"


@pytest_asyncio.fixture(autouse=True)
async def _init_adapter_registry():
    """Initialize the BAL adapter registry for all tests (local/mock mode)."""
    adapter_registry.initialize_adapters_sync("kubernetes")
    yield
    adapter_registry.reset_registry()


@pytest_asyncio.fixture
async def db_engine(worker_id):
    """Create engine, set up tables in a per-worker schema, yield, tear down."""
    import app.models  # noqa: F401 -- register all models with Base.metadata
    from app.database import Base

    schema = _worker_schema(worker_id)

    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    if schema != "public":
        async with engine.begin() as conn:
            await conn.execute(sa_text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            await conn.execute(sa_text(f"SET search_path TO {schema}"))
            await conn.run_sync(Base.metadata.create_all)
    else:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    # Set search_path for all connections from this engine
    if schema != "public":
        from sqlalchemy import event

        @event.listens_for(engine.sync_engine, "connect")
        def set_search_path(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute(f"SET search_path TO {schema}")
            cursor.close()

    yield engine

    if schema != "public":
        async with engine.begin() as conn:
            await conn.execute(sa_text(f"DROP SCHEMA {schema} CASCADE"))
    else:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def session(db_engine):
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@asynccontextmanager
async def _test_lifespan(app):
    """No-op lifespan for tests -- skips DB verification and background tasks."""
    yield


@pytest_asyncio.fixture
async def client(db_engine):
    from app.database import get_session
    from app.middleware.rate_limit import rate_limit_requests
    import app.main as main_module

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    original_lifespan = main_module.app.router.lifespan_context
    main_module.app.router.lifespan_context = _test_lifespan
    rate_limit_requests.clear()

    async def override_get_session():
        async with factory() as session:
            yield session

    main_module.app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=main_module.app), base_url="http://test") as c:
        yield c
    main_module.app.dependency_overrides.clear()
    main_module.app.router.lifespan_context = original_lifespan


@pytest_asyncio.fixture
async def admin_user(session):
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.bootstrap_roles import seed_builtin_roles
    from app.services import role_service

    # Clear the permission cache so tests start fresh
    role_service.invalidate_cache()

    org = Organization(name="Test Org", setup_complete=True)
    session.add(org)
    await session.flush()

    # Seed built-in roles for the test organization
    role_map = await seed_builtin_roles(session, org.id)

    password_hash = AuthService.hash_password("testpassword123")
    user = User(
        email="admin@test.com",
        password_hash=password_hash,
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()

    # Stash role_map on the user object for other fixtures to use
    user._test_role_map = role_map  # type: ignore[attr-defined]
    return user


@pytest_asyncio.fixture
async def admin_token(admin_user) -> str:
    return AuthService.create_token(
        admin_user.id, admin_user.email, admin_user.role_id, admin_user.organization_id, role_name="admin"
    )


@pytest_asyncio.fixture
async def viewer_user(session, admin_user):
    from app.models.user import User

    role_map = admin_user._test_role_map  # type: ignore[attr-defined]
    password_hash = AuthService.hash_password("viewerpass123")
    user = User(
        email="viewer@test.com",
        password_hash=password_hash,
        role_id=role_map["viewer"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    user._test_role_map = role_map  # type: ignore[attr-defined]
    return user


@pytest_asyncio.fixture
async def viewer_token(viewer_user) -> str:
    return AuthService.create_token(
        viewer_user.id, viewer_user.email, viewer_user.role_id, viewer_user.organization_id, role_name="viewer"
    )
