"""Every released bioAF install upgrades to this candidate with its data intact (plan 9, section 5).

Any previously released Alembic install may still exist and may skip releases, so the starting points
come from release history (tests/release_revisions.json), not from observed installs. For each distinct
released migration tree:

1. build that release's schema with that release's own migrations and application closure, in an
   isolated interpreter (tests/support/historical_runner.py);
2. write representative data with SQL that revision accepts (tests/support/historical_data.py);
3. hand the same database to the candidate's production Alembic environment, the path every update
   takes, and check the data survived, the final schema equals a fresh install's, seeds are not
   duplicated, and today's services read and write the upgraded rows;
4. upgrade again and check nothing changes.

Each case gets its own database, so old migrations that assume ``public`` run as they did in the field.
That needs CREATEDB: CI's service user has it; locally run under
``python -m tests.support.disposable_pg run -- python -m pytest tests/test_historical_upgrades.py``.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest
from cryptography.fernet import Fernet, MultiFernet

from tests.plugins.db_url import database_url
from tests.support import historical_data, releases, schema_catalog, worker_schema
from tests.support import historical_runner as runner

BACKEND = Path(__file__).resolve().parent.parent
TEST_URL = database_url()
pytestmark = pytest.mark.skipif(os.environ.get("BIOAF_SKIP_MIGRATION_TESTS") == "1", reason="explicitly disabled")


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _with_database(url: str, name: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{name}"


async def _can_create_databases() -> bool:
    conn = await asyncpg.connect(_plain(TEST_URL))
    try:
        return bool(await conn.fetchval("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user"))
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def createdb_allowed():
    if asyncio.run(_can_create_databases()):
        return
    if os.environ.get("CI"):
        pytest.fail("the historical upgrade matrix needs CREATEDB on the test server")
    pytest.skip("needs CREATEDB: run under `python -m tests.support.disposable_pg run -- ...`")


class CaseDatabase:
    """A database of its own for one case, dropped afterwards."""

    def __init__(self) -> None:
        self.name = f"hist_{uuid.uuid4().hex[:12]}"
        self.url = _with_database(TEST_URL, self.name)

    async def create(self) -> None:
        conn = await asyncpg.connect(_plain(TEST_URL))
        try:
            await conn.execute(f'CREATE DATABASE "{self.name}"')
        finally:
            await conn.close()

    async def drop(self) -> None:
        conn = await asyncpg.connect(_plain(TEST_URL))
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)')
        finally:
            await conn.close()

    async def connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(_plain(self.url))


@pytest.fixture
def case_db(createdb_allowed):
    db = CaseDatabase()
    asyncio.run(db.create())
    yield db
    asyncio.run(db.drop())


def upgrade_with_candidate(url: str) -> subprocess.CompletedProcess:
    """The candidate's production migration path: its own alembic.ini and env.py, configured by env."""
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("BIOAF_", "COV_CORE_", "COVERAGE_"))},
        "BIOAF_DATABASE_URL": url,
        "BIOAF_ENCRYPTION_KEYS": runner.TEST_ENCRYPTION_KEY,
        "BIOAF_JWT_SECRET_KEY": runner.TEST_JWT_SECRET,
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic/alembic.ini", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _local_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND / "alembic" / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


async def _catalog(url: str) -> dict:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            catalog = await schema_catalog.read(conn, "public", count_rows=False)
            named = set((await schema_catalog.named_unique(conn, "public")).items())
            # Everything else: every index, default, trigger, function and enum label.
            structure = set(await worker_schema.structure(conn, "public"))
    finally:
        await engine.dispose()
    columns = await _columns(url)
    return {
        **{k: catalog[k] for k in ("unique", "foreign_keys", "checks", "not_null", "tables")},
        "named": named,
        "columns": columns,
        "structure": structure,
    }


async def _columns(url: str) -> set:
    conn = await asyncpg.connect(_plain(url))
    try:
        rows = await conn.fetch(
            "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 'public'"
        )
    finally:
        await conn.close()
    return {tuple(r) for r in rows}


_FRESH: dict[str, dict] = {}


def fresh_install_catalog() -> dict:
    """What a new install of the candidate builds, read once per worker."""
    if "catalog" not in _FRESH:
        db = CaseDatabase()
        asyncio.run(db.create())
        try:
            done = upgrade_with_candidate(db.url)
            assert done.returncode == 0, done.stderr[-3000:]
            _FRESH["catalog"] = asyncio.run(_catalog(db.url))
        finally:
            asyncio.run(db.drop())
    return _FRESH["catalog"]


SEED_UNIQUE = {
    "platform_config": "key",
    "component_states": "component_key",
    "controlled_vocabularies": "field_name, allowed_value",
}


async def _duplicate_seeds(conn: asyncpg.Connection) -> list[str]:
    problems = []
    for table, cols in SEED_UNIQUE.items():
        dupes = await conn.fetch(f"SELECT {cols}, count(*) FROM {table} GROUP BY {cols} HAVING count(*) > 1")
        problems += [f"{table} duplicate {tuple(d)}" for d in dupes]
    dupes = await conn.fetch("SELECT organization_id, name, count(*) FROM roles GROUP BY 1, 2 HAVING count(*) > 1")
    problems += [f"roles duplicate {tuple(d)}" for d in dupes]
    return problems


def _encrypt(value: str) -> str:
    return Fernet(runner.TEST_ENCRYPTION_KEY.encode()).encrypt(value.encode()).decode()


def _decrypt(value):
    return Fernet(runner.TEST_ENCRYPTION_KEY.encode()).decrypt(value.encode()).decode()


async def _snapshot(conn: asyncpg.Connection) -> dict:
    tables = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
    out = {}
    for (table,) in tables:
        out[table] = [tuple(r) for r in await conn.fetch(f'SELECT * FROM "{table}" ORDER BY 1')]
    return out


async def _service_reads_and_writes(url: str, ledger: historical_data.Ledger) -> None:
    """Today's ORM and services work on the upgraded rows, and can write new ones."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.models.organization import Organization
    from app.models.role import Role
    from app.models.sample import Sample
    from app.models.user import User
    from app.platform.platform_config_service import PlatformConfigService

    engine = create_async_engine(url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            orgs = (await session.execute(select(Organization))).scalars().all()
            assert {o.name for o in orgs} >= {"Lab A", "Lab B"}
            users = (await session.execute(select(User))).scalars().all()
            assert all(u.role_id for u in users if u.email.endswith("@lab.example"))
            roles = (await session.execute(select(Role))).scalars().all()
            assert {r.name for r in roles if r.organization_id == orgs[0].id} >= {"admin", "bench"}
            samples = (await session.execute(select(Sample))).scalars().all()
            assert {s.external_id for s in samples} >= {"S0-0", "S1-2"}
            for key, value in ledger.config.items():
                assert await PlatformConfigService.get(session, key) == value
            exp_id = samples[0].experiment_id
            session.add(Sample(experiment_id=exp_id, external_id="AFTER-UPGRADE", status="registered"))
            await session.commit()
    finally:
        await engine.dispose()


def _distinct_cases() -> list[dict]:
    return releases.distinct_trees(releases.current())


def test_release_mapping_matches_git():
    """The recorded release history is true: no recorded tag moved, and every head was read right."""
    assert releases.recorded_against_git() == []
    recorded = releases.load()
    assert len(recorded) >= 125 and recorded[0]["tag"] == "v0.4.0" and recorded[0]["head"] == "055"
    # Spot-check heads against an independent read of the tree (the earliest, 076's and the baseline).
    by_tag = {r["tag"]: r for r in recorded}
    for tag in ("v0.4.0", "v0.13.0", "v2026.11.7"):
        assert releases.head_revision(tag) == by_tag[tag]["head"]


@pytest.mark.parametrize("release", _distinct_cases(), ids=lambda r: f"{r['tag']}-{r['head']}")
def test_a_populated_release_upgrades_to_the_candidate(release, case_db, monkeypatch):
    manifest = runner.build(release["commit"], release["head"], case_db.url)

    async def _prepare():
        conn = await case_db.connect()
        try:
            assert await conn.fetchval("SELECT version_num FROM alembic_version") == release["head"]
            async with conn.transaction():
                ledger = await historical_data.populate(conn, release["head"], _encrypt)
            return ledger
        finally:
            await conn.close()

    ledger = asyncio.run(_prepare())
    assert all(m.startswith("app") for m in manifest.app_modules)

    first = upgrade_with_candidate(case_db.url)
    assert first.returncode == 0, first.stderr[-4000:]

    async def _check():
        conn = await case_db.connect()
        try:
            assert await conn.fetchval("SELECT version_num FROM alembic_version") == _local_head()
            assert await historical_data.assert_preserved(conn, ledger, _decrypt) == []
            assert await _duplicate_seeds(conn) == []
            return await _snapshot(conn)
        finally:
            await conn.close()

    before_repeat = asyncio.run(_check())
    upgraded = asyncio.run(_catalog(case_db.url))
    fresh = fresh_install_catalog()
    for kind in fresh:
        assert upgraded[kind] == fresh[kind], (
            f"{kind} differs from a fresh install: only upgraded {sorted(upgraded[kind] - fresh[kind])[:10]}, "
            f"only fresh {sorted(fresh[kind] - upgraded[kind])[:10]}"
        )

    again = upgrade_with_candidate(case_db.url)
    assert again.returncode == 0, again.stderr[-4000:]

    async def _unchanged():
        conn = await case_db.connect()
        try:
            return await _snapshot(conn)
        finally:
            await conn.close()

    assert asyncio.run(_unchanged()) == before_repeat, "a repeated upgrade changed data"

    # The installation's key, as the running application would hold it (restored after the test).
    from app.platform import encryption_service

    monkeypatch.setattr(encryption_service, "_multifernet", MultiFernet([Fernet(runner.TEST_ENCRYPTION_KEY.encode())]))
    asyncio.run(_service_reads_and_writes(case_db.url, ledger))


# --- Migration 080: legacy duplicate sample labels (owner policy, 2026-11-07) -------------------------

LONG = "L" * 255


async def _seed_duplicate_labels(conn: asyncpg.Connection) -> dict[str, int]:
    """Below 080 a source label had no uniqueness guarantee. Returns label-ish keys -> sample ids."""
    org = await conn.fetchval("INSERT INTO organizations (name) VALUES ('Dup lab') RETURNING id")
    project = await conn.fetchval(
        "INSERT INTO projects (organization_id, name, status) VALUES ($1, 'p', 'active') RETURNING id", org
    )

    async def experiment(name):
        return await conn.fetchval(
            "INSERT INTO experiments (organization_id, project_id, name, status) VALUES ($1, $2, $3, 'registered') "
            "RETURNING id",
            org,
            project,
            name,
        )

    async def sample(exp, label):
        return await conn.fetchval(
            "INSERT INTO samples (experiment_id, sample_id_external, status) VALUES ($1, $2, 'registered') RETURNING id",
            exp,
            label,
        )

    one, two = await experiment("one"), await experiment("two")
    ids = {
        "dup_a": await sample(one, "DUP"),
        "dup_b": await sample(one, "DUP"),
        "dup_c": await sample(one, "DUP"),
        "unique": await sample(one, "UNIQUE"),
        "null_a": await sample(one, None),
        "null_b": await sample(one, None),
        "long_a": await sample(one, LONG),
        "long_b": await sample(one, LONG),
        "other_experiment_dup": await sample(two, "DUP"),
        "experiment_one": one,
        "experiment_two": two,
    }
    # A real label that looks like a generated one: generation must step around it.
    ids["lookalike"] = await sample(one, f"DUP~{ids['dup_a']}")
    # A file linked to a duplicate: relationships must not move.
    file_id = await conn.fetchval(
        "INSERT INTO files (organization_id, gcs_uri, filename, file_type, experiment_id) "
        "VALUES ($1, 'gs://b/r1.fastq.gz', 'r1.fastq.gz', 'fastq', $2) RETURNING id",
        org,
        one,
    )
    await conn.execute("INSERT INTO sample_files (sample_id, file_id) VALUES ($1, $2)", ids["dup_b"], file_id)
    ids["file"] = file_id
    return ids


def _pre_080_cases() -> list[dict]:
    return [r for r in _distinct_cases() if int(r["head"]) < 80]


@pytest.mark.parametrize("release", _pre_080_cases(), ids=lambda r: f"{r['tag']}-{r['head']}")
def test_duplicate_source_labels_upgrade_with_unique_external_ids_and_a_visible_mapping(release, case_db):
    runner.build(release["commit"], release["head"], case_db.url)

    async def _seed():
        conn = await case_db.connect()
        try:
            return await _seed_duplicate_labels(conn)
        finally:
            await conn.close()

    ids = asyncio.run(_seed())
    done = upgrade_with_candidate(case_db.url)
    assert done.returncode == 0, done.stderr[-4000:]

    async def _check():
        conn = await case_db.connect()
        try:
            rows = {
                r["id"]: r
                for r in await conn.fetch(
                    "SELECT id, experiment_id, sample_id_external, external_id, uuid FROM samples"
                )
            }
            mapping = {
                r["sample_id"]: r
                for r in await conn.fetch(
                    "SELECT sample_id, experiment_id, source_label, external_id, revision "
                    "FROM sample_external_id_transitions"
                )
            }
            index = await conn.fetchval("SELECT pg_get_indexdef('uq_samples_experiment_external_id'::regclass)")
            links = await conn.fetch("SELECT sample_id, file_id FROM sample_files")
            return rows, mapping, index, links
        finally:
            await conn.close()

    rows, mapping, index, links = asyncio.run(_check())
    one = ids["experiment_one"]

    # Every sample and its source label survive; nothing merged, dropped or moved.
    assert {ids[k] for k in ids if k not in {"experiment_one", "experiment_two", "file"}} <= set(rows)
    assert rows[ids["dup_a"]]["sample_id_external"] == "DUP"
    assert rows[ids["long_b"]]["sample_id_external"] == LONG
    assert [(r["sample_id"], r["file_id"]) for r in links] == [(ids["dup_b"], ids["file"])]

    # Unambiguous labels keep their value; unnamed samples stay unnamed; reuse in another experiment is fine.
    assert rows[ids["unique"]]["external_id"] == "UNIQUE"
    assert rows[ids["other_experiment_dup"]]["external_id"] == "DUP"
    assert rows[ids["lookalike"]]["external_id"] == f"DUP~{ids['dup_a']}"
    assert rows[ids["null_a"]]["external_id"] is None and rows[ids["null_b"]]["external_id"] is None

    # Every member of a duplicate group gets a deterministic operational ID; none keeps the bare label.
    dups = [ids[k] for k in ("dup_a", "dup_b", "dup_c", "long_a", "long_b")]
    assert set(mapping) == set(dups)
    assert rows[ids["dup_a"]]["external_id"] == f"DUP~{ids['dup_a']}~2"  # stepped around the lookalike
    assert rows[ids["dup_b"]]["external_id"] == f"DUP~{ids['dup_b']}"
    assert rows[ids["dup_c"]]["external_id"] == f"DUP~{ids['dup_c']}"
    for key in ("long_a", "long_b"):
        assigned = rows[ids[key]]["external_id"]
        assert len(assigned) <= 255 and assigned.endswith(f"~{ids[key]}") and assigned.startswith("L" * 200)
    in_one = [r["external_id"] for r in rows.values() if r["experiment_id"] == one and r["external_id"]]
    assert len(in_one) == len(set(in_one))

    # The mapping names the stable identity, the source label and the assigned ID.
    for sample_id, m in mapping.items():
        assert m["experiment_id"] == one
        assert m["source_label"] == rows[sample_id]["sample_id_external"]
        assert m["external_id"] == rows[sample_id]["external_id"]
        assert m["revision"] == "080"

    # The production index is present with its documented definition.
    assert (
        index.endswith("USING btree (experiment_id, external_id) WHERE (external_id IS NOT NULL)") and "UNIQUE" in index
    )


def test_an_interrupted_080_transition_rolls_back_and_a_retry_assigns_the_same_ids(case_db):
    """A failure later in the same upgrade undoes 080's relabelling with everything else; the retry
    starts again from the release's data and, the rule being frozen, assigns the same IDs."""
    release = max(_pre_080_cases(), key=lambda r: int(r["head"]))
    runner.build(release["commit"], release["head"], case_db.url)

    async def _seed_and_block():
        conn = await case_db.connect()
        try:
            ids = await _seed_duplicate_labels(conn)
            # Migration 081 creates this table; an existing one makes it fail after 080 has run.
            await conn.execute("CREATE TABLE llm_provider_config (id int)")
            return ids, await _snapshot(conn)
        finally:
            await conn.close()

    ids, before = asyncio.run(_seed_and_block())
    failed = upgrade_with_candidate(case_db.url)
    assert failed.returncode != 0 and "llm_provider_config" in failed.stderr

    async def _after_failure():
        conn = await case_db.connect()
        try:
            return await conn.fetchval("SELECT version_num FROM alembic_version"), await _snapshot(conn)
        finally:
            await conn.close()

    version, after = asyncio.run(_after_failure())
    assert version == release["head"]
    assert after == before, "the failed upgrade left partial changes"

    async def _unblock():
        conn = await case_db.connect()
        try:
            await conn.execute("DROP TABLE llm_provider_config")
        finally:
            await conn.close()

    asyncio.run(_unblock())
    retried = upgrade_with_candidate(case_db.url)
    assert retried.returncode == 0, retried.stderr[-4000:]

    async def _assigned():
        conn = await case_db.connect()
        try:
            return dict(await conn.fetch("SELECT sample_id, external_id FROM sample_external_id_transitions"))
        finally:
            await conn.close()

    assert asyncio.run(_assigned()) == {
        ids["dup_a"]: f"DUP~{ids['dup_a']}~2",
        ids["dup_b"]: f"DUP~{ids['dup_b']}",
        ids["dup_c"]: f"DUP~{ids['dup_c']}",
        ids["long_a"]: LONG[: 255 - len(f"~{ids['long_a']}")] + f"~{ids['long_a']}",
        ids["long_b"]: LONG[: 255 - len(f"~{ids['long_b']}")] + f"~{ids['long_b']}",
    }
