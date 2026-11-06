"""The isolation inventory classifies test files by what they need from the database.

Categories, and the fixture route each one gets:

- ``schema``: DDL, migrations, or assumptions about a prior schema. Keeps a dedicated schema.
- ``real_commit``: independent sessions, HTTP requests, background work, post-commit events.
  Keeps real commits; candidate for committed-state reset.
- ``single_connection``: one session, nothing reads across sessions. Candidate for a rollback pilot.
- ``no_database``: uses no database fixture.
- ``unclassified``: could not tell. Keeps the existing isolated path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support import isolation_inventory as inv

TESTS_DIR = Path(__file__).resolve().parent

CONFTEST = """
import pytest_asyncio

@pytest_asyncio.fixture
async def db_engine(worker_id):
    yield None

@pytest_asyncio.fixture
async def session(db_engine):
    yield None

@pytest_asyncio.fixture
async def client(db_engine):
    yield None

@pytest_asyncio.fixture
async def admin_user(session):
    return None
"""


def _classify(tmp_path: Path, source: str, name: str = "test_probe.py") -> inv.Entry:
    (tmp_path / "conftest.py").write_text(CONFTEST)
    path = tmp_path / name
    path.write_text(source)
    graph = inv.fixture_graph([tmp_path / "conftest.py"])
    return inv.classify(path, graph, seeds={})


def test_a_session_only_file_is_single_connection(tmp_path):
    entry = _classify(tmp_path, "async def test_x(session):\n    await session.commit()\n")
    assert entry.category == "single_connection"
    assert entry.route == "rollback_candidate"


def test_fixtures_are_followed_transitively(tmp_path):
    entry = _classify(tmp_path, "async def test_x(admin_user):\n    pass\n")
    assert entry.category == "single_connection"
    assert "fixture:session" in entry.evidence


def test_http_requests_need_real_commits(tmp_path):
    entry = _classify(tmp_path, "async def test_x(client, admin_user):\n    pass\n")
    assert entry.category == "real_commit"
    assert "fixture:client" in entry.evidence


@pytest.mark.parametrize(
    "body, signal",
    [
        ("from app.database import async_session_factory\n", "async_session_factory"),
        ("from sqlalchemy.ext.asyncio import async_sessionmaker\n", "async_sessionmaker"),
        ("from app.services.post_commit_events import wait_for_deferred_events\n", "wait_for_deferred_events"),
        ("import asyncio\nx = asyncio.create_task\n", "create_task"),
    ],
)
def test_independent_sessions_and_background_work_need_real_commits(tmp_path, body, signal):
    entry = _classify(tmp_path, body + "async def test_x(session):\n    pass\n")
    assert entry.category == "real_commit"
    assert signal in entry.evidence


def test_a_file_that_uses_the_engine_directly_needs_real_commits(tmp_path):
    entry = _classify(tmp_path, "async def test_x(db_engine, session):\n    pass\n")
    assert entry.category == "real_commit"


@pytest.mark.parametrize(
    "body",
    [
        "from alembic import command\n",
        "from sqlalchemy import text\nQ = text('ALTER TABLE users ADD COLUMN x int')\n",
        "from app.database import Base\ndef f(c):\n    Base.metadata.create_all(c)\n",
    ],
)
def test_ddl_and_migrations_keep_a_dedicated_schema(tmp_path, body):
    entry = _classify(tmp_path, body + "async def test_x(client):\n    pass\n")
    assert entry.category == "schema"
    assert entry.route == "dedicated_schema"


def test_a_file_without_database_fixtures_is_no_database(tmp_path):
    entry = _classify(tmp_path, "def test_x(tmp_path):\n    pass\n")
    assert entry.category == "no_database"


def test_dynamic_fixture_lookup_is_unclassified(tmp_path):
    entry = _classify(tmp_path, "def test_x(request):\n    request.getfixturevalue('session')\n")
    assert entry.category == "unclassified"
    assert entry.route == "existing_isolated"


def test_seeded_real_commit_files_stay_real_commit(tmp_path):
    (tmp_path / "conftest.py").write_text(CONFTEST)
    path = tmp_path / "test_seeded.py"
    path.write_text("async def test_x(session):\n    pass\n")
    graph = inv.fixture_graph([tmp_path / "conftest.py"])
    entry = inv.classify(path, graph, seeds={"test_seeded.py": "post-commit events"})
    assert entry.category == "real_commit"
    assert "seed: post-commit events" in entry.evidence


def test_the_plans_seed_files_are_real_commit_in_the_repository():
    for name in (
        "test_pipeline_launch_failure.py",
        "test_post_commit_events.py",
        "test_api_keys.py",
        "test_terraform_routes_interruption.py",
        "test_launch_outcome.py",
    ):
        assert name in inv.SEEDS, name


# --- the committed inventory ---------------------------------------------------------


def test_the_committed_inventory_never_routes_a_file_less_isolated_than_its_evidence():
    """A listed file that gained real-commit or schema evidence must be re-reviewed before it moves."""
    recorded = json.loads((TESTS_DIR / "isolation_inventory.json").read_text())["files"]
    current = inv.build(TESTS_DIR)
    rank = {"no_database": 0, "single_connection": 1, "real_commit": 2, "schema": 3, "unclassified": 3}
    regressions = [
        (name, entry["category"], current[name].category)
        for name, entry in recorded.items()
        if name in current and rank[current[name].category] > rank[entry["category"]]
    ]
    assert regressions == [], "regenerate with `python -m tests.support.isolation_inventory --write` and review"


def test_the_committed_inventory_lists_only_existing_files():
    recorded = json.loads((TESTS_DIR / "isolation_inventory.json").read_text())["files"]
    missing = sorted(name for name in recorded if not (TESTS_DIR / name).exists())
    assert missing == []
