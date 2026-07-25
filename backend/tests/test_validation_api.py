"""HTTP API for the literature-validation flow (lit_validation).

Exercises the spine through HTTP: request -> read (drive to plan_ready) -> approve, plus RBAC
(a viewer cannot request). The LLM is faked at the extraction service so the read step is
deterministic.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.services import validation_extraction_service as ext

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _enable_lit_validation(session):
    # lit_validation is gated behind its beta flag (spec-07); these HTTP tests exercise the feature, so
    # turn it on (the flag defaults off, which would otherwise 404 every endpoint).
    from app.services import beta_features_service

    await beta_features_service.set_flag(session, "lit_validation", True)
    await session.commit()


_GOOD = (
    '```json\n{"accessions": ["GSE52778"], "sample_structure": {"organism": "Homo sapiens"}, '
    '"method": {"assay": "bulk RNA-seq", "tools": ["TopHat"], "reference_build": "GRCh37"}, '
    '"differential_design": {"contrasts": [{"name": "dex vs untreated", "test_condition": "dex", '
    '"reference_condition": "untreated", "test_samples": ["GSM1"], "reference_samples": ["GSM2"]}], '
    '"thresholds": {"log2fc": 1.0, "padj": 0.05}}, '
    '"claims": [{"metric_key": "alignment_rate", "value": 83.4, "unit": "%", "source_locator": "Results"}], '
    '"data_availability": "deposited", "blockers": []}\n```'
)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _patch_llm(monkeypatch, response):
    async def fake_get_active(sess, org_id):
        return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

    class _C:
        async def submit(self, prompt, payload, model, api_key, attachments=None):
            return response

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", fake_get_active)
    monkeypatch.setattr(ext, "get_client", lambda p: _C())


async def test_request_read_approve_flow(client, admin_token, monkeypatch):
    _patch_llm(monkeypatch, _GOOD)

    r = await client.post("/api/validation-studies", json={"source_accession": "GSE52778"}, headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    study = r.json()
    assert study["state"] == "requested"
    sid = study["id"]

    r = await client.post(
        f"/api/validation-studies/{sid}/read", json={"full_text": "the paper body"}, headers=_auth(admin_token)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "plan_ready"
    assert body["plan"]["pipeline_key"] == "nf-core/rnaseq"
    assert body["plan"]["comparison_targets"][0]["metric_key"] == "alignment_rate"
    # B2e: the differential design is captured and surfaced for the human to ratify at the C1 gate.
    design = body["plan"]["differential_design"]
    assert design["thresholds"] == {"log2fc": 1.0, "padj": 0.05}
    assert design["contrasts"][0]["test_samples"] == ["GSM1"]

    r = await client.post(f"/api/validation-studies/{sid}/approve", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "acquiring_data"

    r = await client.get(f"/api/validation-studies/{sid}", headers=_auth(admin_token))
    assert r.status_code == 200
    assert r.json()["approved_by_user_id"] is not None


async def test_confirm_finding_set_at_c1_gate(client, admin_token, monkeypatch):
    """B4: the human confirms the paper's deposited DEG table at the C1 gate; the endpoint normalizes
    it and surfaces the parsed finding set on the plan so approval can run Level-3 concordance."""
    _patch_llm(monkeypatch, _GOOD)
    sid = (
        await client.post("/api/validation-studies", json={"source_accession": "GSE52778"}, headers=_auth(admin_token))
    ).json()["id"]
    await client.post(
        f"/api/validation-studies/{sid}/read", json={"full_text": "the paper body"}, headers=_auth(admin_token)
    )

    table = "gene,log2FoldChange,padj\nA1BG,2.5,0.001\nTP53,-1.8,0.01\nGAPDH,0.1,0.9\n"
    r = await client.post(
        f"/api/validation-studies/{sid}/finding-set",
        json={"kind": "gene", "table_text": table, "source_locator": "Table S3"},
        headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    claim = r.json()["plan"]["finding_claim"]
    assert claim["confirmed"] is True
    assert claim["finding_set"]["n_sig"] == 2
    assert {e["id"] for e in claim["finding_set"]["entities"]} == {"A1BG", "TP53"}


async def test_edit_differential_design_at_c1_gate(client, admin_token, monkeypatch):
    """B2e: the human corrects the contrast's sample labels at the C1 gate; the edited design is
    normalized and surfaced back on the plan."""
    _patch_llm(monkeypatch, _GOOD)
    sid = (await client.post("/api/validation-studies", json={}, headers=_auth(admin_token))).json()["id"]
    await client.post(f"/api/validation-studies/{sid}/read", json={"full_text": "x"}, headers=_auth(admin_token))

    r = await client.put(
        f"/api/validation-studies/{sid}/differential-design",
        json={
            "contrasts": [
                {"name": "dex vs untreated", "test_samples": ["SRX30659361"], "reference_samples": ["SRX30659368"]}
            ],
            "thresholds": {"log2fc": 1.5, "padj": 0.01},
        },
        headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    design = r.json()["plan"]["differential_design"]
    assert design["thresholds"] == {"log2fc": 1.5, "padj": 0.01}
    assert design["contrasts"][0]["test_samples"] == ["SRX30659361"]


async def test_viewer_cannot_confirm_finding_set(client, admin_token, viewer_token, monkeypatch):
    _patch_llm(monkeypatch, _GOOD)
    sid = (await client.post("/api/validation-studies", json={}, headers=_auth(admin_token))).json()["id"]
    await client.post(f"/api/validation-studies/{sid}/read", json={"full_text": "x"}, headers=_auth(admin_token))
    r = await client.post(
        f"/api/validation-studies/{sid}/finding-set",
        json={"kind": "gene", "table_text": "gene,log2FoldChange,padj\nA1BG,2.5,0.001\n"},
        headers=_auth(viewer_token),
    )
    assert r.status_code == 403


async def test_decline_flow(client, admin_token, monkeypatch):
    _patch_llm(monkeypatch, _GOOD)
    sid = (await client.post("/api/validation-studies", json={}, headers=_auth(admin_token))).json()["id"]
    await client.post(f"/api/validation-studies/{sid}/read", json={"full_text": "x"}, headers=_auth(admin_token))
    r = await client.post(
        f"/api/validation-studies/{sid}/decline", json={"reason": "wrong accession"}, headers=_auth(admin_token)
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "plan_declined"
    assert r.json()["failure_reason"] == "wrong accession"


async def test_viewer_cannot_request(client, viewer_token):
    r = await client.post("/api/validation-studies", json={}, headers=_auth(viewer_token))
    assert r.status_code == 403


async def test_classify_by_hand_via_api_after_comparing(client, admin_token, admin_user, session):
    """The back half is live-only, so drive a study to 'comparing' with an evidence bundle directly,
    then exercise the HTTP surface: GET shows computed-vs-claimed, POST /classify records the verdict."""
    from app.services.validation_study_service import ValidationStudyService

    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="GSE1"
    )
    for nxt in [
        "acquiring_text",
        "reading",
        "plan_ready",
        "acquiring_data",
        "setup",
        "running",
        "extracting",
        "comparing",
    ]:
        study = await ValidationStudyService.transition(
            session, study.id, admin_user.organization_id, admin_user.id, nxt
        )
    study.evidence_json = {
        "computed_metrics": {"cell_count": 5000},
        "comparison_targets": [{"metric_key": "cell_count", "claimed_value": 10000}],
    }
    await session.commit()

    r = await client.get(f"/api/validation-studies/{study.id}", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "comparing"
    assert body["evidence"]["computed_metrics"]["cell_count"] == 5000
    assert body["evidence"]["comparison_targets"][0]["claimed_value"] == 10000

    r = await client.post(
        f"/api/validation-studies/{study.id}/classify",
        json={"classification": "not_validated"},
        headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "classified"
    assert r.json()["classification"] == "not_validated"
    assert r.json()["confidence"] == 0.0  # not_validated -> Very Unlikely (interim mapping)


async def test_viewer_cannot_classify(client, viewer_token):
    r = await client.post(
        "/api/validation-studies/1/classify", json={"classification": "validated"}, headers=_auth(viewer_token)
    )
    assert r.status_code == 403


async def test_list_studies_returns_org_studies_newest_first(client, admin_token):
    a = (
        await client.post("/api/validation-studies", json={"source_accession": "GSE_A"}, headers=_auth(admin_token))
    ).json()["id"]
    b = (
        await client.post("/api/validation-studies", json={"source_accession": "GSE_B"}, headers=_auth(admin_token))
    ).json()["id"]

    r = await client.get("/api/validation-studies", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    items = r.json()
    ids = [s["id"] for s in items]
    assert a in ids and b in ids
    # newest first (b created after a)
    assert ids.index(b) < ids.index(a)
    # the summary carries the fields the list UI renders
    first = next(s for s in items if s["id"] == b)
    assert first["state"] == "requested"
    assert first["source_accession"] == "GSE_B"
    assert "confidence" in first


async def test_viewer_can_list_studies(client, admin_token, viewer_token):
    await client.post("/api/validation-studies", json={"source_accession": "GSE_V"}, headers=_auth(admin_token))
    r = await client.get("/api/validation-studies", headers=_auth(viewer_token))
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


async def test_missing_data_early_exit_via_api(client, admin_token, monkeypatch):
    no_data = (
        '```json\n{"accessions": [], "method": {"assay": "bulk RNA-seq"}, "claims": [], '
        '"data_availability": "none", "blockers": []}\n```'
    )
    _patch_llm(monkeypatch, no_data)
    sid = (await client.post("/api/validation-studies", json={}, headers=_auth(admin_token))).json()["id"]
    r = await client.post(f"/api/validation-studies/{sid}/read", json={"full_text": "x"}, headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "classified"
    assert r.json()["classification"] == "missing_data"
    assert r.json()["confidence"] is None  # missing_data -> Could Not Reproduce (no confidence)
