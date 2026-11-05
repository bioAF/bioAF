import os

# Pin env vars BEFORE any app.* import so pydantic-settings reads them at
# Settings() construction time.
os.environ.setdefault("BIOAF_COMPUTE_MODE", "local")
# Two keys exercise the MultiFernet rotation path; only the first is the writer.
os.environ.setdefault(
    "BIOAF_ENCRYPTION_KEYS",
    "yQWeSjhut-D91YUcqvDUfQ62wQHNq1G3vUstCSJpk9U=,RULBtMyNqzJbIBpDe1gwY2YCCYkBI0UqjJsdAP-41AU=",
)

# Test-only password hashing cost (default 4; BIOAF_TEST_BCRYPT_ROUNDS=12 for an
# unchanged baseline). Overwrites BIOAF_BCRYPT_ROUNDS; see tests/plugins/bcrypt_rounds.py.
from tests.plugins.bcrypt_rounds import apply_test_bcrypt_rounds  # noqa: E402

apply_test_bcrypt_rounds(os.environ)

from contextlib import asynccontextmanager  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy import text as sa_text  # noqa: E402

from app.adapters import registry as adapter_registry  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402

from tests.plugins.db_url import database_url  # noqa: E402
from tests.support import lifecycle  # noqa: E402

pytest_plugins = ["tests.plugins.phase_report", "tests.plugins.invocation"]

TEST_DATABASE_URL = database_url()


@pytest.fixture(autouse=True)
def _isolated_state():
    """In-process caches and registries start every test as they were after import."""
    with lifecycle.isolated_state():
        yield


@pytest.fixture(autouse=True)
def _service_bcrypt_rounds(monkeypatch):
    """API-key and setup-code hashes use the test cost; each service owns its own seam."""
    from app.services import api_key_service, setup_code_service

    rounds = int(os.environ["BIOAF_TEST_BCRYPT_ROUNDS"])
    monkeypatch.setattr(api_key_service, "HASH_ROUNDS", rounds)
    monkeypatch.setattr(setup_code_service, "HASH_ROUNDS", rounds)


@pytest_asyncio.fixture(autouse=True)
async def _init_adapter_registry():
    """Initialize the BAL adapter registry for all tests (local/mock mode)."""
    adapter_registry.initialize_adapters_sync("kubernetes")
    yield
    adapter_registry.reset_registry()


@pytest_asyncio.fixture
async def db_engine(worker_id):
    """Create engine, set up tables in this invocation's worker schema, yield, tear down.

    The schema is ``test_<invocation>_<worker>`` (see tests/plugins/invocation.py),
    serial runs included, so concurrent invocations never share a schema and
    nothing here touches ``public``.
    """
    import app.models  # noqa: F401 -- register all models with Base.metadata
    from app.database import Base
    from tests.plugins import invocation  # imported here so pytest registers the plugin first

    schema = invocation.claim_schema("test", worker_id)
    quoted = invocation.quote(schema)

    # Pin search_path at the asyncpg protocol level via connect_args so EVERY
    # connection lands in the worker schema. A "connect" event listener running a
    # SET through the greenlet bridge can miss a freshly-created connection under
    # concurrent connection creation (e.g. tests that spawn a background DB task
    # during a request); that connection then resolves tables in the wrong schema
    # and raises "relation ... does not exist". server_settings is race-free.
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"server_settings": {"search_path": schema}},
    )

    async with engine.begin() as conn:
        # A schema left by an interrupted test in this same invocation is stale.
        await conn.execute(sa_text(f"DROP SCHEMA IF EXISTS {quoted} CASCADE"))
        await conn.execute(sa_text(f"CREATE SCHEMA {quoted}"))
        await conn.run_sync(Base.metadata.create_all)

    # Route every database entry point to this test's engine: `app.database.async_session_factory`
    # (auth middleware, webhook dispatcher and worker, etc.) and every module that copied it at import
    # (stack_deploy, lab_documents, lab_glossary, agent_reviews). See tests/support/lifecycle.py.
    import app.database as database_module

    test_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    restore_factories = lifecycle.redirect_session_factories(test_factory, database_module.async_session_factory)
    tasks_before = lifecycle.snapshot_tasks()

    async def _drop_schema():
        async with engine.begin() as conn:
            await conn.execute(sa_text(f"DROP SCHEMA {quoted} CASCADE"))

    try:
        yield engine
    finally:
        # While the test factory and schema still exist: let scheduled post-commit events finish,
        # then cancel tasks the test started, so none keeps a transaction open (deadlocking the
        # drop) or falls back to the production factory. Every step runs even if one fails.
        await lifecycle.run_all(
            lambda: lifecycle.drain_and_cancel(tasks_before),
            restore_factories,
            _drop_schema,
            engine.dispose,
        )


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

    # The test factory db_engine installed, so middleware and requests share one route.
    factory = database_module.async_session_factory

    original_lifespan = main_module.app.router.lifespan_context
    main_module.app.router.lifespan_context = _test_lifespan
    rate_limit_requests.clear()

    async def override_get_session():
        async with factory() as session:
            yield session

    main_module.app.dependency_overrides[get_session] = override_get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=main_module.app), base_url="http://test") as c:
            yield c
    finally:
        # Background tasks the test spawned through endpoints are cancelled by db_engine's teardown,
        # which runs after this and before the schema is dropped.
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
