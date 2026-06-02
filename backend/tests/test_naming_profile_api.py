"""HTTP-level tests for the redesigned Naming Profile API.

Covers the new request/response shape (no `*_mappings`, optional
`experiment_template_id`) and the new `POST /test` endpoint which accepts
an unsaved profile draft.
"""

import pytest


def _seg_number(identifier="SMP", field_name="SampleID", position=0, padding=2):
    return {
        "position": position,
        "identifier": identifier,
        "field_name": field_name,
        "field_type": "number",
        "padding": padding,
        "date_format": None,
        "is_system_chip": False,
    }


def _seg_date(field_name="RunDate", date_format="YYYYMMDD", position=0):
    return {
        "position": position,
        "identifier": None,
        "field_name": field_name,
        "field_type": "date",
        "padding": None,
        "date_format": date_format,
        "is_system_chip": False,
    }


def _seg_string(identifier="req", field_name="Requestor", position=0):
    return {
        "position": position,
        "identifier": identifier,
        "field_name": field_name,
        "field_type": "string",
        "padding": None,
        "date_format": None,
        "is_system_chip": False,
    }


@pytest.mark.asyncio
async def test_post_naming_profile_with_no_template_id_succeeds(client, admin_token):
    resp = await client.post(
        "/api/naming-profiles",
        json={
            "name": "No template profile",
            "segments": [_seg_number()],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "No template profile"
    assert body["status"] == "active"
    assert body["experiment_template_id"] is None
    assert len(body["segments"]) == 1
    assert body["segments"][0]["identifier"] == "SMP"


@pytest.mark.asyncio
async def test_post_naming_profile_with_invalid_identifier_returns_422(
    client, admin_token
):
    resp = await client.post(
        "/api/naming-profiles",
        json={
            "name": "Bad identifier",
            "segments": [_seg_number(identifier="TOOLONG")],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_naming_profile_with_duplicate_identifier_returns_422(
    client, admin_token
):
    resp = await client.post(
        "/api/naming-profiles",
        json={
            "name": "Duplicate identifiers",
            "segments": [
                _seg_number(identifier="SMP", field_name="SampleID", position=0),
                _seg_number(identifier="SMP", field_name="Other", position=1),
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_naming_profile_empty_segments_returns_422(client, admin_token):
    resp = await client.post(
        "/api/naming-profiles",
        json={"name": "Empty", "segments": []},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_list_update_delete_roundtrip(client, admin_token):
    create = await client.post(
        "/api/naming-profiles",
        json={
            "name": "Roundtrip",
            "segments": [_seg_number()],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create.status_code == 200
    profile_id = create.json()["id"]

    listed = await client.get(
        "/api/naming-profiles",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert listed.status_code == 200
    assert any(p["id"] == profile_id for p in listed.json())

    fetched = await client.get(
        f"/api/naming-profiles/{profile_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Roundtrip"

    updated = await client.put(
        f"/api/naming-profiles/{profile_id}",
        json={"name": "Renamed"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"

    deactivated = await client.delete(
        f"/api/naming-profiles/{profile_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_test_endpoint_accepts_unsaved_profile_payload(client, admin_token):
    """POST /test takes an unsaved profile draft and a list of filenames."""
    resp = await client.post(
        "/api/naming-profiles/test",
        json={
            "filenames": ["SMP0042_20260602.fastq.gz", "garbage.txt"],
            "delimiter": "_",
            "strip_extension": True,
            "segments": [
                _seg_number(identifier="SMP", field_name="SampleID", position=0),
                _seg_date(field_name="RunDate", date_format="YYYYMMDD", position=1),
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    results = resp.json()
    assert len(results) == 2

    first, second = results
    assert first["filename"] == "SMP0042_20260602.fastq.gz"
    assert first["parsed"] == {"SampleID": "0042", "RunDate": "2026-06-02"}
    assert first["unrecognized"] == []

    assert second["filename"] == "garbage.txt"
    assert second["parsed"] == {}
    assert "garbage" in second["unrecognized"]


@pytest.mark.asyncio
async def test_test_endpoint_warns_on_yyyy_mm_dd_with_hyphen_delimiter(
    client, admin_token
):
    resp = await client.post(
        "/api/naming-profiles/test",
        json={
            "filenames": ["SMP0042-2026-06-02-2027-07-03.txt"],
            "delimiter": "-",
            "strip_extension": True,
            "segments": [
                _seg_number(identifier="SMP", field_name="SampleID", position=0, padding=2),
                _seg_date(
                    field_name="RunDate",
                    date_format="YYYY-MM-DD",
                    position=1,
                ),
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()[0]
    assert result["parsed"]["SampleID"] == "0042"
    assert result["parsed"]["RunDate"] == "2026-06-02"
    assert any("ambiguous" in w.lower() for w in result["warnings"])


@pytest.mark.asyncio
async def test_viewer_denied_access(client, viewer_token):
    resp = await client.get(
        "/api/naming-profiles",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403
