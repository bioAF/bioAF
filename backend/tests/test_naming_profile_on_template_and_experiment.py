"""Naming profile assignment + inheritance on templates and experiments.

The experiment template can carry a default naming_profile_id. Experiments
created from that template inherit it; experiments may override per-row.
The API surfaces three keys on ExperimentResponse:

- naming_profile_id: the experiment's own override (may be None)
- template_naming_profile_id: the template's default (may be None)
- effective_naming_profile_id: experiment override if set, else template
  default (may be None if neither is set)

See ADR-058 and local/Naming Profiles/redesign-plan.md.
"""

import pytest


def _segment_payload(**overrides) -> dict:
    """Minimal valid SegmentDefinition body, kept inline so we don't import
    from the backend module under test."""
    default = {
        "position": 0,
        "identifier": "SMP",
        "field_name": "SampleID",
        "field_type": "number",
        "padding": 2,
        "date_format": None,
        "is_system_chip": False,
    }
    default.update(overrides)
    return default


async def _create_profile(client, admin_token, name: str = "TestProfile") -> int:
    resp = await client.post(
        "/api/naming-profiles",
        json={"name": name, "segments": [_segment_payload()]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_template_create_carries_naming_profile_id(client, admin_token):
    profile_id = await _create_profile(client, admin_token, "Tmpl Profile")
    resp = await client.post(
        "/api/templates",
        json={"name": "Tmpl A", "naming_profile_id": profile_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["naming_profile_id"] == profile_id


@pytest.mark.asyncio
async def test_template_update_sets_naming_profile_id(client, admin_token):
    profile_id = await _create_profile(client, admin_token, "Tmpl Update Profile")
    create = await client.post(
        "/api/templates",
        json={"name": "Tmpl B"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    template_id = create.json()["id"]

    update = await client.patch(
        f"/api/templates/{template_id}",
        json={"naming_profile_id": profile_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert update.status_code == 200
    assert update.json()["naming_profile_id"] == profile_id


@pytest.mark.asyncio
async def test_template_naming_profile_id_is_optional(client, admin_token):
    resp = await client.post(
        "/api/templates",
        json={"name": "No profile here"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["naming_profile_id"] is None


# ---------------------------------------------------------------------------
# Experiment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_experiment_create_with_explicit_naming_profile(client, admin_token):
    profile_id = await _create_profile(client, admin_token, "Exp Direct Profile")
    resp = await client.post(
        "/api/experiments",
        json={"name": "Exp A", "naming_profile_id": profile_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["naming_profile_id"] == profile_id
    assert body["template_naming_profile_id"] is None
    assert body["effective_naming_profile_id"] == profile_id


@pytest.mark.asyncio
async def test_experiment_inherits_template_naming_profile(client, admin_token):
    profile_id = await _create_profile(client, admin_token, "Inherit Profile")
    tmpl_resp = await client.post(
        "/api/templates",
        json={"name": "Tmpl with profile", "naming_profile_id": profile_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    template_id = tmpl_resp.json()["id"]

    exp_resp = await client.post(
        "/api/experiments",
        json={"name": "Inheriting Exp", "template_id": template_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert exp_resp.status_code == 200, exp_resp.text
    body = exp_resp.json()
    assert body["naming_profile_id"] is None
    assert body["template_naming_profile_id"] == profile_id
    assert body["effective_naming_profile_id"] == profile_id


@pytest.mark.asyncio
async def test_experiment_override_wins_over_template_default(client, admin_token):
    tmpl_profile_id = await _create_profile(client, admin_token, "Tmpl Default")
    exp_profile_id = await _create_profile(client, admin_token, "Exp Override")
    tmpl_resp = await client.post(
        "/api/templates",
        json={"name": "Tmpl C", "naming_profile_id": tmpl_profile_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    template_id = tmpl_resp.json()["id"]

    exp_resp = await client.post(
        "/api/experiments",
        json={
            "name": "Overriding Exp",
            "template_id": template_id,
            "naming_profile_id": exp_profile_id,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert exp_resp.status_code == 200
    body = exp_resp.json()
    assert body["naming_profile_id"] == exp_profile_id
    assert body["template_naming_profile_id"] == tmpl_profile_id
    assert body["effective_naming_profile_id"] == exp_profile_id


@pytest.mark.asyncio
async def test_experiment_update_can_set_naming_profile_id(client, admin_token):
    profile_id = await _create_profile(client, admin_token, "Updated Profile")
    exp_resp = await client.post(
        "/api/experiments",
        json={"name": "Exp to Update"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp_resp.json()["id"]

    update = await client.patch(
        f"/api/experiments/{exp_id}",
        json={"naming_profile_id": profile_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert update.status_code == 200
    assert update.json()["naming_profile_id"] == profile_id
    assert update.json()["effective_naming_profile_id"] == profile_id


@pytest.mark.asyncio
async def test_experiment_with_neither_override_nor_template_default(client, admin_token):
    exp_resp = await client.post(
        "/api/experiments",
        json={"name": "Bare Exp"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert exp_resp.status_code == 200
    body = exp_resp.json()
    assert body["naming_profile_id"] is None
    assert body["template_naming_profile_id"] is None
    assert body["effective_naming_profile_id"] is None
