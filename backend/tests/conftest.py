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

import asyncio  # noqa: E402
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

pytest_plugins = ["tests.plugins.phase_report", "tests.plugins.invocation", "tests.plugins.shuffle"]

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


# Migration-built worker schemas, one per pilot prefix, built on first use (tests/support/worker_schema.py).
_WORKER_SCHEMAS: dict[str, object] = {}
# Set when a migrated_reset test has run since the worker schema was last reset.
_WORKER_SCHEMA_DIRTY = {"value": False}


def _engine_for(schema: str):
    # Pin search_path at the asyncpg protocol level via connect_args so EVERY
    # connection lands in the test's schema. A "connect" event listener running a
    # SET through the greenlet bridge can miss a freshly-created connection under
    # concurrent connection creation (e.g. tests that spawn a background DB task
    # during a request); that connection then resolves tables in the wrong schema
    # and raises "relation ... does not exist". server_settings is race-free.
    return create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"server_settings": {"search_path": schema}},
    )


@pytest_asyncio.fixture
async def db_engine(worker_id, request):
    """An engine on this test's database, every session factory routed to it, torn down after.

    The route comes from tests/support/fixture_routes.py:

    - ``existing`` (default): a fresh create_all schema ``test_<invocation>_<worker>``, dropped after.
    - ``migrated_reset``: the worker's migration-built schema, reset to its empty-upgrade baseline.
    - ``rollback``: the worker's migration-built schema inside one outer transaction, rolled back.

    Schemas are per invocation and worker (tests/plugins/invocation.py), serial runs included, so
    concurrent invocations never share one and nothing here touches ``public``.
    """
    import app.models  # noqa: F401 -- register all models with Base.metadata
    from app.database import Base
    from tests.plugins import invocation  # imported here so pytest registers the plugin first
    from tests.support import fixture_routes, worker_schema

    route = fixture_routes.route_for(request.node.path.name)
    cleanup: list = []

    if route == "existing":
        schema = invocation.claim_schema("test", worker_id)
        quoted = invocation.quote(schema)
        engine = _engine_for(schema)
        async with engine.begin() as conn:
            # A schema left by an interrupted test in this same invocation is stale.
            await conn.execute(sa_text(f"DROP SCHEMA IF EXISTS {quoted} CASCADE"))
            await conn.execute(sa_text(f"CREATE SCHEMA {quoted}"))
            await conn.run_sync(Base.metadata.create_all)

        async def _drop_schema():
            async with engine.begin() as conn:
                await conn.execute(sa_text(f"DROP SCHEMA {quoted} CASCADE"))

        test_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        cleanup.append(_drop_schema)
    else:
        ws = _WORKER_SCHEMAS.get("pilot")
        if ws is None:
            ws = await asyncio.to_thread(worker_schema.build, "pilot", worker_id)
            _WORKER_SCHEMA_DIRTY["value"] = False
        engine = _engine_for(ws.schema)  # type: ignore[attr-defined]
        if route == "migrated_reset" or _WORKER_SCHEMA_DIRTY["value"]:
            ws = await worker_schema.reset(engine, ws)  # type: ignore[arg-type]
        _WORKER_SCHEMAS["pilot"] = ws
        _WORKER_SCHEMA_DIRTY["value"] = route == "migrated_reset"
        if route == "rollback":
            scope = worker_schema.rollback_scope(engine, ws)  # type: ignore[arg-type]
            test_factory = await scope.__aenter__()
            cleanup.append(lambda: scope.__aexit__(None, None, None))
        else:
            test_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Route every database entry point to this test's engine: `app.database.async_session_factory`
    # (auth middleware, webhook dispatcher and worker, etc.) and every module that copied it at import
    # (stack_deploy, lab_documents, lab_glossary, agent_reviews). See tests/support/lifecycle.py.
    import app.database as database_module

    restore_factories = lifecycle.redirect_session_factories(test_factory, database_module.async_session_factory)
    # The test's task owner: work the test starts through the application's spawn points,
    # requests through `client`, and committed events all belong to it.
    from app import runtime

    owner = runtime.TaskOwner(name="test")
    tasks_before = lifecycle.snapshot_tasks()

    try:
        with runtime.bound(owner):
            yield engine
    finally:
        # While the test factory and schema still exist: let the test's events finish, then stop
        # the work it started through its owner (then, as a backstop, any application task that
        # escaped it), so none keeps a transaction open (deadlocking the drop) or falls back to the
        # production factory. Every step runs even if one fails.
        await lifecycle.run_all(
            lambda: lifecycle.settle_owner(owner),
            lambda: lifecycle.cancel_escaped_tasks(tasks_before),
            restore_factories,
            *cleanup,
            engine.dispose,
        )


@pytest_asyncio.fixture
async def session(db_engine):
    # The factory db_engine routed for this test (on the rollback route it joins the outer transaction).
    import app.database as database_module

    async with database_module.async_session_factory() as session:
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
    # Requests bind the test's task owner, as they bind the lifespan's in production.
    from app import runtime

    previous_owner = getattr(main_module.app.state, "runtime", None)
    main_module.app.state.runtime = runtime.current_owner()

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
        main_module.app.state.runtime = previous_owner


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


# Shared role builders. A test file that needs a different user (another Organization, extra
# credentials, a different commit pattern) defines its own fixture of the same name, which wins.
@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def comp_bio_token(comp_bio_user) -> str:
    return AuthService.create_token(
        comp_bio_user.id,
        comp_bio_user.email,
        comp_bio_user.role_id,
        comp_bio_user.organization_id,
        role_name="comp_bio",
    )


@pytest_asyncio.fixture
async def bench_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("benchpass123")
    user = User(
        email="bench@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["bench"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def bench_token(bench_user) -> str:
    return AuthService.create_token(
        bench_user.id, bench_user.email, bench_user.role_id, bench_user.organization_id, role_name="bench"
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


@pytest.fixture(autouse=True)
def request_clock():
    """Source pacing, cooldowns, and retry backoff run on virtual time.

    Every test starts from empty pacing state on an AutoClock
    (tests/support/request_clock.py): a wait advances virtual time by exactly
    its length and returns at once, so retries never sleep for real. A test
    that measures timing installs its own clock with request_pacing.reset().
    """
    from app.services import request_pacing
    from tests.support.request_clock import AutoClock

    clock = AutoClock()
    request_pacing.reset(clock)
    yield clock
    request_pacing.reset()


@pytest.fixture(autouse=True)
def url_network(monkeypatch):
    """No test resolves or fetches a real URL through the URL-import guard.

    The guard's resolver and the downloader's HTTP client are replaced by a
    controlled network (tests/support/url_network.py) that refuses anything a
    test did not configure. A rejection test therefore cannot make an outbound
    request even if the guard regresses, and an acceptance test is independent of
    the developer's network.
    """
    from app.services import lab_document_upload_service
    from tests.support.url_network import UrlNetwork

    network = UrlNetwork()
    monkeypatch.setattr(lab_document_upload_service, "_resolve_host", network.resolve)
    monkeypatch.setattr(lab_document_upload_service, "_open_http_client", network.open_client)
    return network


@pytest.fixture
def billing_clock():
    """Fix the billing reference instant and host timezone (tests/support/billing_clock.py)."""
    from tests.support.billing_clock import BillingClock

    clock = BillingClock()
    yield clock
    clock.restore()
