"""Sample import: CSV confirm, legacy CSV upload, and Google Sheet confirm.

All three entry points run one workflow. Each accepted row is one unit: the
sample, its custom fields, any batch it created, and its audit records commit
together or not at all, and a rejected row never shifts a later row's fields.
"""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from app.models.audit_log import AuditLog
from app.models.experiment import Experiment
from app.models.sample import Sample
from app.models.sample_batch import SampleBatch
from app.models.sample_custom_field import SampleCustomField
from app.services.csv_service import parse_sample_csv

ENTRY_POINTS = ["csv_confirm", "csv_upload", "sheet_confirm"]
SHEET_URL = "https://docs.google.com/spreadsheets/d/abc/edit"


# --- parser ------------------------------------------------------------------


def test_a_rejected_row_does_not_shift_later_custom_fields():
    content = b"external_id,qc_status,tag\nA,pass,alpha\nX,bogus,lost\nB,pass,beta\nC,pass,gamma\n"
    samples, errors, custom_rows = parse_sample_csv(content, 1, column_mappings={"tag": "custom:tag"})
    assert [s.external_id for s in samples] == ["A", "B", "C"]
    assert custom_rows == [{"tag": "alpha"}, {"tag": "beta"}, {"tag": "gamma"}]
    assert len(errors) == 1 and errors[0].startswith("Row 3:")


@pytest.mark.parametrize(
    "rows,kept",
    [
        ("X,bogus,x\nA,pass,a\nB,pass,b\n", ["A", "B"]),  # failure before
        ("A,pass,a\nB,pass,b\nX,bogus,x\n", ["A", "B"]),  # failure after
        ("X,bogus,x\nA,pass,a\nY,bogus,y\nB,pass,b\nZ,bogus,z\n", ["A", "B"]),  # several
    ],
)
def test_failures_anywhere_keep_each_sample_with_its_own_fields(rows, kept):
    content = ("external_id,qc_status,tag\n" + rows).encode()
    samples, errors, custom_rows = parse_sample_csv(content, 1, column_mappings={"tag": "custom:tag"})
    assert [s.external_id for s in samples] == kept
    assert custom_rows == [{"tag": k.lower()} for k in kept]
    assert len(errors) == rows.count("bogus")


def test_a_numeric_conversion_error_keeps_the_sample_without_that_field():
    """Recorded behavior (owner decision pending): the row is accepted, the bad field omitted."""
    content = b"external_id,cell_count,tag\nA,lots,alpha\nB,5,beta\n"
    samples, errors, custom_rows = parse_sample_csv(content, 1, column_mappings={"tag": "custom:tag"})
    assert [(s.external_id, s.cell_count) for s in samples] == [("A", None), ("B", 5)]
    assert custom_rows == [{"tag": "alpha"}, {"tag": "beta"}]
    assert errors == ["Row 2: Invalid numeric value 'lots' for cell_count"]


def test_error_row_labels_follow_the_recorded_conventions():
    """Conversion errors name the CSV line; validation errors count non-empty records from 2.

    An empty line before the invalid record makes the two conventions differ.
    """
    content = b"external_id,qc_status,cell_count\n,,\nX,bogus,1\nY,pass,many\n"
    samples, errors, _ = parse_sample_csv(content, 1)
    assert [s.external_id for s in samples] == ["Y"]
    assert errors == [
        "Row 4: Invalid numeric value 'many' for cell_count",
        "Row 2: qc_status: Value error, qc_status must be 'pass', 'warning', or 'fail'",
    ]


# --- API fixtures and helpers ------------------------------------------------


@pytest.fixture
def headers(admin_token) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest_asyncio.fixture
async def experiment(session, admin_user) -> SimpleNamespace:
    """Just the id: tests expire the session to read back what was persisted."""
    exp = Experiment(organization_id=admin_user.organization_id, name="Import", column_aliases={"tag": "custom:tag"})
    session.add(exp)
    await session.commit()
    return SimpleNamespace(id=exp.id)


async def _import(client, headers, entry: str, experiment_id: int, csv_text: str):
    content = csv_text.encode()
    if entry == "csv_confirm":
        return await client.post(
            f"/api/experiments/{experiment_id}/samples/upload/confirm",
            headers=headers,
            files={"file": ("s.csv", io.BytesIO(content), "text/csv")},
            data={"column_mappings": "{}"},
        )
    if entry == "csv_upload":
        return await client.post(
            f"/api/experiments/{experiment_id}/samples/upload",
            headers=headers,
            files={"file": ("s.csv", io.BytesIO(content), "text/csv")},
        )
    with patch("app.api.sheets_import._read_sheet_as_csv", AsyncMock(return_value=(content, "Sheet1"))):
        return await client.post(
            f"/api/v1/sheets/{experiment_id}/samples/confirm", headers=headers, json={"sheet_url": SHEET_URL}
        )


async def _persisted(session, experiment_id: int) -> dict[str, dict[str, str]]:
    """external_id -> its custom fields, read back from the database."""
    session.expire_all()
    rows = (
        await session.execute(
            select(Sample.external_id, SampleCustomField.field_name, SampleCustomField.field_value)
            .outerjoin(SampleCustomField, SampleCustomField.sample_id == Sample.id)
            .where(Sample.experiment_id == experiment_id)
        )
    ).all()
    out: dict[str, dict[str, str]] = {}
    for external_id, name, value in rows:
        out.setdefault(external_id, {})
        if name is not None:
            out[external_id][name] = value
    return out


async def _count(session, model, *where) -> int:
    return (await session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


# --- alignment and response shapes -------------------------------------------


@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.asyncio
async def test_each_import_stores_custom_fields_on_their_own_samples(client, session, headers, experiment, entry):
    csv_text = "external_id,qc_status,tag\nA,pass,alpha\nX,bogus,lost\nB,pass,beta\nC,pass,gamma\n"
    response = await _import(client, headers, entry, experiment.id, csv_text)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created_count"] == 3
    assert body["error_count"] == 1
    assert await _persisted(session, experiment.id) == {
        "A": {"tag": "alpha"},
        "B": {"tag": "beta"},
        "C": {"tag": "gamma"},
    }


@pytest.mark.parametrize(
    "entry,keys",
    [
        ("csv_confirm", {"created_count", "error_count", "errors", "custom_fields_created"}),
        ("csv_upload", {"created_count", "error_count", "errors"}),
        ("sheet_confirm", {"created_count", "error_count", "errors", "custom_fields_created"}),
    ],
)
@pytest.mark.asyncio
async def test_each_entry_point_keeps_its_response_keys(client, headers, experiment, entry, keys):
    response = await _import(client, headers, entry, experiment.id, "external_id,tag\nA,alpha\n")
    assert response.status_code == 200
    assert set(response.json()) == keys
    if "custom_fields_created" in keys:
        assert response.json()["custom_fields_created"] == ["tag"]


@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.asyncio
async def test_all_invalid_rows_answer_400_with_the_errors(client, session, headers, experiment, entry):
    response = await _import(client, headers, entry, experiment.id, "external_id,qc_status\nX,bogus\n")
    assert response.status_code == 400
    assert response.json() == {
        "detail": {"errors": ["Row 2: qc_status: Value error, qc_status must be 'pass', 'warning', or 'fail'"]}
    }
    assert await _persisted(session, experiment.id) == {}


@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.asyncio
async def test_an_empty_file_answers_400_and_a_header_only_file_creates_nothing(client, headers, experiment, entry):
    empty = await _import(client, headers, entry, experiment.id, "")
    assert empty.status_code == 400
    assert empty.json() == {"detail": {"errors": ["File is empty"]}}
    header_only = await _import(client, headers, entry, experiment.id, "external_id,tag\n")
    assert header_only.status_code == 200
    assert (header_only.json()["created_count"], header_only.json()["errors"]) == (0, [])


# --- row units ---------------------------------------------------------------


@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.asyncio
async def test_a_duplicate_sample_id_rolls_back_only_its_row(client, session, headers, experiment, entry):
    """A unique-index conflict on flush used to poison the request; now only that row is lost."""
    session.add(Sample(experiment_id=experiment.id, external_id="TAKEN", status="registered"))
    await session.commit()
    csv_text = "external_id,sample_batch,tag\nA,batch-a,alpha\nTAKEN,batch-dup,dup\nB,batch-b,beta\nB,batch-b2,again\n"
    response = await _import(client, headers, entry, experiment.id, csv_text)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created_count"] == 2
    assert body["errors"] == [
        "Sample 2: Sample ID “TAKEN” already exists in this experiment.",
        "Sample 4: Sample ID “B” already exists in this experiment.",
    ]
    assert await _persisted(session, experiment.id) == {"TAKEN": {}, "A": {"tag": "alpha"}, "B": {"tag": "beta"}}
    batches = set(
        (await session.execute(select(SampleBatch.name).where(SampleBatch.experiment_id == experiment.id))).scalars()
    )
    assert batches == {"batch-a", "batch-b"}  # the failed rows' new batches did not survive
    assert await _count(session, AuditLog, AuditLog.entity_type == "sample", AuditLog.action == "create") == 2


@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.asyncio
async def test_a_domain_failure_after_the_sample_is_written_rolls_back_the_whole_row(
    client, session, headers, experiment, entry
):
    """A custom field name the column cannot hold fails after the sample, batch and audit row exist."""
    long_name = "n" * 256
    experiment_row = await session.get(Experiment, experiment.id)
    experiment_row.column_aliases = {"tag": "custom:tag", "long": f"custom:{long_name}"}
    await session.commit()
    csv_text = "external_id,sample_batch,tag,long\nA,batch-a,alpha,\nX,batch-x,lost,v\nB,batch-b,beta,\n"
    response = await _import(client, headers, entry, experiment.id, csv_text)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created_count"] == 2
    assert body["errors"] == ["Sample 2: Custom field name is longer than 255 characters."]
    assert await _persisted(session, experiment.id) == {"A": {"tag": "alpha"}, "B": {"tag": "beta"}}
    batches = set(
        (await session.execute(select(SampleBatch.name).where(SampleBatch.experiment_id == experiment.id))).scalars()
    )
    assert batches == {"batch-a", "batch-b"}
    assert await _count(session, AuditLog, AuditLog.entity_type == "sample", AuditLog.action == "create") == 2


@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.asyncio
async def test_a_custom_field_conflict_on_flush_rolls_back_its_sample(client, session, headers, experiment, entry):
    await session.execute(
        text(
            "CREATE FUNCTION refuse_boom() RETURNS trigger AS $$ BEGIN "
            "IF NEW.field_value = 'boom' THEN RAISE EXCEPTION 'conflict' USING ERRCODE = 'unique_violation'; "
            "END IF; RETURN NEW; END $$ LANGUAGE plpgsql"
        )
    )
    await session.execute(
        text(
            "CREATE TRIGGER refuse_boom BEFORE INSERT ON sample_custom_fields FOR EACH ROW EXECUTE FUNCTION refuse_boom()"
        )
    )
    await session.commit()
    csv_text = "external_id,tag\nA,alpha\nX,boom\nB,beta\n"
    response = await _import(client, headers, entry, experiment.id, csv_text)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created_count"] == 2
    assert body["errors"] == ["Sample 2: It conflicts with a record that already exists."]
    assert await _persisted(session, experiment.id) == {"A": {"tag": "alpha"}, "B": {"tag": "beta"}}


@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.asyncio
async def test_a_failed_final_commit_leaves_no_rows(client, session, headers, experiment, entry):
    await session.execute(
        text(
            "CREATE FUNCTION refuse_at_commit() RETURNS trigger AS $$ BEGIN "
            "IF NEW.external_id = 'LATE' THEN RAISE EXCEPTION 'refused at commit'; END IF; RETURN NEW; END $$ "
            "LANGUAGE plpgsql"
        )
    )
    await session.execute(
        text(
            "CREATE CONSTRAINT TRIGGER refuse_at_commit AFTER INSERT ON samples DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION refuse_at_commit()"
        )
    )
    await session.commit()
    with pytest.raises(Exception, match="refused at commit"):
        await _import(client, headers, entry, experiment.id, "external_id,tag\nA,alpha\nLATE,late\n")
    assert await _persisted(session, experiment.id) == {}
    assert await _count(session, AuditLog, AuditLog.entity_type == "sample") == 0


@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.asyncio
async def test_an_unrecognized_database_error_fails_the_request_with_no_rows(
    client, session, headers, experiment, entry
):
    """A value the column cannot hold is not a recognized row conflict: the request fails whole."""
    too_long = "Z" * 300
    with pytest.raises(Exception, match="too long"):
        await _import(client, headers, entry, experiment.id, f"external_id,tag\nA,alpha\n{too_long},z\n")
    assert await _persisted(session, experiment.id) == {}


# --- Organization boundary ---------------------------------------------------


@pytest_asyncio.fixture
async def foreign_experiment(session) -> Experiment:
    from app.models.organization import Organization

    org = Organization(name="Elsewhere", setup_complete=True)
    session.add(org)
    await session.flush()
    exp = Experiment(organization_id=org.id, name="Theirs", column_aliases={"secret": "custom:secret"})
    session.add(exp)
    await session.commit()
    return exp


@pytest.mark.parametrize("suffix", ["preview", "confirm"])
@pytest.mark.parametrize("target", ["foreign", "missing"])
@pytest.mark.asyncio
async def test_sheet_sample_import_answers_404_before_reading_the_sheet(
    client, session, headers, foreign_experiment, suffix, target
):
    experiment_id = foreign_experiment.id if target == "foreign" else foreign_experiment.id + 1000
    reader = AsyncMock(return_value=(b"external_id\nSMUGGLED\n", "Sheet1"))
    with patch("app.api.sheets_import._read_sheet_as_csv", reader):
        response = await client.post(
            f"/api/v1/sheets/{experiment_id}/samples/{suffix}", headers=headers, json={"sheet_url": SHEET_URL}
        )
    assert response.status_code == 404
    assert response.json() == {"detail": "Experiment not found"}
    reader.assert_not_awaited()
    assert await _count(session, Sample, Sample.experiment_id == foreign_experiment.id) == 0


@pytest.mark.asyncio
async def test_sheet_sample_import_still_works_in_the_callers_organization(client, session, headers, experiment):
    reader = AsyncMock(return_value=(b"external_id,tag\nA,alpha\n", "Sheet1"))
    with patch("app.api.sheets_import._read_sheet_as_csv", reader):
        preview = await client.post(
            f"/api/v1/sheets/{experiment.id}/samples/preview", headers=headers, json={"sheet_url": SHEET_URL}
        )
        confirm = await client.post(
            f"/api/v1/sheets/{experiment.id}/samples/confirm",
            headers=headers,
            json={"sheet_url": SHEET_URL, "column_mappings": {}},
        )
    assert preview.status_code == 200
    assert {c["mapped_to"] for c in preview.json()["recognized_columns"]} == {"external_id", "custom:tag"}
    assert confirm.status_code == 200
    assert confirm.json()["created_count"] == 1
    assert await _persisted(session, experiment.id) == {"A": {"tag": "alpha"}}


@pytest.mark.asyncio
async def test_a_taken_batch_position_rolls_back_only_its_row(client, session, admin_user, headers, experiment):
    from app.models.sequencing_batch import SequencingBatch

    batch = SequencingBatch(organization_id=admin_user.organization_id, name="run", code="RUN1", status="pending")
    session.add(batch)
    await session.flush()
    session.add(
        Sample(
            experiment_id=experiment.id,
            external_id="HELD",
            sequencing_batch_id=batch.id,
            sequencing_batch_position=1,
            status="registered",
        )
    )
    await session.commit()
    csv_text = "external_id,sequencing_batch,sequencing_batch_position\nA,RUN1,1\nB,RUN1,2\n"
    response = await _import(client, headers, "csv_confirm", experiment.id, csv_text)
    assert response.status_code == 200, response.text
    assert response.json()["errors"] == ["Sample 1: Position 1 in sequencing batch “RUN1” is already taken."]
    assert set(await _persisted(session, experiment.id)) == {"HELD", "B"}


def test_a_conflict_on_an_assigned_position_does_not_name_a_missing_number():
    from sqlalchemy.exc import IntegrityError

    from app.schemas.sample import SampleCreate
    from app.services.csv_service import ParsedSampleRow
    from app.services.sample_import_service import _unique_conflict_text

    class Cause(Exception):
        constraint_name = "uq_sample_seq_batch_position"

    cause = Cause()
    orig = Exception("dup")
    orig.sqlstate = "23505"  # type: ignore[attr-defined]
    orig.__cause__ = cause
    row = ParsedSampleRow(2, SampleCreate(external_id="A", sequencing_batch_code="RUN1"))
    text_ = _unique_conflict_text(IntegrityError("insert", {}, orig), row)
    assert text_ == "Its position in sequencing batch “RUN1” is already taken."
