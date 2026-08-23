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
                {
                    "name": "dex vs untreated",
                    "test_samples": ["SRX30659361", "SRX30659362"],
                    "reference_samples": ["SRX30659368", "SRX30659369"],
                }
            ],
            "thresholds": {"log2fc": 1.5, "padj": 0.01},
        },
        headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    design = r.json()["plan"]["differential_design"]
    assert design["thresholds"] == {"log2fc": 1.5, "padj": 0.01}
    assert design["contrasts"][0]["test_samples"] == ["SRX30659361", "SRX30659362"]


async def test_finding_set_candidates_returns_autofetched(client, admin_token, monkeypatch):
    """B4 auto-fetch assist: the C1 gate can pull best-effort GEO candidates to pre-fill the confirm.
    The fetch is stubbed (no network); the endpoint just surfaces what the service returns."""
    _patch_llm(monkeypatch, _GOOD)
    sid = (
        await client.post("/api/validation-studies", json={"source_accession": "GSE52778"}, headers=_auth(admin_token))
    ).json()["id"]
    await client.post(f"/api/validation-studies/{sid}/read", json={"full_text": "x"}, headers=_auth(admin_token))

    from app.services.literature.ground_truth_fetch_service import GroundTruthFetchService

    async def _fake(accession, *, kind="gene", fetcher=None):
        return [{"source": "geo_supplementary", "filename": f"{accession}_DEG.csv", "n_sig": 5, "finding_set": {}}]

    monkeypatch.setattr(GroundTruthFetchService, "fetch_geo_candidates", _fake)

    r = await client.get(f"/api/validation-studies/{sid}/finding-set/candidates", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    cands = r.json()["candidates"]
    assert len(cands) == 1
    assert cands[0]["filename"] == "GSE52778_DEG.csv"


async def test_sample_manifest_returns_entries(client, admin_token, monkeypatch):
    """The Level-3 picker fetches the study's real sample manifest (title + condition + accessions).
    The manifest fetch is stubbed (no network); the endpoint unions the plan's accessions."""
    _patch_llm(monkeypatch, _GOOD)
    sid = (
        await client.post("/api/validation-studies", json={"source_accession": "GSE52778"}, headers=_auth(admin_token))
    ).json()["id"]
    await client.post(f"/api/validation-studies/{sid}/read", json={"full_text": "x"}, headers=_auth(admin_token))

    from app.services.literature.accession_manifest_service import AccessionManifestService, ManifestResult

    async def _fake(accession, *, fetcher=None):
        return ManifestResult(
            samples=[
                {
                    "experiment_accession": "SRX1",
                    "run_accession": "SRR1",
                    "sample_accession": "SRS1",
                    "title": "Dex-treated rep 1",
                    "condition": "treatment: dex",
                }
            ]
        )

    monkeypatch.setattr(AccessionManifestService, "fetch_manifest", _fake)

    r = await client.get(f"/api/validation-studies/{sid}/sample-manifest", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unavailable_reason"] is None
    assert len(body["samples"]) == 1
    assert body["samples"][0]["experiment_accession"] == "SRX1"
    assert body["samples"][0]["title"] == "Dex-treated rep 1"
    assert body["samples"][0]["condition"] == "treatment: dex"


async def test_sample_manifest_unions_and_dedupes_across_accessions(client, admin_token, monkeypatch):
    """A multi-accession study unions its manifests and de-dupes an experiment seen in two accessions."""
    two_acc = (
        '```json\n{"accessions": ["GSE_A", "GSE_B"], "method": {"assay": "bulk RNA-seq"}, '
        '"claims": [], "data_availability": "deposited", "blockers": []}\n```'
    )
    _patch_llm(monkeypatch, two_acc)
    sid = (await client.post("/api/validation-studies", json={}, headers=_auth(admin_token))).json()["id"]
    await client.post(f"/api/validation-studies/{sid}/read", json={"full_text": "x"}, headers=_auth(admin_token))

    from app.services.literature.accession_manifest_service import AccessionManifestService, ManifestResult

    async def _fake(accession, *, fetcher=None):
        shared = {
            "experiment_accession": "SRX_SHARED",
            "run_accession": "",
            "sample_accession": "",
            "title": "Shared",
            "condition": "",
        }
        unique = {
            "experiment_accession": f"SRX_{accession}",
            "run_accession": "",
            "sample_accession": "",
            "title": accession,
            "condition": "",
        }
        return ManifestResult(samples=[shared, unique])

    monkeypatch.setattr(AccessionManifestService, "fetch_manifest", _fake)

    r = await client.get(f"/api/validation-studies/{sid}/sample-manifest", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    exps = [s["experiment_accession"] for s in r.json()["samples"]]
    assert exps == ["SRX_SHARED", "SRX_GSE_A", "SRX_GSE_B"]  # shared collapsed to one


async def test_sample_manifest_no_accession_is_unavailable(client, admin_token, monkeypatch):
    """A study with no deposited accession returns 200 with an unavailable reason, not a 500, so the
    picker falls back to free-text entry."""
    no_data = (
        '```json\n{"accessions": [], "method": {"assay": "bulk RNA-seq"}, "claims": [], '
        '"data_availability": "deposited", "blockers": []}\n```'
    )
    _patch_llm(monkeypatch, no_data)
    sid = (await client.post("/api/validation-studies", json={}, headers=_auth(admin_token))).json()["id"]
    await client.post(f"/api/validation-studies/{sid}/read", json={"full_text": "x"}, headers=_auth(admin_token))

    r = await client.get(f"/api/validation-studies/{sid}/sample-manifest", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["samples"] == []
    assert body["unavailable_reason"]


async def test_sample_manifest_fetch_failure_is_unavailable(client, admin_token, monkeypatch):
    """A fetch failure surfaces as a 200 unavailable signal (the reason), never a 500."""
    _patch_llm(monkeypatch, _GOOD)
    sid = (
        await client.post("/api/validation-studies", json={"source_accession": "GSE52778"}, headers=_auth(admin_token))
    ).json()["id"]
    await client.post(f"/api/validation-studies/{sid}/read", json={"full_text": "x"}, headers=_auth(admin_token))

    from app.services.literature.accession_manifest_service import AccessionManifestService, ManifestResult

    async def _fake(accession, *, fetcher=None):
        return ManifestResult(samples=[], unavailable_reason="Could not reach ENA to list this study's samples.")

    monkeypatch.setattr(AccessionManifestService, "fetch_manifest", _fake)

    r = await client.get(f"/api/validation-studies/{sid}/sample-manifest", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["samples"] == []
    assert "ENA" in body["unavailable_reason"]


async def test_sample_manifest_requires_approver(client, admin_token, viewer_token, monkeypatch):
    """The picker is an approve-time action, so viewing the manifest requires the approve permission."""
    _patch_llm(monkeypatch, _GOOD)
    sid = (
        await client.post("/api/validation-studies", json={"source_accession": "GSE52778"}, headers=_auth(admin_token))
    ).json()["id"]
    r = await client.get(f"/api/validation-studies/{sid}/sample-manifest", headers=_auth(viewer_token))
    assert r.status_code == 403


async def test_sample_manifest_missing_study_is_404(client, admin_token):
    """Org-scoped load: a study id that is not this org's (here, nonexistent) 404s before any fetch."""
    r = await client.get("/api/validation-studies/999999/sample-manifest", headers=_auth(admin_token))
    assert r.status_code == 404


async def _study_in_state(session, user, state):
    from app.services.validation_study_service import ValidationStudyService

    study = await ValidationStudyService.create_study(session, user.organization_id, user.id, source_accession="GSE1")
    study.state = state
    await session.commit()
    return study


async def test_override_samples_advances_a_held_study_to_setup(client, admin_token, admin_user, session):
    """The 'run with the samples we have' action on a held (samples_mismatch) study advances it to
    setup and stamps who overrode."""
    study = await _study_in_state(session, admin_user, "samples_mismatch")

    r = await client.post(f"/api/validation-studies/{study.id}/override-samples", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "setup"

    await session.refresh(study)
    assert (study.evidence_json or {}).get("samples_override", {}).get("user_id") == admin_user.id


async def test_override_samples_rejects_a_study_not_held(client, admin_token, admin_user, session):
    """Override is only valid from samples_mismatch; any other state is a 400."""
    study = await _study_in_state(session, admin_user, "plan_ready")
    r = await client.post(f"/api/validation-studies/{study.id}/override-samples", headers=_auth(admin_token))
    assert r.status_code == 400


async def test_override_samples_requires_approver(client, viewer_token, admin_user, session):
    study = await _study_in_state(session, admin_user, "samples_mismatch")
    r = await client.post(f"/api/validation-studies/{study.id}/override-samples", headers=_auth(viewer_token))
    assert r.status_code == 403


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


async def _paper(session, org_id, title):
    from app.models.literature import LiteraturePaper

    paper = LiteraturePaper(
        organization_id=org_id,
        title=title,
        title_normalized=title.lower(),
        provenance="manual",
    )
    session.add(paper)
    await session.commit()
    return paper


async def test_study_response_titles_by_paper_when_linked(client, admin_token, admin_user, session):
    """A study reproducing a library paper is named by that paper's title, not 'Study #{id}'."""
    paper = await _paper(session, admin_user.organization_id, "A Landmark RNA-seq Reproduction")
    sid = (
        await client.post("/api/validation-studies", json={"paper_id": paper.id}, headers=_auth(admin_token))
    ).json()["id"]

    r = await client.get(f"/api/validation-studies/{sid}", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "A Landmark RNA-seq Reproduction"


async def test_study_title_ladder_falls_back_doi_then_accession_then_id(client, admin_token):
    """No paper: title falls back down the ladder DOI -> accession -> 'Study #{id}'."""
    by_doi = (
        await client.post("/api/validation-studies", json={"source_doi": "10.1/xyz"}, headers=_auth(admin_token))
    ).json()
    assert by_doi["title"] == "10.1/xyz"

    by_acc = (
        await client.post("/api/validation-studies", json={"source_accession": "GSE99"}, headers=_auth(admin_token))
    ).json()
    assert by_acc["title"] == "GSE99"

    bare = (await client.post("/api/validation-studies", json={}, headers=_auth(admin_token))).json()
    assert bare["title"] == f"Study #{bare['id']}"


async def test_list_studies_resolves_titles_for_a_mixed_batch(client, admin_token, admin_user, session):
    """The list resolves the same title ladder, batching the paper-title lookup."""
    paper = await _paper(session, admin_user.organization_id, "Batched Title Paper")
    linked = (
        await client.post("/api/validation-studies", json={"paper_id": paper.id}, headers=_auth(admin_token))
    ).json()["id"]
    accd = (
        await client.post("/api/validation-studies", json={"source_accession": "GSE_BATCH"}, headers=_auth(admin_token))
    ).json()["id"]

    r = await client.get("/api/validation-studies", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    items = {s["id"]: s for s in r.json()}
    assert items[linked]["title"] == "Batched Title Paper"
    assert items[accd]["title"] == "GSE_BATCH"


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


async def test_plan_surfaces_the_papers_tool_list(client, admin_token, monkeypatch):
    """The tool list is the input an attributed divergence is argued from, so a human ratifying a
    verdict has to be able to see it."""
    _patch_llm(monkeypatch, _GOOD)
    sid = (await client.post("/api/validation-studies", json={}, headers=_auth(admin_token))).json()["id"]
    r = await client.post(f"/api/validation-studies/{sid}/read", json={"full_text": "x"}, headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["plan"]["tools"] == ["TopHat"]
