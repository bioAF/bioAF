"""plan_8_2 section 2.1 and owner decision 5: an audited, on-request recovery of a study's checks.

Studies 44 and 45 hold comparisons made before bindings and a table read by the old decoder. Nothing
historical is rerun automatically; a person asks for a recovery, sees what it will reuse and change, and
bioAF carries it out through the product path: the passages that cite each supplement are read again, the
supplements are checked again under the binding contract, the dependent checks are re-evaluated, and the
projection is rebuilt. The prior plan, outcomes, classification and scorecard stay in history with the
reason, the actor and the build. A recovery never launches a workflow and asks no model.
"""

import io
import zipfile

import pytest
import pytest_asyncio

from app.models.audit_log import AuditLog
from app.services import validation_check_queue as queue
from app.services import validation_consistency_checks as consistency
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService
from tests.test_binding_acceptance import _GROFF, _GROFF_CLAIMS, _GROFF_CONTRASTS, _S3

_JATS = (_GROFF / "fulltext_jats.xml").read_text()


def _legacy_supplements():
    """The Groff supplement rows as a study before bindings recorded them: no passages, and every claim
    compared with S3, the three invalid disagreements among them."""
    from app.services.supplement_inventory import parse_jats_supplements

    rows = parse_jats_supplements(_JATS)
    for row in rows:
        row.pop("citing_passages", None)
        if row.get("filename") == _S3:
            row.update(
                resolved=True,
                role="results_table",
                consistency=[
                    {"claim_index": 0, "outcome": "not_checkable", "table": _S3, "reason": "no cutoff"},
                    {"claim_index": 1, "outcome": "disagree", "table": _S3, "rows_passing": 194, "reason": "194 vs 53"},
                    {"claim_index": 2, "outcome": "disagree", "table": _S3, "rows_passing": 194, "reason": "194 vs 10"},
                    {"claim_index": 3, "outcome": "disagree", "table": _S3, "rows_passing": 194, "reason": "194 vs 9"},
                ],
            )
    return rows


async def _legacy_groff(session, user):
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id)
    plan = await ReproductionPlanService.create_plan(
        session,
        study,
        user.id,
        accessions=["EGAS00000000001"],
        differential_design={"contrasts": [{**c, "reported_experiment_id": "e1"} for c in _GROFF_CONTRASTS]},
        reported_experiments=[{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0, 1, 2, 3]}],
    )
    targets = await ReproductionPlanService.add_comparison_targets(
        session, plan, [{**c, "metric_key": "", "reported_experiment_id": "e1"} for c in _GROFF_CLAIMS]
    )
    study.state = "classified"
    study.classification = "access_restricted"
    study.evidence_json = {"pmcid": "PMC6771404", "supplements": _legacy_supplements()}
    await session.flush()
    # The records the queue kept before bindings: done, each with the supplement's comparison.
    for index, target in enumerate(targets):
        held = study.evidence_json["supplements"]
        s3 = next(r for r in held if r.get("filename") == _S3)
        record = await queue.ensure_record(
            session,
            study,
            plan,
            target,
            queue.AUTHOR_RESULTS,
            {
                "table": {"name": _S3, "source": "supplement", "supplement_index": held.index(s3)},
                "identified_by": "only_table",
            },
        )
        await queue.finish(session, record, state=queue.DONE, outcome=dict(s3["consistency"][index]))
    await session.commit()
    return study, plan, targets


@pytest.fixture
def europe_pmc(monkeypatch):
    """The article's JATS and its supplementary bundle, as Europe PMC serves them."""
    from app.services import validation_assessment
    from app.services.literature.fulltext_service import FullTextFetchService, FullTextResult, _jats_sections
    from app.services.supplement_inventory import parse_jats_supplements

    calls = {"text": 0, "bundle": 0}

    async def fetch(*, doi=None, pmid=None, pmcid=None):
        calls["text"] += 1
        return FullTextResult(
            text="text",
            source="europepmc",
            external_id="PMC6771404",
            supplements=parse_jats_supplements(_JATS),
            sections=_jats_sections(_JATS),
        )

    async def bundle(url, *args, **kwargs):
        calls["bundle"] += 1
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr(_S3, (_GROFF / "supplemental_file_3_siggenes.txt").read_bytes())
        return buf.getvalue()

    monkeypatch.setattr(FullTextFetchService, "fetch", staticmethod(fetch))
    monkeypatch.setattr(validation_assessment, "deposit_bytes_fetcher", bundle)
    return calls


@pytest.fixture
def no_launch(monkeypatch):
    """Any workflow launch fails the test: a recovery never starts one."""
    from app.services.validation_driver_service import ValidationDriverService

    async def refuse(*args, **kwargs):
        raise AssertionError("a recovery launched a workflow")

    monkeypatch.setattr(ValidationDriverService, "_launch", staticmethod(refuse))


class TestThePreview:
    @pytest.mark.asyncio
    async def test_the_preview_says_what_will_be_reused_fetched_and_rerun_and_changes_nothing(
        self, session, admin_user
    ):
        from app.services.validation_recovery import preview_recovery

        study, plan, targets = await _legacy_groff(session, admin_user)
        before = dict(study.evidence_json)
        preview = await preview_recovery(session, study)
        assert preview["available"] is True
        assert preview["launches_workflow"] is False and preview["model_calls"] == 0
        kinds = [a["kind"] for a in preview["actions"]]
        assert kinds == ["fetch_passages", "recheck_supplements", "reevaluate_checks", "refresh_projection"]
        assert len(preview["affected_checks"]) == 4
        assert all("before" in c["why"] for c in preview["affected_checks"])
        assert preview["fingerprint"]
        assert study.evidence_json == before
        assert {r.state for r in await queue.records_for(session, study.id)} == {queue.DONE}

    @pytest.mark.asyncio
    async def test_a_study_with_nothing_to_recover_says_so(self, session, admin_user):
        from app.services.validation_recovery import preview_recovery

        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        await session.flush()
        preview = await preview_recovery(session, study)
        assert preview["available"] is False and preview["actions"] == []


class TestTheRecovery:
    @pytest.mark.asyncio
    async def test_groffs_invalid_disagreements_are_withdrawn_and_kept_as_superseded_evidence(
        self, session, admin_user, europe_pmc, no_launch
    ):
        from app.services.validation_recovery import run_recovery
        from app.services.validation_report_summary import report_summary_for

        study, plan, targets = await _legacy_groff(session, admin_user)
        prior_classification = study.classification
        result = await run_recovery(session, study, user_id=admin_user.id)
        await session.commit()
        assert result["requeued"] == 4 and europe_pmc == {"text": 1, "bundle": 1}

        # The queue re-evaluates the checks under the binding contract, from what the recovery recorded.
        await consistency.run_pending(session, study, plan, fetcher=_refuse_download)
        await session.commit()
        records = {r.comparison_target_id: r for r in await queue.records_for(session, study.id)}
        assert records[targets[0].id].outcome_json["binding"]["status"] == "established"
        for target in targets[1:]:
            record = records[target.id]
            assert record.state == queue.UNRESOLVED and record.terminal_reason == queue.BINDING
            # The invalid comparison is kept, whole, as the superseded revision.
            assert record.history_json[-1]["outcome"]["outcome"] == "disagree"
            assert record.history_json[-1]["superseded_because"].startswith("recovery")

        summary = await report_summary_for(session, study, admin_user.organization_id)
        assert "disagree" not in {(c.get("consistency") or {}).get("outcome") for c in summary["claims"]}
        assert summary["claims"][1]["consistency"]["superseded"]["outcome"] == "disagree"
        entry = study.evidence_json["recovery_history"][-1]
        assert entry["actor"] == admin_user.id and entry["build"]["commit"]
        assert entry["prior"]["classification"] == prior_classification
        assert len(entry["affected_checks"]) == 4
        assert study.classification == prior_classification  # nothing reclassified here
        audit = (
            await session.execute(
                AuditLog.__table__.select().where(
                    AuditLog.entity_type == "validation_study",
                    AuditLog.entity_id == study.id,
                    AuditLog.action == "recovery",
                )
            )
        ).all()
        assert len(audit) == 1

    @pytest.mark.asyncio
    async def test_checks_bound_under_the_current_rules_are_not_rerun(self, session, admin_user, europe_pmc, no_launch):
        from app.services.validation_recovery import run_recovery

        study, plan, targets = await _legacy_groff(session, admin_user)
        await run_recovery(session, study, user_id=admin_user.id)
        await consistency.run_pending(session, study, plan, fetcher=_refuse_download)
        await session.commit()
        revisions = {r.id: r.revision for r in await queue.records_for(session, study.id)}
        second = await run_recovery(session, study, user_id=admin_user.id)
        await session.commit()
        assert second["requeued"] == 0
        assert {r.id: r.revision for r in await queue.records_for(session, study.id)} == revisions

    @pytest.mark.asyncio
    async def test_a_changed_preview_is_refused_rather_than_carried_out_unseen(self, session, admin_user, europe_pmc):
        from app.services.validation_recovery import PreviewChanged, run_recovery

        study, plan, targets = await _legacy_groff(session, admin_user)
        with pytest.raises(PreviewChanged):
            await run_recovery(session, study, user_id=admin_user.id, preview_fingerprint="an earlier preview")


async def _refuse_download(url):
    raise AssertionError(f"the recovery's re-evaluation reuses what it recorded: {url}")


class TestTheApi:
    @pytest_asyncio.fixture(autouse=True)
    async def _enable(self, session):
        from app.services import beta_features_service

        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()

    @pytest.mark.asyncio
    async def test_a_viewer_may_preview_and_may_not_recover(
        self, session, admin_user, viewer_token, client, europe_pmc, no_launch
    ):
        study, plan, targets = await _legacy_groff(session, admin_user)
        headers = {"Authorization": f"Bearer {viewer_token}"}
        preview = await client.get(f"/api/validation-studies/{study.id}/recovery", headers=headers)
        assert preview.status_code == 200 and preview.json()["available"] is True
        refused = await client.post(f"/api/validation-studies/{study.id}/recovery", json={}, headers=headers)
        assert refused.status_code == 403

    @pytest.mark.asyncio
    async def test_a_requester_recovers_through_the_api_and_the_report_says_what_changed(
        self, session, admin_user, admin_token, client, europe_pmc, no_launch
    ):
        study, plan, targets = await _legacy_groff(session, admin_user)
        headers = {"Authorization": f"Bearer {admin_token}"}
        preview = (await client.get(f"/api/validation-studies/{study.id}/recovery", headers=headers)).json()
        r = await client.post(
            f"/api/validation-studies/{study.id}/recovery",
            json={"preview_fingerprint": preview["fingerprint"]},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["recovery"]["requeued"] == 4
        assert body["report_summary"]["recovery"]["last"]["actor"] == admin_user.id

    @pytest.mark.asyncio
    async def test_a_study_being_read_is_not_recovered(self, session, admin_user, admin_token, client, europe_pmc):
        study, plan, targets = await _legacy_groff(session, admin_user)
        study.state = "reading"
        await session.commit()
        headers = {"Authorization": f"Bearer {admin_token}"}
        r = await client.post(f"/api/validation-studies/{study.id}/recovery", json={}, headers=headers)
        assert r.status_code == 409


class TestASamd1ShapedStudy:
    @pytest.mark.asyncio
    async def test_only_the_dependent_check_is_reevaluated_and_the_refused_reanalysis_waits_for_approval(
        self, session, admin_user, no_launch
    ):
        from app.services.validation_recovery import preview_recovery, run_recovery
        from tests.test_binding_acceptance import _P01, _SAMD1_CONTRASTS, _SAMD1_TABLES, _count, _deposit_study

        study, plan, targets = await _deposit_study(
            session,
            admin_user,
            contrasts=_SAMD1_CONTRASTS,
            experiments=[
                {"id": "e2", "assay": "bulk RNA-seq", "claim_indices": [0, 1]},
                {"id": "e3", "assay": "bulk RNA-seq", "claim_indices": [2]},
            ],
            claims=[_count(257, 0, _P01, "up"), _count(524, 0, _P01, "down"), _count(5904, 1, _P01)],
            tables=_SAMD1_TABLES,
            input_table="GSE999001_RNA-Seq_DeSeq2.txt.gz",
            selected=0,
        )
        study.state = "classified"
        study.evidence_json = {
            **study.evidence_json,
            "completion": {
                "limitations": [
                    {
                        "kind": "input_unreadable",
                        "detail": "GSE999001_RNA-Seq_DeSeq2.txt.gz is not a readable table (format looks binary)",
                    }
                ]
            },
        }
        # Two checks the current rules decided, one done before bindings, and the refused reanalysis.
        current = await consistency.enqueue(session, study, plan)
        for record in current[:2]:
            await queue.finish(
                session,
                record,
                state=queue.UNRESOLVED,
                outcome={"outcome": "unresolved", "binding": {"version": 1, "status": "candidate"}},
                terminal_reason=queue.BINDING,
            )
        legacy = current[2]
        legacy.dependencies_json = {k: v for k, v in legacy.dependencies_json.items() if k != "binding_version"}
        await queue.finish(
            session,
            legacy,
            state=queue.DONE,
            outcome={"outcome": "unresolved", "table": "diff.txt.gz", "reason": "headerless"},
        )
        reanalysis = await queue.ensure_record(
            session, study, plan, targets[0], queue.PROCESSED_REANALYSIS, {"analysis": 1}, analysis_key="a1"
        )
        await queue.finish(session, reanalysis, state=queue.UNRESOLVED, outcome={"outcome": "not_executed"})
        await session.commit()
        revisions = {r.id: r.revision for r in await queue.records_for(session, study.id)}

        preview = await preview_recovery(session, study)
        assert [a["check_id"] for a in preview["affected_checks"]] == [legacy.check_id]
        assert [a["check_id"] for a in preview["needs_approval"]] == [reanalysis.check_id]

        await run_recovery(session, study, user_id=admin_user.id)
        await session.commit()
        after = {r.id: (r.revision, r.state) for r in await queue.records_for(session, study.id)}
        assert after[legacy.id] == (revisions[legacy.id] + 1, queue.PENDING)
        for record in (*current[:2], reanalysis):
            assert after[record.id][0] == revisions[record.id]
