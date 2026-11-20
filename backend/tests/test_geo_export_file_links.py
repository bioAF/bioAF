"""GEO export file references follow Sample links (``sample_files``), never filename resemblance.

Candidate files are the Experiment's existing scope: the requested Organization's files stored under
``/experiments/{id}/``. Within it:

- linked to a selected Sample: listed once export-wide, and on each selected Sample row it is linked to;
- linked only to excluded Samples, or only to Samples outside this Experiment: omitted;
- no Sample links at all: an Experiment-level asset (listed export-wide, on no Sample row).
"""

import pytest
import pytest_asyncio

from tests.support.geo_package import read_package


async def _file(session, org_id, exp_id, filename, file_type, *, uri_exp_id=None):
    from app.models.file import File

    uri = f"gs://lab-raw/experiments/{uri_exp_id or exp_id}/{filename}"
    f = File(
        organization_id=org_id,
        gcs_uri=uri,
        storage_uri=uri,
        filename=filename,
        file_type=file_type,
        md5_checksum=f"md5-{filename}",
    )
    session.add(f)
    await session.flush()
    return f


async def _link(session, file, *samples):
    from app.models.sample import sample_files

    for s in samples:
        await session.execute(sample_files.insert().values(file_id=file.id, sample_id=s.id))


@pytest_asyncio.fixture
async def linked_experiment(client, admin_token, session, admin_user):
    from app.models.experiment import Experiment
    from app.models.organization import Organization
    from app.models.sample import Sample

    org_id = admin_user.organization_id
    resp = await client.post(
        "/api/experiments",
        json={"name": "GEO File Links", "description": "Link-owned files."},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]
    other = Experiment(name="Elsewhere", organization_id=org_id)
    session.add(other)
    await session.flush()

    p1 = Sample(experiment_id=exp_id, external_id="P1", organism="Homo sapiens", qc_status="pass")
    p2 = Sample(experiment_id=exp_id, external_id="P2", organism="Homo sapiens", qc_status="warning")
    f1 = Sample(experiment_id=exp_id, external_id="F1", organism="Homo sapiens", qc_status="fail")
    x1 = Sample(experiment_id=other.id, external_id="X1", organism="Homo sapiens", qc_status="pass")
    session.add_all([p1, p2, f1, x1])
    await session.flush()

    # Linked only to a selected Sample, with and without the Sample's name in the filename.
    await _link(session, await _file(session, org_id, exp_id, "P1_R1.fastq.gz", "fastq.gz"), p1)
    await _link(session, await _file(session, org_id, exp_id, "lane7_reads.fastq.gz", "fastq.gz"), p2)
    # Named like P1, but linked only to the failed Sample: the link decides, not the name.
    await _link(session, await _file(session, org_id, exp_id, "P1_lookalike.h5", "h5"), f1)
    # Linked to a selected and an excluded Sample.
    await _link(session, await _file(session, org_id, exp_id, "shared_matrix.h5", "h5"), p1, f1)
    # No Sample links: an Experiment-level asset.
    await _file(session, org_id, exp_id, "count_matrix.h5", "h5")
    # Linked, but only to another Experiment's Sample: not unlinked, so omitted.
    await _link(session, await _file(session, org_id, exp_id, "borrowed.h5", "h5"), x1)
    # Links cannot widen scope: another Experiment's storage, and another Organization's file.
    await _link(session, await _file(session, org_id, exp_id, "P1_elsewhere.h5", "h5", uri_exp_id=other.id), p1)
    foreign_org = Organization(name="Foreign Lab", setup_complete=True)
    session.add(foreign_org)
    await session.flush()
    await _link(session, await _file(session, foreign_org.id, exp_id, "P1_foreign.h5", "h5"), p1)
    await session.commit()
    return {"experiment_id": exp_id}


EXPECTED = {
    "exclude_failed": {
        "samples": {
            "P1": {"raw": ["P1_R1.fastq.gz"], "processed": ["shared_matrix.h5"]},
            "P2": {"raw": ["lane7_reads.fastq.gz"], "processed": []},
        },
        "files": ["P1_R1.fastq.gz", "count_matrix.h5", "lane7_reads.fastq.gz", "shared_matrix.h5"],
    },
    "include_all": {
        "samples": {
            "P1": {"raw": ["P1_R1.fastq.gz"], "processed": ["shared_matrix.h5"]},
            "P2": {"raw": ["lane7_reads.fastq.gz"], "processed": []},
            "F1": {"raw": [], "processed": ["P1_lookalike.h5", "shared_matrix.h5"]},
        },
        "files": ["P1_R1.fastq.gz", "P1_lookalike.h5", "count_matrix.h5", "lane7_reads.fastq.gz", "shared_matrix.h5"],
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize("qc_filter", ["exclude_failed", "include_all"])
async def test_file_references_follow_sample_links_and_qc_selection(
    client, comp_bio_token, linked_experiment, qc_filter
):
    expected = EXPECTED[qc_filter]
    url = f"/api/experiments/{linked_experiment['experiment_id']}/export/geo?qc_status_filter={qc_filter}"
    auth = {"Authorization": f"Bearer {comp_bio_token}"}

    readiness = await client.post(f"{url}&validate_only=true", headers=auth)
    assert readiness.status_code == 200, readiness.text
    assert sorted(f["filename"] for f in readiness.json()["file_manifest"]["files"]) == expected["files"]

    download = await client.post(url, headers=auth)
    assert download.status_code == 200, download.text
    pkg = read_package(download.content)

    assert {name: {k: sorted(v) for k, v in refs.items()} for name, refs in pkg.samples.items()} == expected["samples"]
    # Export-wide lists: each file exactly once, shared and Experiment-level files included.
    assert sorted(pkg.checksums) == expected["files"]
    assert sorted(pkg.manifest) == expected["files"]
    assert sorted(pkg.readme) == expected["files"]
    processed = [f for f in expected["files"] if f.endswith(".h5")]
    assert sorted(uri.rsplit("/", 1)[1] for uri in pkg.supplementary) == processed
    assert all(f"/experiments/{linked_experiment['experiment_id']}/" in uri for uri in pkg.supplementary)


@pytest.mark.asyncio
async def test_an_all_failed_experiment_keeps_only_unlinked_assets(client, comp_bio_token, linked_experiment, session):
    from sqlalchemy import update

    from app.models.sample import Sample

    await session.execute(
        update(Sample).where(Sample.experiment_id == linked_experiment["experiment_id"]).values(qc_status="fail")
    )
    await session.commit()
    resp = await client.post(
        f"/api/experiments/{linked_experiment['experiment_id']}/export/geo",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp.status_code == 200, resp.text
    pkg = read_package(resp.content)
    assert pkg.samples == {}
    assert pkg.checksums == ["count_matrix.h5"]
    assert pkg.readme == ["count_matrix.h5"]


@pytest.mark.asyncio
async def test_new_uploads_carry_their_selected_sample_into_geo(
    client, admin_token, comp_bio_token, session, admin_user
):
    """An upload made for a selected Sample is referenced on that Sample's row; one made only for the
    Experiment stays an Experiment-level asset. New data works under the link policy without repair."""
    import io
    from unittest.mock import patch

    from app.models.component import PlatformConfig
    from app.models.sample import Sample
    from tests.test_upload_ingest_bucket import _storage_adapter

    session.add(PlatformConfig(key="ingest_bucket_name", value="bioaf-ingest-test"))
    resp = await client.post(
        "/api/experiments",
        json={"name": "GEO Uploads", "description": "New uploads."},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]
    owner = Sample(experiment_id=exp_id, external_id="UP1", organism="Homo sapiens", qc_status="pass")
    session.add(owner)
    await session.commit()

    auth = {"Authorization": f"Bearer {admin_token}"}
    with patch("app.adapters.registry.get_storage_adapter", return_value=_storage_adapter()):
        for name, query in (("reads.fastq.gz", f"&sample_ids={owner.id}"), ("UP1_matrix.h5", "")):
            up = await client.post(
                f"/api/files/upload/simple?experiment_id={exp_id}{query}",
                files={"file": (name, io.BytesIO(b"data"), "application/octet-stream")},
                headers=auth,
            )
            assert up.status_code == 200, up.text

    # The Experiment-only upload is named after UP1, so it is reported until someone decides.
    report = await client.post(
        f"/api/experiments/{exp_id}/export/geo?validate_only=true",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert [f["filename"] for f in report.json()["file_association_findings"]] == ["UP1_matrix.h5"]
    finding = report.json()["file_association_findings"][0]
    ack = await client.post(
        f"/api/experiments/{exp_id}/export/geo/file-associations/acknowledgments",
        json={
            "file_id": finding["file_id"],
            "sample_ids": [owner.id],
            "fingerprint": finding["fingerprint"],
            "reason": "Matrix covers the whole experiment",
        },
        headers=auth,
    )
    assert ack.status_code == 200, ack.text

    download = await client.post(
        f"/api/experiments/{exp_id}/export/geo", headers={"Authorization": f"Bearer {comp_bio_token}"}
    )
    assert download.status_code == 200, download.text
    pkg = read_package(download.content)
    assert pkg.samples == {"UP1": {"raw": ["reads.fastq.gz"], "processed": []}}
    assert sorted(pkg.readme) == ["UP1_matrix.h5", "reads.fastq.gz"]


@pytest.mark.asyncio
@pytest.mark.parametrize("gone", ["deleted", "storage_deleted"])
async def test_deleted_files_are_never_geo_candidates(client, comp_bio_token, admin_token, session, admin_user, gone):
    """A file deleted from view, or whose stored bytes are gone, is not referenced, reported, or blocking."""
    from datetime import datetime, timezone

    from app.models.sample import Sample

    org_id = admin_user.organization_id
    resp = await client.post(
        "/api/experiments",
        json={"name": "GEO Deleted Files", "description": "Deleted records."},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = resp.json()["id"]
    s1 = Sample(experiment_id=exp_id, external_id="DEL1", organism="Homo sapiens", qc_status="pass")
    session.add(s1)
    await session.flush()
    live = await _file(session, org_id, exp_id, "DEL1_live_R1.fastq.gz", "fastq.gz")
    await _link(session, live, s1)
    linked_gone = await _file(session, org_id, exp_id, "DEL1_gone_R1.fastq.gz", "fastq.gz")
    await _link(session, linked_gone, s1)
    unlinked_gone = await _file(session, org_id, exp_id, "DEL1_legacy.h5", "h5")  # would be a finding if live
    for f in (linked_gone, unlinked_gone):
        if gone == "deleted":
            f.deleted_at = datetime.now(timezone.utc)
        else:
            f.storage_deleted = True
    await session.commit()

    auth = {"Authorization": f"Bearer {comp_bio_token}"}
    readiness = await client.post(f"/api/experiments/{exp_id}/export/geo?validate_only=true", headers=auth)
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["file_association_findings"] == []
    assert readiness.json()["download_blocked"] is False
    assert [f["filename"] for f in readiness.json()["file_manifest"]["files"]] == ["DEL1_live_R1.fastq.gz"]

    download = await client.post(f"/api/experiments/{exp_id}/export/geo", headers=auth)
    assert download.status_code == 200, download.text
    pkg = read_package(download.content)
    assert pkg.samples == {"DEL1": {"raw": ["DEL1_live_R1.fastq.gz"], "processed": []}}
    assert pkg.checksums == ["DEL1_live_R1.fastq.gz"]
    assert pkg.manifest == ["DEL1_live_R1.fastq.gz"]
    assert pkg.readme == ["DEL1_live_R1.fastq.gz"]
    assert pkg.supplementary == []

    # Nor can a deleted file be acknowledged: it is outside the candidate scope.
    ack = await client.post(
        f"/api/experiments/{exp_id}/export/geo/file-associations/acknowledgments",
        json={"file_id": unlinked_gone.id, "sample_ids": [s1.id], "fingerprint": "0" * 64, "reason": "gone"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert ack.status_code == 404, ack.text
