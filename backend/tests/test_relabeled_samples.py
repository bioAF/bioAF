"""Samples migration 080 relabelled stay findable by the label people knew (plan 9, owner policy 2026-11-07).

Before 080 one experiment could hold the same source label twice. 080 gave every such sample a new
external ID (``<label>~<sample id>``) and recorded it in sample_external_id_transitions. These tests set
up exactly what 080 leaves behind and prove how people and integrations then see those samples: the
original label is shown beside the new ID, a lookup by the original label names the candidates instead
of picking one, and nothing quietly creates another sample under the ambiguous label.
"""

import csv
import io

import pytest
from sqlalchemy import select

from app.models.experiment import Experiment
from app.models.sample import Sample
from app.models.sample_external_id_transition import SampleExternalIdTransition


async def _relabelled_experiment(session, org_id: int, owner_id: int) -> dict:
    exp = Experiment(organization_id=org_id, name="Legacy duplicates", owner_user_id=owner_id, status="registered")
    session.add(exp)
    await session.flush()
    a = Sample(experiment_id=exp.id, sample_id_external="DUP", status="registered")
    b = Sample(experiment_id=exp.id, sample_id_external="DUP", status="registered")
    plain = Sample(experiment_id=exp.id, sample_id_external="PLAIN", external_id="PLAIN", status="registered")
    session.add_all([a, b, plain])
    await session.flush()
    for s in (a, b):
        s.external_id = f"DUP~{s.id}"
        session.add(
            SampleExternalIdTransition(
                sample_id=s.id, experiment_id=exp.id, source_label="DUP", external_id=s.external_id
            )
        )
    await session.commit()
    return {
        "experiment": exp.id,
        "a": a.id,
        "b": b.id,
        "plain": plain.id,
        "ids": sorted([a.external_id, b.external_id]),
    }


@pytest.mark.asyncio
async def test_integration_detail_and_list_show_the_label_a_sample_was_relabelled_from(
    client, session, admin_user, integration_api_key
):
    data = await _relabelled_experiment(session, admin_user.organization_id, admin_user.id)
    headers = integration_api_key["headers"]

    one = await client.get(f"/api/v1/integrations/samples/{data['a']}", headers=headers)
    assert one.status_code == 200
    assert one.json()["external_id"] == f"DUP~{data['a']}"
    assert one.json()["relabeled_from"] == "DUP"

    plain = await client.get(f"/api/v1/integrations/samples/{data['plain']}", headers=headers)
    assert plain.json()["relabeled_from"] is None

    # Filtering by the old label finds every sample that carried it.
    listed = await client.get(
        "/api/v1/integrations/samples",
        headers=headers,
        params={"external_id": "DUP", "experiment_id": data["experiment"]},
    )
    assert listed.status_code == 200
    assert sorted(s["external_id"] for s in listed.json()["items"]) == data["ids"]


@pytest.mark.asyncio
async def test_lookup_by_an_ambiguous_old_label_names_the_candidates_instead_of_picking_one(
    client, session, admin_user, integration_api_key
):
    data = await _relabelled_experiment(session, admin_user.organization_id, admin_user.id)
    headers = integration_api_key["headers"]

    r = await client.get(
        "/api/v1/integrations/samples/by-external/DUP", headers=headers, params={"experiment_id": data["experiment"]}
    )
    assert r.status_code == 409
    assert r.json()["detail"] == {"error": "ambiguous_external_id", "label": "DUP", "candidates": data["ids"]}

    # The new IDs resolve exactly; an unrelated label is still simply not found.
    exact = await client.get(
        f"/api/v1/integrations/samples/by-external/DUP~{data['b']}",
        headers=headers,
        params={"experiment_id": data["experiment"]},
    )
    assert exact.status_code == 200 and exact.json()["id"] == data["b"]
    missing = await client.get(
        "/api/v1/integrations/samples/by-external/NOPE", headers=headers, params={"experiment_id": data["experiment"]}
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_creating_a_sample_under_an_ambiguous_old_label_is_refused(
    client, session, admin_user, integration_api_key
):
    data = await _relabelled_experiment(session, admin_user.organization_id, admin_user.id)
    r = await client.post(
        "/api/v1/integrations/samples",
        headers=integration_api_key["headers"],
        json={"experiment_id": data["experiment"], "external_id": "DUP", "organism": "Mouse"},
    )
    assert r.status_code == 409
    assert r.json()["detail"] == {"error": "ambiguous_external_id", "label": "DUP", "candidates": data["ids"]}
    count = len((await session.execute(select(Sample.id).where(Sample.experiment_id == data["experiment"]))).all())
    assert count == 3


@pytest.mark.asyncio
async def test_ingest_does_not_auto_create_a_sample_under_an_ambiguous_old_label(session, admin_user):
    from app.services.ingest_service import resolve_or_create_sample

    data = await _relabelled_experiment(session, admin_user.organization_id, admin_user.id)
    resolved = await resolve_or_create_sample(
        "DUP", data["experiment"], admin_user.organization_id, admin_user.id, session
    )
    assert resolved is None
    exact = await resolve_or_create_sample(
        f"DUP~{data['a']}", data["experiment"], admin_user.organization_id, admin_user.id, session
    )
    assert exact == data["a"]
    count = len((await session.execute(select(Sample.id).where(Sample.experiment_id == data["experiment"]))).all())
    assert count == 3


@pytest.mark.asyncio
async def test_the_app_sample_views_show_the_old_label(client, session, admin_user, admin_token):
    data = await _relabelled_experiment(session, admin_user.organization_id, admin_user.id)
    headers = {"Authorization": f"Bearer {admin_token}"}

    detail = await client.get(f"/api/samples/{data['a']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["relabeled_from"] == "DUP"

    listed = await client.get(f"/api/experiments/{data['experiment']}/samples", headers=headers)
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    rows = rows.get("samples", rows) if isinstance(rows, dict) else rows
    by_id = {s["id"]: s for s in rows}
    assert by_id[data["a"]]["relabeled_from"] == "DUP" and by_id[data["plain"]]["relabeled_from"] is None


@pytest.mark.asyncio
async def test_the_experiment_export_manifest_carries_the_old_label(session, admin_user):
    from app.services.export_service import _build_sample_manifest

    data = await _relabelled_experiment(session, admin_user.organization_id, admin_user.id)
    samples = (
        (await session.execute(select(Sample).where(Sample.experiment_id == data["experiment"]).order_by(Sample.id)))
        .scalars()
        .all()
    )
    rows = list(csv.DictReader(io.StringIO(_build_sample_manifest(samples))))
    assert [(r["external_id"], r["relabeled_from"]) for r in rows] == [
        (f"DUP~{data['a']}", "DUP"),
        (f"DUP~{data['b']}", "DUP"),
        ("PLAIN", ""),
    ]


@pytest.mark.asyncio
async def test_a_lookup_into_another_organizations_experiment_reveals_nothing(
    client, session, admin_user, integration_api_key
):
    """The ambiguity check must not answer for an experiment outside the caller's organization."""
    from app.models.organization import Organization

    other = Organization(name="Other lab", setup_complete=True)
    session.add(other)
    await session.flush()
    data = await _relabelled_experiment(session, other.id, admin_user.id)

    r = await client.get(
        "/api/v1/integrations/samples/by-external/DUP",
        headers=integration_api_key["headers"],
        params={"experiment_id": data["experiment"]},
    )
    assert r.status_code == 404
    assert "DUP~" not in r.text


@pytest.mark.asyncio
async def test_lookup_reports_the_ambiguity_even_when_a_sample_carries_the_old_label(
    client, session, admin_user, integration_api_key
):
    """Should a sample carry the split label anyway, the lookup still refuses to pick one."""
    data = await _relabelled_experiment(session, admin_user.organization_id, admin_user.id)
    session.add(Sample(experiment_id=data["experiment"], external_id="DUP", status="registered"))
    await session.commit()

    r = await client.get(
        "/api/v1/integrations/samples/by-external/DUP",
        headers=integration_api_key["headers"],
        params={"experiment_id": data["experiment"]},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["candidates"] == data["ids"]


async def _sample_count(session, experiment_id: int) -> int:
    return len((await session.execute(select(Sample.id).where(Sample.experiment_id == experiment_id))).all())


@pytest.mark.asyncio
async def test_the_app_create_and_bulk_create_refuse_an_ambiguous_old_label(client, session, admin_user, admin_token):
    data = await _relabelled_experiment(session, admin_user.organization_id, admin_user.id)
    headers = {"Authorization": f"Bearer {admin_token}"}
    expected = {"error": "ambiguous_external_id", "label": "DUP", "candidates": data["ids"]}

    one = await client.post(
        f"/api/experiments/{data['experiment']}/samples", headers=headers, json={"external_id": "DUP"}
    )
    assert one.status_code == 409, one.text
    assert one.json()["detail"] == expected

    bulk = await client.post(
        f"/api/experiments/{data['experiment']}/samples/bulk",
        headers=headers,
        json={"samples": [{"external_id": "NEW-1"}, {"external_id": "DUP"}]},
    )
    assert bulk.status_code == 409, bulk.text
    assert bulk.json()["detail"] == expected
    assert await _sample_count(session, data["experiment"]) == 3


@pytest.mark.asyncio
async def test_renaming_a_sample_to_an_ambiguous_old_label_is_refused(client, session, admin_user, admin_token):
    data = await _relabelled_experiment(session, admin_user.organization_id, admin_user.id)
    headers = {"Authorization": f"Bearer {admin_token}"}

    r = await client.patch(f"/api/samples/{data['plain']}", headers=headers, json={"external_id": "DUP"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["candidates"] == data["ids"]
    # Every other edit to a relabelled sample still works.
    ok = await client.patch(f"/api/samples/{data['a']}", headers=headers, json={"organism": "Mouse"})
    assert ok.status_code == 200, ok.text
    await session.refresh(await session.get(Sample, data["plain"]))
    assert (await session.get(Sample, data["plain"])).external_id == "PLAIN"


@pytest.mark.asyncio
async def test_claiming_an_unclaimed_sample_under_an_ambiguous_old_label_is_refused(
    client, session, admin_user, admin_token
):
    data = await _relabelled_experiment(session, admin_user.organization_id, admin_user.id)
    unclaimed = Sample(experiment_id=data["experiment"], external_id="raw-1", status="registered", is_unclaimed=True)
    session.add(unclaimed)
    await session.commit()

    r = await client.post(
        f"/api/samples/{unclaimed.id}/claim", headers={"Authorization": f"Bearer {admin_token}"}, json={"name": "DUP"}
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["candidates"] == data["ids"]


@pytest.mark.asyncio
async def test_the_candidate_lookup_is_scoped_to_the_organization(session, admin_user):
    from app.models.organization import Organization
    from app.services.sample_service import SampleService

    other = Organization(name="Other lab", setup_complete=True)
    session.add(other)
    await session.flush()
    data = await _relabelled_experiment(session, other.id, admin_user.id)

    assert await SampleService.ambiguous_label_candidates(session, other.id, data["experiment"], "DUP") == data["ids"]
    assert (
        await SampleService.ambiguous_label_candidates(session, admin_user.organization_id, data["experiment"], "DUP")
        == []
    )
