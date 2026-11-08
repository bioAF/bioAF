"""Representative data for a released schema, written with that revision's SQL (plan 9, section 5).

``populate`` inspects the schema it is given and writes only into tables and columns that exist, so
one builder serves every released revision (055 onward) without inventing states the revision could
not hold. Every row it writes is recorded in a ``Ledger`` so ``assert_preserved`` can prove the
candidate's upgrade kept it: same primary key, same values, except for the documented transformations
(076 encrypts named columns; values in them are compared decrypted; nothing else may change a
value written here). From 076 on the builder stores ciphertext there, as the application did.

The data covers two Organizations, users with roles, projects/experiments/samples, files, compute
sessions with notebook-file links (including a link with no recorded direction), configuration
overrides of seeded keys, audit
history, and secrets in the columns 076 encrypts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import asyncpg

# 076's columns. A value written in plaintext before 076 is ciphertext afterwards, decrypting to the
# same plaintext. Values here are compared through the candidate's encryption service.
ENCRYPTED = {
    ("organizations", "smtp_password"),
    ("organizations", "slack_client_secret"),
    ("organizations", "slack_signing_secret"),
    ("session_credentials", "ssh_private_key"),
    ("compute_sessions", "heartbeat_token"),
    ("slack_installations", "bot_token"),
    ("slack_webhooks", "webhook_url"),
}

# A user's own setting for a key that migrations seed (as `''` or `'null'`) or rewrite.
CONFIG_OVERRIDES = {
    "gcp_project_id": "lab-own-project",
    "raw_bucket_name": "lab-own-raw-bucket",
    "compute_stack": "kubernetes",
}


@dataclass
class Ledger:
    revision: str
    # Encrypts as the application did at revisions from 076 on, where these columns hold ciphertext.
    encrypt: Any = None
    rows: dict[str, dict[int, dict[str, Any]]] = field(default_factory=dict)
    config: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def record(self, table: str, row_id: int, values: dict[str, Any]) -> None:
        self.rows.setdefault(table, {})[row_id] = dict(values)


class Schema:
    def __init__(self, columns: dict[str, dict[str, dict]]):
        self.columns = columns

    @classmethod
    async def read(cls, conn: asyncpg.Connection) -> "Schema":
        rows = await conn.fetch(
            """
            SELECT table_name, column_name, is_nullable = 'NO' AS required,
                   column_default IS NOT NULL AS has_default, data_type
            FROM information_schema.columns WHERE table_schema = current_schema()
            """
        )
        columns: dict[str, dict[str, dict]] = {}
        for r in rows:
            columns.setdefault(r["table_name"], {})[r["column_name"]] = dict(r)
        return cls(columns)

    def has(self, table: str, column: str | None = None) -> bool:
        return table in self.columns and (column is None or column in self.columns[table])


_FILLER = {
    "character varying": "legacy",
    "text": "legacy",
    "integer": 1,
    "bigint": 1,
    "boolean": False,
    "jsonb": "{}",
    "json": "{}",
    "numeric": 1,
    "double precision": 1.0,
    "timestamp with time zone": None,  # filled with now() below
    "date": None,
}


async def insert(conn: asyncpg.Connection, schema: Schema, ledger: Ledger, table: str, **values: Any) -> int | None:
    """Insert into ``table`` the values whose columns exist; fill other required columns plainly."""
    if not schema.has(table):
        return None
    cols = schema.columns[table]
    row = {k: v for k, v in values.items() if k in cols}
    exprs: dict[str, str] = {}
    for name, info in cols.items():
        if name in row or name == "id" or not info["required"] or info["has_default"]:
            continue
        kind = info["data_type"]
        if kind in ("timestamp with time zone", "timestamp without time zone", "date"):
            exprs[name] = "now()"
        elif kind in _FILLER:
            row[name] = _FILLER[kind]
        else:
            raise ValueError(f"{table}.{name}: no filler for {kind}")
    stored = dict(row)
    if int(ledger.revision) >= 76:
        for column, value in row.items():
            if (table, column) in ENCRYPTED and value is not None:
                stored[column] = ledger.encrypt(value)
    names = list(stored) + list(exprs)
    placeholders = [f"${i + 1}" for i in range(len(row))] + list(exprs.values())
    sql = f'INSERT INTO "{table}" ({", ".join(names)}) VALUES ({", ".join(placeholders)}) RETURNING id'
    row_id = await conn.fetchval(sql, *stored.values())
    ledger.record(table, row_id, row)
    return row_id


def _sessions_table(schema: Schema) -> str:
    return "compute_sessions" if schema.has("compute_sessions") else "notebook_sessions"


async def populate(conn: asyncpg.Connection, revision: str, encrypt) -> Ledger:
    schema = await Schema.read(conn)
    ledger = Ledger(revision, encrypt)
    rev = int(revision)

    for org_index, org_name in enumerate(("Lab A", "Lab B")):
        org = await insert(
            conn,
            schema,
            ledger,
            "organizations",
            name=org_name,
            smtp_password=f"smtp-secret-{org_index}",
            slack_client_secret=f"slack-client-{org_index}",
            slack_signing_secret=f"slack-signing-{org_index}",
        )
        # Roles are per Organization; the application seeds them when an Organization is created.
        role_ids = {}
        for role_name in ("admin", "comp_bio", "bench", "viewer"):
            role_ids[role_name] = await insert(
                conn, schema, ledger, "roles", name=role_name, organization_id=org, is_system=True
            )
            if schema.has("role_permissions"):
                await insert(
                    conn,
                    schema,
                    ledger,
                    "role_permissions",
                    role_id=role_ids[role_name],
                    resource="experiments",
                    action="view",
                )
        admin = await insert(
            conn,
            schema,
            ledger,
            "users",
            organization_id=org,
            email=f"admin{org_index}@lab.example",
            name=f"Admin {org_index}",
            password_hash="$2b$04$legacyhashlegacyhashlegacyhashlegacyhashlegacyhashle",
            role_id=role_ids["admin"],
            status="active",
        )
        member = await insert(
            conn,
            schema,
            ledger,
            "users",
            organization_id=org,
            email=f"bench{org_index}@lab.example",
            name=f"Bench {org_index}",
            password_hash="$2b$04$legacyhashlegacyhashlegacyhashlegacyhashlegacyhashle",
            role_id=role_ids["bench"],
            status="active",
        )
        project = await insert(
            conn, schema, ledger, "projects", organization_id=org, name=f"Project {org_index}", status="active"
        )
        experiment = await insert(
            conn,
            schema,
            ledger,
            "experiments",
            organization_id=org,
            project_id=project,
            name=f"Experiment {org_index}",
            status="registered",
            owner_user_id=admin,
        )
        for s in range(3):
            label = f"S{org_index}-{s}"
            values = {"experiment_id": experiment, "status": "registered", "organism": "Mus musculus"}
            values["external_id" if rev >= 80 else "sample_id_external"] = label
            if rev >= 80:
                values["sample_id_external"] = label
            await insert(conn, schema, ledger, "samples", **values)
        file_ids = []
        for name in ("in.h5ad", "out.png", "unknown.csv"):
            file_ids.append(
                await insert(
                    conn,
                    schema,
                    ledger,
                    "files",
                    organization_id=org,
                    gcs_uri=f"gs://lab-{org_index}/{name}",
                    storage_uri=f"gs://lab-{org_index}/{name}",
                    filename=name,
                    file_type=name.rsplit(".", 1)[1],
                    experiment_id=experiment,
                    source_type="upload",
                )
            )
        sessions = _sessions_table(schema)
        session = await insert(
            conn,
            schema,
            ledger,
            sessions,
            user_id=member,
            organization_id=org,
            session_type="jupyter",
            resource_profile="small",
            cpu_cores=2,
            memory_gb=8,
            status="stopped",
            heartbeat_token=f"heartbeat-{org_index}",
        )
        for file_id, direction in zip(file_ids, ("input", "output", None)):
            await insert(
                conn,
                schema,
                ledger,
                "notebook_session_files",
                session_id=session,
                file_id=file_id,
                access_type=direction,
            )
        await insert(
            conn,
            schema,
            ledger,
            "session_credentials",
            user_id=member,
            organization_id=org,
            username=f"bench{org_index}",
            password_hash="legacy",
            ssh_private_key=f"-----BEGIN KEY {org_index}-----",
        )
        await insert(
            conn,
            schema,
            ledger,
            "slack_webhooks",
            organization_id=org,
            name=f"hook {org_index}",
            webhook_url=f"https://hooks.slack.example/{org_index}",
        )
        await insert(
            conn,
            schema,
            ledger,
            "audit_log",
            user_id=admin,
            entity_type="experiment",
            entity_id=experiment,
            action="create",
            organization_id=org,
        )

    # Configuration the lab changed after install. Migrations seeded these keys; the lab's value
    # must survive every later migration.
    for key, value in CONFIG_OVERRIDES.items():
        exists = await conn.fetchval("SELECT count(*) FROM platform_config WHERE key = $1", key)
        if exists:
            await conn.execute("UPDATE platform_config SET value = $2 WHERE key = $1", key, value)
        else:
            await conn.execute("INSERT INTO platform_config (key, value) VALUES ($1, $2)", key, value)
        ledger.config[key] = value
    return ledger


RENAMED = {"notebook_sessions": "compute_sessions"}


async def assert_preserved(conn: asyncpg.Connection, ledger: Ledger, decrypt) -> list[str]:
    """Every recorded row is still there with its values; returns the differences (empty when kept)."""
    problems = []
    for table, rows in ledger.rows.items():
        target = RENAMED.get(table, table)
        for row_id, values in rows.items():
            current = await conn.fetchrow(f'SELECT * FROM "{target}" WHERE id = $1', row_id)
            if current is None:
                problems.append(f"{target} {row_id} is gone")
                continue
            for column, expected in values.items():
                actual = current[column] if column in current.keys() else "<column dropped>"
                if (target, column) in ENCRYPTED and expected is not None:
                    actual = decrypt(actual)
                if isinstance(expected, str) and expected == "{}" and isinstance(actual, (dict, str)):
                    continue
                if actual != expected:
                    problems.append(f"{target} {row_id}.{column}: {expected!r} became {actual!r}")
    for key, value in ledger.config.items():
        actual = await conn.fetchval("SELECT value FROM platform_config WHERE key = $1", key)
        if actual != value:
            problems.append(f"platform_config {key}: {value!r} became {actual!r}")
    return problems


async def populate_legacy_nulls(conn: asyncpg.Connection, ledger: Ledger) -> dict[str, list[int]]:
    """One row in each table behind the 28 legacy-nullable columns, then every one of them NULL.

    Released schemas accept these NULLs and nothing records the lost values (plan 9, section 1), so
    an upgrade must carry them through unchanged and the candidate must read them. Returns the rows
    written, by table. For the latest released revision, after ``populate``.
    """
    from tests.test_migrations_apply import LEGACY_NULLABLE

    schema = await Schema.read(conn)
    org = min(ledger.rows["organizations"])
    user = min(ledger.rows["users"])
    experiment = min(ledger.rows["experiments"])
    sample = min(ledger.rows["samples"])
    file_id = min(ledger.rows["files"])
    ids: dict[str, int | None] = {}

    async def add(table: str, **values: Any) -> int | None:
        ids[table] = await insert(conn, schema, ledger, table, **values)
        return ids[table]

    env = await add("environments", organization_id=org, name="legacy-env", created_by_user_id=user)
    env_version = await add(
        "environment_versions",
        environment_id=env,
        version_number=1,
        definition_format="dockerfile",
        definition_content="FROM scratch",
        created_by_user_id=user,
    )
    pipeline = await add(
        "custom_pipelines", organization_id=org, name="legacy", pipeline_key="legacy-pipeline", created_by_user_id=user
    )
    await add(
        "custom_pipeline_versions",
        custom_pipeline_id=pipeline,
        version_number=1,
        code_source_type="inline",
        entrypoint_command="run.sh",
        environment_version_id=env_version,
        created_by_user_id=user,
    )
    await add("entity_snapshots", entity_type="sample", entity_id=sample, snapshot_json="{}")
    config = await add(
        "experiment_auto_runs",
        organization_id=org,
        experiment_id=experiment,
        pipeline_key="p",
        configured_by_user_id=user,
    )
    seq = await add("sequencing_batches", organization_id=org, code="LEGACY-SEQ", name="LEGACY-SEQ")
    await add("manifest_entries", sequencing_batch_id=seq, expected_filename="a.fastq.gz", expected_md5="0" * 32)
    await add("sample_batches", experiment_id=experiment, name="legacy prep")
    run = await add("pipeline_runs", organization_id=org, pipeline_name="legacy", experiment_id=experiment)
    await add("pipeline_run_input_files", pipeline_run_id=run, file_id=file_id)
    await conn.execute(
        "INSERT INTO pending_auto_runs (organization_id, auto_run_config_id, experiment_id, sample_id, "
        "sample_completed_at, scheduled_at) VALUES ($1, $2, $3, $4, now(), now())",
        org,
        config,
        experiment,
        sample,
    )
    await add("samplesheet_mappings", organization_id=org, pipeline_key="legacy-pipeline", scope="organization")
    await add("slack_channel_mappings", organization_id=org, channel_id="C1", channel_name="general")
    await add(
        "slack_installations",
        organization_id=org,
        team_id="T1",
        team_name="Lab",
        bot_token="xoxb-legacy",
        bot_user_id="U1",
        installed_by=user,
    )
    await conn.execute("INSERT INTO platform_config (key, value) VALUES ('legacy_unset_key', 'x')")

    # roles, session_credentials and notebook_session_files already have rows from populate().
    written: dict[str, list[int]] = {}
    for table, column in sorted(LEGACY_NULLABLE):
        # Every row, except platform_config, where only the added setting goes NULL (the others are
        # the install's real settings).
        where = " WHERE key = 'legacy_unset_key'" if table == "platform_config" else ""
        await conn.execute(f'UPDATE "{table}" SET "{column}" = NULL{where}')
        written.setdefault(table, [r[0] for r in await conn.fetch(f'SELECT id FROM "{table}"')])
    return written
