"""Legacy GEO file associations: findings, the download conflict, and their in-app resolution.

Before links decided ownership, a file whose name started with a Sample's external ID was referenced on
that Sample's row. An unlinked candidate file that still matches a currently selected Sample that way is a
finding: evidence of a possibly lost reference, not proof of ownership. Readiness reports findings and
stays usable; a download recomputes them and refuses (409 ``geo_file_associations_unresolved``) until each
reported file/Sample pair is either linked (the existing link operation) or acknowledged as
Experiment-level.
"""

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from app.models.file import File
from app.services.auth_service import AuthService
from tests.support.geo_package import read_package

ACK_PATH = "/api/experiments/{exp}/export/geo/file-associations/acknowledgments"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _custom_role_user(session, org_id: int, email: str, grants: list[tuple[str, str]]) -> dict:
    from app.models.user import User
    from app.services import role_service

    role = await role_service.create_role(session, org_id, f"custom-{email}", permissions=grants)
    user = User(
        email=email,
        password_hash=AuthService.hash_password("customrolepass1"),
        role_id=role.id,
        organization_id=org_id,
        status="active",
    )
    session.add(user)
    await session.commit()
    role_service.invalidate_cache()
    return _bearer(AuthService.create_token(user.id, user.email, role.id, org_id, role_name=role.name))


async def _unlinked(session, org_id, exp_id, filename, file_type):
    uri = f"gs://lab-raw/experiments/{exp_id}/{filename}"
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


@pytest_asyncio.fixture
async def legacy(client, admin_token, session, admin_user):
    """Legacy uploads that never created links, named after passing and failed Samples."""
    from app.models.sample import Sample

    org_id = admin_user.organization_id
    resp = await client.post(
        "/api/experiments",
        json={"name": "Legacy GEO", "description": "Uploads without links."},
        headers=_bearer(admin_token),
    )
    exp_id = resp.json()["id"]
    lib1 = Sample(experiment_id=exp_id, external_id="LIB1", organism="Homo sapiens", qc_status="pass")
    lib10 = Sample(experiment_id=exp_id, external_id="LIB10", organism="Homo sapiens", qc_status=None)
    libf = Sample(experiment_id=exp_id, external_id="LIBF", organism="Homo sapiens", qc_status="fail")
    session.add_all([lib1, lib10, libf])
    await session.flush()
    both = await _unlinked(session, org_id, exp_id, "LIB10_R1.fastq.gz", "fastq.gz")  # matches LIB1 and LIB10
    failed = await _unlinked(session, org_id, exp_id, "LIBF_R1.fastq.gz", "fastq.gz")  # matches only LIBF
    await _unlinked(session, org_id, exp_id, "matrix.h5", "h5")  # matches nobody: plain Experiment-level asset
    await session.commit()
    return {
        "experiment_id": exp_id,
        "org_id": org_id,
        "samples": {"LIB1": lib1.id, "LIB10": lib10.id, "LIBF": libf.id},
        "files": {"both": both.id, "failed": failed.id},
    }


def _export_url(exp_id: int, qc_filter: str = "exclude_failed", validate_only: bool = False) -> str:
    url = f"/api/experiments/{exp_id}/export/geo?qc_status_filter={qc_filter}"
    return url + "&validate_only=true" if validate_only else url


async def _readiness(client, token, exp_id, qc_filter="exclude_failed") -> dict:
    resp = await client.post(_export_url(exp_id, qc_filter, validate_only=True), headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _pairs(report: dict) -> dict[str, list[str]]:
    return {f["filename"]: sorted(s["external_id"] for s in f["samples"]) for f in report["file_association_findings"]}


async def _acknowledge(client, token, exp_id, finding: dict, reason="Shared lane file, not owned by one Sample"):
    return await client.post(
        ACK_PATH.format(exp=exp_id),
        json={
            "file_id": finding["file_id"],
            "sample_ids": [s["sample_id"] for s in finding["samples"]],
            "fingerprint": finding["fingerprint"],
            "reason": reason,
        },
        headers=_bearer(token),
    )


def _finding(report: dict, filename: str) -> dict:
    return next(f for f in report["file_association_findings"] if f["filename"] == filename)


# --- readiness and download -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readiness_reports_findings_for_selected_samples_only(client, comp_bio_token, legacy):
    excluded = await _readiness(client, comp_bio_token, legacy["experiment_id"])
    assert _pairs(excluded) == {"LIB10_R1.fastq.gz": ["LIB1", "LIB10"]}
    assert excluded["download_blocked"] is True
    finding = _finding(excluded, "LIB10_R1.fastq.gz")
    assert finding["file_id"] == legacy["files"]["both"]
    assert finding["resolved"] is False and finding["fingerprint"]
    assert all(s["acknowledged"] is False for s in finding["samples"])
    # Metadata summary counts are unchanged by association findings.
    assert excluded["summary"]["total_fields"] > 0

    everyone = await _readiness(client, comp_bio_token, legacy["experiment_id"], "include_all")
    assert _pairs(everyone) == {"LIB10_R1.fastq.gz": ["LIB1", "LIB10"], "LIBF_R1.fastq.gz": ["LIBF"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("qc_filter", ["exclude_failed", "include_all"])
async def test_download_refuses_unresolved_findings_without_a_prior_readiness_check(
    client, comp_bio_token, legacy, session, qc_filter
):
    resp = await client.post(_export_url(legacy["experiment_id"], qc_filter), headers=_bearer(comp_bio_token))

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "geo_file_associations_unresolved"
    assert body["details"]["experiment_id"] == legacy["experiment_id"]
    assert legacy["files"]["both"] in {f["file_id"] for f in body["details"]["findings"]}
    exported = await session.execute(text("SELECT count(*) FROM audit_log WHERE entity_type = 'geo_export'"))
    assert exported.scalar_one() == 0


@pytest.mark.asyncio
async def test_a_file_matching_only_a_failed_sample_blocks_include_all_but_not_exclusion(
    client, comp_bio_token, admin_token, legacy
):
    # Resolve the passing Samples' file by link; only the failed-only match remains.
    linked = await client.post(
        f"/api/files/{legacy['files']['both']}/link",
        json={"sample_id": legacy["samples"]["LIB10"]},
        headers=_bearer(admin_token),
    )
    assert linked.status_code == 200, linked.text

    ok = await client.post(_export_url(legacy["experiment_id"]), headers=_bearer(comp_bio_token))
    assert ok.status_code == 200, ok.text
    blocked = await client.post(_export_url(legacy["experiment_id"], "include_all"), headers=_bearer(comp_bio_token))
    assert blocked.status_code == 409
    assert [f["filename"] for f in blocked.json()["details"]["findings"]] == ["LIBF_R1.fastq.gz"]


@pytest.mark.asyncio
async def test_a_confirmed_link_resolves_the_finding_and_owns_the_reference(
    client, comp_bio_token, admin_token, legacy
):
    resp = await client.post(
        f"/api/files/{legacy['files']['both']}/link",
        json={"sample_id": legacy["samples"]["LIB10"]},
        headers=_bearer(admin_token),
    )
    assert resp.status_code == 200

    report = await _readiness(client, comp_bio_token, legacy["experiment_id"])
    assert report["file_association_findings"] == [] and report["download_blocked"] is False

    download = await client.post(_export_url(legacy["experiment_id"]), headers=_bearer(comp_bio_token))
    assert download.status_code == 200, download.text
    pkg = read_package(download.content)
    # The link, not the name: LIB1 (a name prefix of the file) gets nothing.
    assert pkg.samples["LIB10"]["raw"] == ["LIB10_R1.fastq.gz"]
    assert pkg.samples["LIB1"]["raw"] == []
    # Unlinked files stay Experiment-level whatever they are named, including one matching only LIBF.
    assert sorted(pkg.checksums) == ["LIB10_R1.fastq.gz", "LIBF_R1.fastq.gz", "matrix.h5"]


@pytest.mark.asyncio
async def test_an_acknowledged_file_stays_experiment_level_and_unblocks_the_download(
    client, comp_bio_token, admin_token, legacy, session
):
    report = await _readiness(client, admin_token, legacy["experiment_id"])
    resp = await _acknowledge(client, admin_token, legacy["experiment_id"], _finding(report, "LIB10_R1.fastq.gz"))
    assert resp.status_code == 200, resp.text

    after = await _readiness(client, comp_bio_token, legacy["experiment_id"])
    finding = _finding(after, "LIB10_R1.fastq.gz")
    assert finding["resolved"] is True and all(s["acknowledged"] for s in finding["samples"])
    assert after["download_blocked"] is False

    download = await client.post(_export_url(legacy["experiment_id"]), headers=_bearer(comp_bio_token))
    assert download.status_code == 200, download.text
    pkg = read_package(download.content)
    assert pkg.samples["LIB1"]["raw"] == [] and pkg.samples["LIB10"]["raw"] == []
    # Unlinked files stay Experiment-level whatever they are named, including one matching only LIBF.
    assert sorted(pkg.checksums) == ["LIB10_R1.fastq.gz", "LIBF_R1.fastq.gz", "matrix.h5"]

    # Acknowledging creates no Sample link and is audited.
    links = await session.execute(
        text("SELECT count(*) FROM sample_files WHERE file_id = :f"), {"f": finding["file_id"]}
    )
    assert links.scalar_one() == 0
    audited = await session.execute(
        text("SELECT count(*) FROM audit_log WHERE entity_type = 'file' AND action = 'geo_association_acknowledged'")
    )
    assert audited.scalar_one() == 1


@pytest.mark.asyncio
async def test_repeating_an_acknowledgment_is_idempotent(client, admin_token, legacy, session):
    report = await _readiness(client, admin_token, legacy["experiment_id"])
    finding = _finding(report, "LIB10_R1.fastq.gz")
    for _ in range(2):
        resp = await _acknowledge(client, admin_token, legacy["experiment_id"], finding)
        assert resp.status_code == 200, resp.text
    rows = await session.execute(text("SELECT count(*) FROM geo_file_association_acknowledgments"))
    assert rows.scalar_one() == 2  # one per reported file/Sample pair


@pytest.mark.asyncio
async def test_a_stale_acknowledgment_is_refused(client, admin_token, legacy, session):
    report = await _readiness(client, admin_token, legacy["experiment_id"])
    finding = _finding(report, "LIB10_R1.fastq.gz")
    # The Sample's identity changes after the user reviewed the finding.
    await session.execute(
        text("UPDATE samples SET external_id = 'LIB1B' WHERE id = :s"), {"s": legacy["samples"]["LIB1"]}
    )
    await session.commit()

    resp = await _acknowledge(client, admin_token, legacy["experiment_id"], finding)
    assert resp.status_code == 409, resp.text
    rows = await session.execute(text("SELECT count(*) FROM geo_file_association_acknowledgments"))
    assert rows.scalar_one() == 0


@pytest.mark.asyncio
async def test_a_changed_file_invalidates_its_acknowledgment(client, comp_bio_token, admin_token, legacy, session):
    report = await _readiness(client, admin_token, legacy["experiment_id"])
    assert (
        await _acknowledge(client, admin_token, legacy["experiment_id"], _finding(report, "LIB10_R1.fastq.gz"))
    ).status_code == 200

    await session.execute(
        text("UPDATE files SET filename = 'LIB10_R1_v2.fastq.gz' WHERE id = :f"), {"f": legacy["files"]["both"]}
    )
    await session.commit()

    after = await _readiness(client, comp_bio_token, legacy["experiment_id"])
    assert _finding(after, "LIB10_R1_v2.fastq.gz")["resolved"] is False
    assert after["download_blocked"] is True
    blocked = await client.post(_export_url(legacy["experiment_id"]), headers=_bearer(comp_bio_token))
    assert blocked.status_code == 409


@pytest.mark.asyncio
async def test_a_newly_affected_sample_needs_its_own_acknowledgment(
    client, comp_bio_token, admin_token, legacy, session
):
    from app.models.sample import Sample

    report = await _readiness(client, admin_token, legacy["experiment_id"])
    assert (
        await _acknowledge(client, admin_token, legacy["experiment_id"], _finding(report, "LIB10_R1.fastq.gz"))
    ).status_code == 200
    session.add(
        Sample(experiment_id=legacy["experiment_id"], external_id="LIB10_R", organism="Homo sapiens", qc_status="pass")
    )
    await session.commit()

    after = await _readiness(client, comp_bio_token, legacy["experiment_id"])
    finding = _finding(after, "LIB10_R1.fastq.gz")
    assert {s["external_id"]: s["acknowledged"] for s in finding["samples"]} == {
        "LIB1": True,
        "LIB10": True,
        "LIB10_R": False,
    }
    assert finding["resolved"] is False and after["download_blocked"] is True


@pytest.mark.asyncio
async def test_acknowledging_more_or_other_pairs_than_reported_is_refused(client, admin_token, legacy, session):
    report = await _readiness(client, admin_token, legacy["experiment_id"], "include_all")
    finding = _finding(report, "LIB10_R1.fastq.gz")
    forged = {**finding, "samples": finding["samples"] + [{"sample_id": legacy["samples"]["LIBF"]}]}
    resp = await _acknowledge(client, admin_token, legacy["experiment_id"], forged)
    assert resp.status_code == 409, resp.text
    rows = await session.execute(text("SELECT count(*) FROM geo_file_association_acknowledgments"))
    assert rows.scalar_one() == 0


# --- authorization and isolation --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_permission_alone_cannot_acknowledge_or_link(client, admin_user, session, legacy):
    downloader = await _custom_role_user(
        session, admin_user.organization_id, "downloader@t.test", [("files", "view"), ("files", "download")]
    )
    report = (await client.post(_export_url(legacy["experiment_id"], validate_only=True), headers=downloader)).json()
    finding = _finding(report, "LIB10_R1.fastq.gz")

    ack = await client.post(
        ACK_PATH.format(exp=legacy["experiment_id"]),
        json={
            "file_id": finding["file_id"],
            "sample_ids": [s["sample_id"] for s in finding["samples"]],
            "fingerprint": finding["fingerprint"],
            "reason": "no",
        },
        headers=downloader,
    )
    assert ack.status_code == 403
    link = await client.post(
        f"/api/files/{finding['file_id']}/link", json={"sample_id": legacy["samples"]["LIB10"]}, headers=downloader
    )
    assert link.status_code == 403
    assert (await session.execute(text("SELECT count(*) FROM geo_file_association_acknowledgments"))).scalar_one() == 0
    assert (await session.execute(text("SELECT count(*) FROM sample_files"))).scalar_one() == 0


@pytest.mark.asyncio
async def test_another_organization_cannot_see_acknowledge_or_link(client, admin_token, legacy, session):
    from app.models.organization import Organization
    from app.services.bootstrap_roles import seed_builtin_roles

    report = await _readiness(client, admin_token, legacy["experiment_id"])
    finding = _finding(report, "LIB10_R1.fastq.gz")

    foreign = Organization(name="Foreign Lab", setup_complete=True)
    session.add(foreign)
    await session.flush()
    await seed_builtin_roles(session, foreign.id)
    await session.commit()
    outsider = await _custom_role_user(
        session, foreign.id, "outsider@t.test", [("files", "view"), ("files", "download"), ("files", "edit")]
    )

    readiness = await client.post(_export_url(legacy["experiment_id"], validate_only=True), headers=outsider)
    assert readiness.status_code == 404
    ack = await client.post(
        ACK_PATH.format(exp=legacy["experiment_id"]),
        json={
            "file_id": finding["file_id"],
            "sample_ids": [s["sample_id"] for s in finding["samples"]],
            "fingerprint": finding["fingerprint"],
            "reason": "outsider",
        },
        headers=outsider,
    )
    assert ack.status_code == 404
    link = await client.post(
        f"/api/files/{finding['file_id']}/link", json={"sample_id": legacy["samples"]["LIB10"]}, headers=outsider
    )
    assert link.status_code == 404
    assert (await session.execute(text("SELECT count(*) FROM geo_file_association_acknowledgments"))).scalar_one() == 0
    assert (await session.execute(text("SELECT count(*) FROM sample_files"))).scalar_one() == 0


@pytest.mark.asyncio
async def test_the_link_operation_refuses_a_sample_from_another_organization(
    client, admin_token, admin_user, legacy, session
):
    """The existing link operation verifies the target Sample, not only the file, belongs to the caller."""
    from app.models.experiment import Experiment
    from app.models.organization import Organization
    from app.models.sample import Sample

    foreign = Organization(name="Other Lab", setup_complete=True)
    session.add(foreign)
    await session.flush()
    exp = Experiment(name="Theirs", organization_id=foreign.id)
    session.add(exp)
    await session.flush()
    theirs = Sample(experiment_id=exp.id, external_id="THEIRS", organism="Homo sapiens")
    session.add(theirs)
    await session.commit()

    resp = await client.post(
        f"/api/files/{legacy['files']['both']}/link", json={"sample_id": theirs.id}, headers=_bearer(admin_token)
    )
    assert resp.status_code == 404, resp.text
    assert (await session.execute(text("SELECT count(*) FROM sample_files"))).scalar_one() == 0


@pytest.mark.asyncio
async def test_an_acknowledgment_for_a_file_outside_the_experiment_scope_is_refused(
    client, admin_token, legacy, session
):
    report = await _readiness(client, admin_token, legacy["experiment_id"])
    finding = _finding(report, "LIB10_R1.fastq.gz")
    outside = await _unlinked(
        session, legacy["org_id"], legacy["experiment_id"] + 1000, "LIB10_other.fastq.gz", "fastq.gz"
    )
    await session.commit()

    resp = await client.post(
        ACK_PATH.format(exp=legacy["experiment_id"]),
        json={
            "file_id": outside.id,
            "sample_ids": [s["sample_id"] for s in finding["samples"]],
            "fingerprint": finding["fingerprint"],
            "reason": "out of scope",
        },
        headers=_bearer(admin_token),
    )
    assert resp.status_code == 404, resp.text


# --- failure handling and availability --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_detector_builds_no_package(session, legacy, monkeypatch):
    from app.services.geo import file_associations, geo_export_service

    built = []

    def boom(*args, **kwargs):
        raise RuntimeError("detector unavailable")

    monkeypatch.setattr(file_associations, "detect_findings", boom)

    def record_build(*args, **kwargs):
        built.append(1)
        return b""

    monkeypatch.setattr(geo_export_service, "generate_geo_workbook", record_build)

    from app.exceptions import GeoAssociationCheckUnavailableError

    with pytest.raises(GeoAssociationCheckUnavailableError):
        await geo_export_service.GeoExportService.export(session, legacy["experiment_id"], legacy["org_id"])
    assert built == []


@pytest.mark.asyncio
async def test_an_unavailable_check_refuses_readiness_and_download_over_http(
    client, comp_bio_token, legacy, monkeypatch
):
    from app.services.geo import file_associations

    def boom(*args, **kwargs):
        raise RuntimeError("detector unavailable")

    monkeypatch.setattr(file_associations, "detect_findings", boom)
    for validate_only in (True, False):
        resp = await client.post(
            _export_url(legacy["experiment_id"], validate_only=validate_only), headers=_bearer(comp_bio_token)
        )
        assert resp.status_code == 503, resp.text
        assert resp.json()["code"] == "geo_association_check_unavailable"
        assert "detector unavailable" not in resp.text


@pytest.mark.asyncio
async def test_a_blocked_experiment_leaves_other_experiments_exportable(client, comp_bio_token, admin_token, legacy):
    blocked = await client.post(_export_url(legacy["experiment_id"]), headers=_bearer(comp_bio_token))
    assert blocked.status_code == 409

    other = await client.post(
        "/api/experiments", json={"name": "Clean", "description": "No legacy files."}, headers=_bearer(admin_token)
    )
    ok = await client.post(_export_url(other.json()["id"]), headers=_bearer(comp_bio_token))
    assert ok.status_code == 200, ok.text
    assert (await client.get("/api/experiments", headers=_bearer(comp_bio_token))).status_code == 200


@pytest.mark.asyncio
async def test_deleting_an_acknowledged_sample_or_file_is_not_blocked(client, admin_token, legacy, session):
    report = await _readiness(client, admin_token, legacy["experiment_id"])
    assert (
        await _acknowledge(client, admin_token, legacy["experiment_id"], _finding(report, "LIB10_R1.fastq.gz"))
    ).status_code == 200

    await session.execute(text("DELETE FROM samples WHERE id = :s"), {"s": legacy["samples"]["LIB1"]})
    await session.execute(text("DELETE FROM files WHERE id = :f"), {"f": legacy["files"]["both"]})
    await session.commit()
    remaining = await session.execute(select(func.count()).select_from(text("geo_file_association_acknowledgments")))
    assert remaining.scalar_one() == 0


# --- SuperSeries ------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def legacy_project(session, admin_user, legacy):
    """The legacy Experiment plus a clean one, in one project."""
    from app.models.experiment import Experiment
    from app.models.project import Project
    from app.models.sample import Sample

    project = Project(name="Legacy Project", organization_id=admin_user.organization_id)
    session.add(project)
    await session.flush()
    await session.execute(
        text("UPDATE experiments SET project_id = :p WHERE id = :e"), {"p": project.id, "e": legacy["experiment_id"]}
    )
    clean = Experiment(name="Clean", organization_id=admin_user.organization_id, project_id=project.id)
    session.add(clean)
    await session.flush()
    session.add(Sample(experiment_id=clean.id, external_id="CLEAN1", organism="Homo sapiens", qc_status="pass"))
    await session.commit()
    return {**legacy, "project_id": project.id, "clean_id": clean.id}


@pytest.mark.asyncio
async def test_superseries_refuses_an_unresolved_nested_finding_instead_of_a_partial_archive(
    client, admin_token, legacy_project, session
):
    from app.exceptions import GeoFileAssociationsUnresolvedError
    from app.services.geo.superseries_export_service import SuperSeriesExportService

    with pytest.raises(GeoFileAssociationsUnresolvedError) as refused:
        await SuperSeriesExportService.export(session, legacy_project["project_id"], legacy_project["org_id"])
    assert refused.value.details["experiment_ids"] == [legacy_project["experiment_id"]]

    resp = await client.post(f"/api/projects/{legacy_project['project_id']}/export/geo", headers=_bearer(admin_token))
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "geo_file_associations_unresolved"
    assert body["details"]["experiment_ids"] == [legacy_project["experiment_id"]]
    assert {f["experiment_id"] for f in body["details"]["findings"]} == {legacy_project["experiment_id"]}
    audited = await session.execute(text("SELECT count(*) FROM audit_log WHERE entity_type = 'geo_superseries_export'"))
    assert audited.scalar_one() == 0


@pytest.mark.asyncio
async def test_superseries_checks_every_experiment_before_building_any(session, legacy_project, monkeypatch):
    from app.exceptions import GeoFileAssociationsUnresolvedError
    from app.services.geo import geo_export_service
    from app.services.geo.superseries_export_service import SuperSeriesExportService

    built = []
    real = geo_export_service.GeoExportService.build_package

    def record(data):
        built.append(data.experiment_id)
        return real(data)

    monkeypatch.setattr(geo_export_service.GeoExportService, "build_package", staticmethod(record))
    with pytest.raises(GeoFileAssociationsUnresolvedError):
        await SuperSeriesExportService.export(session, legacy_project["project_id"], legacy_project["org_id"])
    assert built == []


@pytest.mark.asyncio
async def test_resolving_the_nested_finding_lets_the_project_download_proceed(client, admin_token, legacy_project):
    import zipfile
    from io import BytesIO

    report = await _readiness(client, admin_token, legacy_project["experiment_id"])
    ack = await _acknowledge(
        client, admin_token, legacy_project["experiment_id"], _finding(report, "LIB10_R1.fastq.gz")
    )
    assert ack.status_code == 200, ack.text

    resp = await client.post(f"/api/projects/{legacy_project['project_id']}/export/geo", headers=_bearer(admin_token))
    assert resp.status_code == 200, resp.text
    nested = [n for n in zipfile.ZipFile(BytesIO(resp.content)).namelist() if n.startswith("experiments/")]
    assert len(nested) == 2

    # include_all also needs the failed-only match resolved.
    everyone = await client.post(
        f"/api/projects/{legacy_project['project_id']}/export/geo?qc_status_filter=include_all",
        headers=_bearer(admin_token),
    )
    assert everyone.status_code == 409
    assert [f["filename"] for f in everyone.json()["details"]["findings"]] == ["LIBF_R1.fastq.gz"]


@pytest.mark.asyncio
async def test_superseries_refuses_when_a_nested_check_cannot_run(
    client, admin_token, legacy_project, session, monkeypatch
):
    """Not knowing whether an Experiment has findings must refuse the package, not drop the Experiment."""
    from app.exceptions import GeoAssociationCheckUnavailableError
    from app.services.geo import file_associations
    from app.services.geo.superseries_export_service import SuperSeriesExportService

    real = file_associations.detect_findings

    def fail_for_legacy(unlinked, selected):
        if any(s.experiment_id == legacy_project["experiment_id"] for s in selected):
            raise RuntimeError("detector unavailable")
        return real(unlinked, selected)

    monkeypatch.setattr(file_associations, "detect_findings", fail_for_legacy)
    with pytest.raises(GeoAssociationCheckUnavailableError) as refused:
        await SuperSeriesExportService.export(session, legacy_project["project_id"], legacy_project["org_id"])
    assert refused.value.details["experiment_ids"] == [legacy_project["experiment_id"]]

    resp = await client.post(f"/api/projects/{legacy_project['project_id']}/export/geo", headers=_bearer(admin_token))
    assert resp.status_code == 503, resp.text
    assert resp.json()["details"]["experiment_ids"] == [legacy_project["experiment_id"]]


@pytest.mark.asyncio
async def test_a_failed_link_lookup_refuses_like_a_failed_detector(
    client, admin_token, comp_bio_token, legacy_project, monkeypatch
):
    """Loading or selecting associations is part of the check: its failure never yields a partial export."""
    from app.services.geo import file_associations

    async def broken(*args, **kwargs):
        raise RuntimeError("link lookup unavailable")

    monkeypatch.setattr(file_associations, "load_links", broken)
    project = await client.post(
        f"/api/projects/{legacy_project['project_id']}/export/geo", headers=_bearer(admin_token)
    )
    assert project.status_code == 503, project.text
    assert set(project.json()["details"]["experiment_ids"]) == {
        legacy_project["experiment_id"],
        legacy_project["clean_id"],
    }

    for validate_only in (True, False):
        resp = await client.post(
            _export_url(legacy_project["experiment_id"], validate_only=validate_only), headers=_bearer(comp_bio_token)
        )
        assert resp.status_code == 503, resp.text
        assert "link lookup unavailable" not in resp.text
