import os

# Pin env vars BEFORE any app.* import so pydantic-settings reads them at
# Settings() construction time.
os.environ.setdefault("BIOAF_COMPUTE_MODE", "local")
# Two keys exercise the MultiFernet rotation path; only the first is the writer.
os.environ.setdefault(
    "BIOAF_ENCRYPTION_KEYS",
    "yQWeSjhut-D91YUcqvDUfQ62wQHNq1G3vUstCSJpk9U=,RULBtMyNqzJbIBpDe1gwY2YCCYkBI0UqjJsdAP-41AU=",
)

from contextlib import asynccontextmanager  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy import text as sa_text  # noqa: E402

from app.adapters import registry as adapter_registry  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402

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

    # For per-worker (xdist) schemas, pin search_path at the asyncpg protocol
    # level via connect_args so EVERY connection lands in the worker schema. The
    # "connect" event listener below sets it too, but that runs a SET through the
    # greenlet bridge and can miss a freshly-created connection under concurrent
    # connection creation (e.g. tests that spawn a background DB task during a
    # request); that connection then resolves tables in the wrong schema and
    # raises "relation ... does not exist". server_settings is race-free.
    connect_args = {} if schema == "public" else {"server_settings": {"search_path": schema}}

    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args=connect_args,
    )

    if schema != "public":
        async with engine.begin() as conn:
            await conn.execute(sa_text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            await conn.execute(sa_text(f"SET search_path TO {schema}"))
            await conn.run_sync(Base.metadata.create_all)
    else:
        # Drop all tables individually, then drop user-defined enum types
        # that survive table drops and cause IntegrityError on create_all.
        async with engine.begin() as conn:
            rows = await conn.execute(sa_text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
            tables = [row[0] for row in rows.fetchall()]
        for table in tables:
            async with engine.begin() as conn:
                await conn.execute(sa_text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
        async with engine.begin() as conn:
            rows = await conn.execute(
                sa_text("SELECT typname FROM pg_type WHERE typnamespace = 'public'::regnamespace AND typtype = 'e'")
            )
            for (name,) in rows.fetchall():
                await conn.execute(sa_text(f'DROP TYPE IF EXISTS "{name}" CASCADE'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # Set search_path for all connections from this engine
    if schema != "public":
        from sqlalchemy import event

        @event.listens_for(engine.sync_engine, "connect")
        def set_search_path(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute(f"SET search_path TO {schema}")
            cursor.close()

    # Redirect any code path that calls `app.database.async_session_factory`
    # directly (auth middleware, webhook dispatcher and worker, etc.) to the
    # test engine for the duration of this test.
    import app.database as database_module

    original_session_factory = database_module.async_session_factory
    database_module.async_session_factory = async_sessionmaker(  # type: ignore[assignment]
        engine, class_=AsyncSession, expire_on_commit=False
    )

    yield engine

    database_module.async_session_factory = original_session_factory  # type: ignore[assignment]

    if schema != "public":
        async with engine.begin() as conn:
            await conn.execute(sa_text(f"DROP SCHEMA {schema} CASCADE"))
    else:
        async with engine.begin() as conn:
            rows = await conn.execute(sa_text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
            tables = [row[0] for row in rows.fetchall()]
        for table in tables:
            async with engine.begin() as conn:
                await conn.execute(sa_text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
        async with engine.begin() as conn:
            rows = await conn.execute(
                sa_text("SELECT typname FROM pg_type WHERE typnamespace = 'public'::regnamespace AND typtype = 'e'")
            )
            for (name,) in rows.fetchall():
                await conn.execute(sa_text(f'DROP TYPE IF EXISTS "{name}" CASCADE'))

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
    import app.database as database_module
    import app.main as main_module

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    original_lifespan = main_module.app.router.lifespan_context
    main_module.app.router.lifespan_context = _test_lifespan
    rate_limit_requests.clear()

    # Middleware paths that go through `async_session_factory` directly (e.g.
    # the API-key authentication path) need to see the test schema.
    original_session_factory = database_module.async_session_factory
    database_module.async_session_factory = factory  # type: ignore[assignment]

    async def override_get_session():
        async with factory() as session:
            yield session

    main_module.app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=main_module.app), base_url="http://test") as c:
        yield c
    main_module.app.dependency_overrides.clear()
    main_module.app.router.lifespan_context = original_lifespan

    # Cancel detached background tasks the test spawned via endpoints (e.g. the
    # lit-review-run `_execute_run` task fired by `schedule_run`). Done while the
    # test session factory is still active and BEFORE the worker schema is
    # dropped, so a leaked task cannot (a) keep a transaction open and deadlock
    # DROP SCHEMA, or (b) fall back to the restored production factory and fail
    # auth. Only our own app-code coroutines are cancelled; pytest/asyncio
    # internals are left alone.
    import asyncio as _asyncio

    lingering = []
    for _t in _asyncio.all_tasks():
        if _t is _asyncio.current_task() or _t.done():
            continue
        _code = getattr(_t.get_coro(), "cr_code", None)
        _file = getattr(_code, "co_filename", "") or ""
        if f"{os.sep}app{os.sep}" in _file:
            lingering.append(_t)
    for _t in lingering:
        _t.cancel()
    if lingering:
        await _asyncio.gather(*lingering, return_exceptions=True)

    database_module.async_session_factory = original_session_factory  # type: ignore[assignment]


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


@pytest_asyncio.fixture
async def integration_api_key(session, admin_user):
    """Service account + API key with the full public scope alphabet."""
    from app.services import api_key_service, service_account_service

    role_map = admin_user._test_role_map  # type: ignore[attr-defined]
    sa = await service_account_service.create(
        session,
        org_id=admin_user.organization_id,
        display_name="Test SA",
        role_id=role_map["admin"],
        created_by_user_id=admin_user.id,
    )
    _row, secret = await api_key_service.mint(
        session,
        org_id=admin_user.organization_id,
        sa_user_id=sa.id,
        name="primary",
        scopes=list(api_key_service.PUBLIC_SCOPE_ALPHABET),
        created_by_user_id=admin_user.id,
    )
    await session.commit()
    return {"sa": sa, "secret": secret, "headers": {"Authorization": f"Bearer {secret}"}}


@pytest_asyncio.fixture
async def viewer_api_key(session, admin_user):
    """SA with viewer role + a key carrying only :view scopes."""
    from app.services import api_key_service, service_account_service

    role_map = admin_user._test_role_map  # type: ignore[attr-defined]
    sa = await service_account_service.create(
        session,
        org_id=admin_user.organization_id,
        display_name="Test SA (viewer)",
        role_id=role_map["viewer"],
        created_by_user_id=admin_user.id,
    )
    _row, secret = await api_key_service.mint(
        session,
        org_id=admin_user.organization_id,
        sa_user_id=sa.id,
        name="viewer-key",
        scopes=[
            "projects:view",
            "experiments:view",
            "samples:view",
            "files:view",
        ],
        created_by_user_id=admin_user.id,
    )
    await session.commit()
    return {"sa": sa, "secret": secret, "headers": {"Authorization": f"Bearer {secret}"}}


@pytest.fixture(autouse=True)
def _no_accession_manifest_network(monkeypatch):
    """No test reaches ENA or GEO for a study's sample manifest.

    The reproduction-plan extractor reads the scoped accession's own ``library_strategy`` to decide
    which pipeline a multi-assay paper should run. That is a real network fetch on the read path, so
    it is stubbed to fail here: ``fetch_manifest`` never raises, so an unreachable deposit yields no
    strategy and the paper's prose decides, exactly as it did before. A test that wants a strategy
    supplies its own fetcher.
    """
    from app.services.literature import accession_manifest_service

    async def _offline(url: str) -> str:
        raise RuntimeError(f"tests do not fetch {url}")

    monkeypatch.setattr(accession_manifest_service, "_http_fetch_text", _offline)
