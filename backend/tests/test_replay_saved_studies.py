"""plan_8_3 section 0.2: the live regression studies replay offline and reproduce what was recorded.

Gate 0. Studies 55 (Groff), 56 (SAMD1) and 57 (the substrate-stiffness paper) are where the deployed
build stops, and every repair in plan_8_3 is verified against them. Their evidence is captured as
committed fixtures, and these tests establish that driving bioAF's own callers over that evidence
reaches the outcomes the live run recorded, with no paper read, no model call and no compute.

A repair that cannot be demonstrated here is not ready for a live run.
"""

import json

import pytest

from tests.replay import (
    SAVED_STUDIES,
    consistency_of,
    groff_bundle_fetcher,
    load,
    replay_recovery,
    replay_consistency,
    replay_mapping,
    replay_report,
    restore,
)


async def _restored(session, admin_user, study_id: int):
    return await restore(session, study_id, organization_id=admin_user.organization_id, user_id=admin_user.id)


class TestTheCapturedFixturesAreRedistributable:
    """The fixture rules: no account, environment or credential detail reaches a committed fixture."""

    @pytest.mark.parametrize("study_id", sorted(SAVED_STUDIES))
    def test_no_account_or_environment_detail_is_committed(self, study_id):
        text = SAVED_STUDIES[study_id].read_text()
        for forbidden in (
            "bioaf-495400",
            "bioaf-demo",
            "@bioaf.co",
            "bioaf-results-bioaf-co",
            "sk-ant",
            "/home/brentmills",
        ):
            assert forbidden not in text, f"study {study_id}'s fixture holds {forbidden}"
        bundle = json.loads(text)
        for dropped in ("organization_id", "requested_by_user_id", "approved_by_user_id"):
            assert dropped not in bundle["study"]


class TestTheSavedStudiesRestoreAsTheyWereRecorded:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("study_id", sorted(SAVED_STUDIES))
    async def test_the_study_its_plan_its_targets_and_its_checks_come_back(self, session, admin_user, study_id):
        bundle = load(study_id)
        restored = await _restored(session, admin_user, study_id)
        assert restored.study.id == study_id
        assert restored.study.state == bundle["study"]["state"]
        assert restored.study.classification == bundle["study"]["classification"]
        assert restored.plan.id == bundle["reproduction_plans"][0]["id"]
        assert len(restored.targets) == len(bundle["comparison_targets"])
        assert len(restored.checks) == len(bundle["check_records"])


class TestGroffsParentCountAgreesAndItsRefinementIsNowReached:
    """Study 55's recorded stop was the 194-gene parent list AGREEING while the 88-gene refinement of
    that same list came back `not_checkable`: `list_evidence` wanted the claim's count and the table's
    citation in one sentence, and the paper states the refinement in the sentences after it.

    Section 1.2 links the refinement to the list it refines. The parent's outcome is unchanged, and
    what the refinement now reaches is the magnitude its wording does not state, which is an unresolved
    outcome with a control (a recorded filter-semantics confirmation), not credit.
    """

    @pytest.mark.asyncio
    async def test_the_parent_count_still_agrees(self, session, admin_user):
        restored = await _restored(session, admin_user, 55)
        outcomes = await replay_consistency(session, restored)
        assert outcomes[10]["outcome"] == "agree"
        assert outcomes[10]["rows_passing"] == 194

    @pytest.mark.asyncio
    async def test_the_queued_check_still_keeps_the_answer_it_recorded(self, session, admin_user):
        """A record made while the table's bytes were in hand is bioAF's answer until the stage that
        made it runs again. Nothing rewrites it in place, and nothing re-downloads on its own."""
        restored = await _restored(session, admin_user, 55)
        outcomes = await replay_consistency(session, restored)
        assert outcomes[11]["outcome"] == "not_checkable"
        assert outcomes[11]["reason"] == "the claim states no significance cutoff, and bioAF supplies none"

    @pytest.mark.asyncio
    async def test_a_recovery_offers_to_read_the_supplements_under_the_current_reading(self, session, admin_user):
        """How the repair reaches a study that already holds records. A comparison is computed while a
        supplement's bytes are in hand, and a bundle member has no address of its own to fetch again, so
        nothing recomputes it by itself. The recovery names it, and spends one download and no model."""
        from app.services.validation_recovery import preview_recovery

        restored = await _restored(session, admin_user, 55)
        preview = await preview_recovery(session, restored.study)
        recheck = next(a for a in preview["actions"] if a["kind"] == "recheck_supplements")
        assert "earlier reading" in recheck["detail"]
        assert preview["model_calls"] == 0
        assert preview["launches_workflow"] is False

    @pytest.mark.asyncio
    async def test_the_recovery_reaches_the_subset_operation_and_stops_on_its_magnitude(
        self, session, admin_user, monkeypatch
    ):
        """The refinement is linked to the 194-gene list it refines, the operation is reached, and it
        stops on the magnitude the paper's wording does not state. Not credit: an unresolved outcome
        with a control."""
        restored = await _restored(session, admin_user, 55)
        result = await replay_recovery(session, restored, fetcher=groff_bundle_fetcher(), monkeypatch=monkeypatch)
        assert result["rechecked_supplements"] is True
        records = {
            r["claim_index"]: r
            for r in consistency_of(
                restored.study.evidence_json or {}, "supp_gr.252981.119_Supplemental_File_3_XX-v-XY_siggenes.txt"
            )
        }
        assert records[10]["outcome"] == "agree"
        assert records[10]["rows_passing"] == 194
        assert records[11]["method"] == "published_subset_count"
        assert records[11]["outcome"] == "unresolved"
        assert "magnitude" in records[11]["reason"]
        assert records[11]["subset"]["parent"]["count"] == 194

    @pytest.mark.asyncio
    async def test_every_record_it_writes_names_the_reading_that_made_it(self, session, admin_user, monkeypatch):
        from app.services.validation_author_consistency import CONSISTENCY_VERSION

        restored = await _restored(session, admin_user, 55)
        await replay_recovery(session, restored, fetcher=groff_bundle_fetcher(), monkeypatch=monkeypatch)
        records = consistency_of(
            restored.study.evidence_json or {}, "supp_gr.252981.119_Supplemental_File_3_XX-v-XY_siggenes.txt"
        )
        assert records
        assert {r.get("consistency_version") for r in records} == {CONSISTENCY_VERSION}

    @pytest.mark.asyncio
    async def test_the_earlier_comparisons_are_kept(self, session, admin_user, monkeypatch):
        restored = await _restored(session, admin_user, 55)
        await replay_recovery(session, restored, fetcher=groff_bundle_fetcher(), monkeypatch=monkeypatch)
        history = (restored.study.evidence_json or {}).get("recovery_history") or []
        kept = history[-1]["prior"]["supplement_comparisons"]
        earlier = next(r for row in kept for r in row["consistency"] or [] if r.get("claim_index") == 11)
        assert earlier["outcome"] == "not_checkable"

    @pytest.mark.asyncio
    async def test_the_replayed_table_is_the_one_the_live_run_read(self, session, admin_user):
        restored = await _restored(session, admin_user, 55)
        outcomes = await replay_consistency(session, restored)
        recorded = next(
            c["outcome_json"]["decoding"]["source_checksum"]
            for c in restored.bundle["check_records"]
            if (c.get("outcome_json") or {}).get("claim_index") == 10
        )
        assert outcomes[10]["decoding"]["source_checksum"] == recorded


class TestTheSamd1MappingStopsOnItsClonesAndNoLongerOnItsArms:
    """Study 56's recorded stop was eight rows and twelve refusals: every row refused for its arm's
    condition, and the four KO rows also for their clone identity.

    Section 3.1 repaired the first half: the arms' conditions are the genotypes the records state, so
    all eight rows are accepted on their attributes (the per-row diff, with the superseded rule beside
    the current one, is `tests/fixtures/refusal_diffs/arm_condition_compatibility.json`). The second
    half is CORRECT and stands: nothing study 56 cites states which clone a KO column came from, and
    that is resolved through stage 5's own control, not by loosening the check.
    """

    @pytest.mark.asyncio
    async def test_the_mapping_is_still_held(self, session, admin_user):
        restored = await _restored(session, admin_user, 56)
        recorded = restored.bundle["study"]["evidence_json"]["input_choice"]["mapping_validation"]
        validation = await replay_mapping(session, restored)
        assert validation["status"] == recorded["status"] == "unresolved"

    @pytest.mark.asyncio
    async def test_no_row_is_refused_for_its_arms_condition_any_more(self, session, admin_user):
        restored = await _restored(session, admin_user, 56)
        validation = await replay_mapping(session, restored)
        assert [r for r in validation["reasons"] if "arm" in r] == []

    @pytest.mark.asyncio
    async def test_the_four_clone_identities_are_what_holds_it(self, session, admin_user):
        restored = await _restored(session, admin_user, 56)
        recorded = restored.bundle["study"]["evidence_json"]["input_choice"]["mapping_validation"]["reasons"]
        validation = await replay_mapping(session, restored)
        identity = [r for r in recorded if "biological unit" in r]
        assert len(identity) == 4
        assert validation["reasons"] == identity


class TestTheStiffnessPaperEndsWithScopeNotEstablished:
    """Study 57's recorded stop. Five assay types are correctly outside bioAF's methods, and that
    correct answer is obscured: the finding inventory's recovery attempt could not reach the provider,
    so the established membership was discarded and the report says the scope is not established."""

    @pytest.mark.asyncio
    async def test_replaying_the_report_reproduces_the_recorded_scorecard(self, session, admin_user):
        restored = await _restored(session, admin_user, 57)
        recorded = restored.bundle["study"]["evidence_json"]["scorecard_record"]["compact"]
        report = await replay_report(session, restored)
        card = report["scorecard"]
        assert card["display_score"] is None
        assert card["status"] == recorded["status"] == "not_established"
        assert card["cause"] == recorded["cause"] == "bioaf_limitation"
        assert "could not reach the language model" in card["reason"]

    @pytest.mark.asyncio
    async def test_the_five_assay_types_are_outside_bioafs_methods(self, session, admin_user):
        restored = await _restored(session, admin_user, 57)
        report = await replay_report(session, restored)
        assert report["applicability"]["status"] == "not_applicable"
        assert [e["support"] for e in report["applicability"]["experiments"]] == ["unsupported"] * 5


class TestGroffsClarificationPathRunsEndToEnd:
    """plan_8_4 milestone 0: the whole path for the 88-gene claim, through bioAF's own callers.

    The recovery reaches the subset operation and stops on the magnitude the paper's wording does not
    state. That is the point every earlier piece stopped at: the service takes a clarification, the
    endpoint takes one, and there was no way to give it one. Here the reading is recorded through the
    service the API calls, and what it reaches is followed to the projection every surface reads.

    Three readings are exercised. A justified absolute reading reaches the paper's own 88; a signed
    reading reaches 26 and DISAGREES, which is a legitimate scientific outcome and not a bug to be
    tuned away; no clarification leaves the magnitude open. Nothing here invents a confirmation to
    obtain a favourable number: the note states what a person would be resting the reading on, and the
    count that follows is whatever the table holds.
    """

    _TABLE = "supp_gr.252981.119_Supplemental_File_3_XX-v-XY_siggenes.txt"

    async def _recovered(self, session, admin_user, monkeypatch):
        restored = await _restored(session, admin_user, 55)
        await replay_recovery(session, restored, fetcher=groff_bundle_fetcher(), monkeypatch=monkeypatch)
        return restored

    async def _requeue(self, session, restored):
        """Run the queue, which is what the production worker does after a recovery. The queued check
        reuses the comparison the recovery made while the bytes were in hand."""
        from app.services import validation_consistency_checks as consistency

        await consistency.enqueue(session, restored.study, restored.plan)
        await consistency.run_pending(session, restored.study, restored.plan, fetcher=_no_fetch)
        await session.flush()

    def _row(self, restored, claim_index: int) -> dict:
        records = {r["claim_index"]: r for r in consistency_of(restored.study.evidence_json or {}, self._TABLE)}
        return records[claim_index]

    async def _confirm(self, session, restored, magnitude: bool, note: str):
        """The service the endpoint calls, with the entry the endpoint builds."""
        from app.services.validation_table_confirmations import confirmation_entry, record_confirmation

        target = sorted(restored.targets, key=lambda t: t.id)[11]
        contrast = (restored.plan.differential_design_json or {})["contrasts"][target.contrast_index]["name"]
        entry = confirmation_entry(
            table=self._TABLE,
            contrast=contrast,
            filter_semantics={"magnitude": magnitude},
            note=note,
            confirmed_by="a person",
            at="2026-09-18T00:00:00+00:00",
        )
        await record_confirmation(session, restored.study, restored.plan, entry, reason="a person recorded the reading")
        await session.flush()
        return entry

    @pytest.mark.asyncio
    async def test_with_no_clarification_the_magnitude_stays_open_and_the_report_says_so(
        self, session, admin_user, monkeypatch
    ):
        restored = await self._recovered(session, admin_user, monkeypatch)
        row = self._row(restored, 11)
        assert row["outcome"] == "unresolved"
        await self._requeue(session, restored)
        report = await replay_report(session, restored)
        semantics = report["claims"][11]["consistency"]["filter_semantics"]
        assert semantics["unresolved"] is True
        assert "magnitude" in semantics["reason"]
        assert semantics["statement"]

    @pytest.mark.asyncio
    async def test_a_recorded_reading_is_persisted_with_the_evidence_it_rests_on(
        self, session, admin_user, monkeypatch
    ):
        restored = await self._recovered(session, admin_user, monkeypatch)
        await self._confirm(session, restored, True, "the authors' code applies abs(log2FoldChange)")
        kept = (restored.study.evidence_json or {}).get("table_confirmations") or []
        assert kept[-1]["filter_semantics"] == {"magnitude": True}
        assert kept[-1]["note"] == "the authors' code applies abs(log2FoldChange)"
        assert kept[-1]["confirmed_by"] == "a person"

    @pytest.mark.asyncio
    async def test_the_absolute_reading_reaches_the_papers_own_count(self, session, admin_user, monkeypatch):
        restored = await self._recovered(session, admin_user, monkeypatch)
        await self._confirm(session, restored, True, "the authors' code applies abs(log2FoldChange)")
        # The comparison is recomputed where it is computed: while the table's bytes are in hand.
        await replay_recovery(session, restored, fetcher=groff_bundle_fetcher(), monkeypatch=monkeypatch)
        row = self._row(restored, 11)
        assert row["outcome"] == "agree"
        assert row["rows_passing"] == 88
        assert row["subset"]["filter"]["resolved_by"] == "confirmation"
        assert row["subset"]["parent"]["count"] == 194

    @pytest.mark.asyncio
    async def test_a_signed_reading_disagrees_and_that_stands(self, session, admin_user, monkeypatch):
        restored = await self._recovered(session, admin_user, monkeypatch)
        await self._confirm(session, restored, False, "the heatmap legend plots only the up-regulated genes")
        await replay_recovery(session, restored, fetcher=groff_bundle_fetcher(), monkeypatch=monkeypatch)
        row = self._row(restored, 11)
        assert row["outcome"] == "disagree"
        assert row["rows_passing"] == 26

    @pytest.mark.asyncio
    async def test_the_parent_count_is_untouched_by_either_reading(self, session, admin_user, monkeypatch):
        """The clarification settles the refinement's magnitude and nothing else. The 194-gene check
        rests on no filter at all and must not move."""
        restored = await self._recovered(session, admin_user, monkeypatch)
        await self._confirm(session, restored, False, "the heatmap legend plots only the up-regulated genes")
        await replay_recovery(session, restored, fetcher=groff_bundle_fetcher(), monkeypatch=monkeypatch)
        parent = self._row(restored, 10)
        assert parent["outcome"] == "agree"
        assert parent["rows_passing"] == 194

    @pytest.mark.asyncio
    async def test_the_settled_reading_reaches_the_report_and_its_counts(self, session, admin_user, monkeypatch):
        restored = await self._recovered(session, admin_user, monkeypatch)
        await self._confirm(session, restored, True, "the authors' code applies abs(log2FoldChange)")
        await replay_recovery(session, restored, fetcher=groff_bundle_fetcher(), monkeypatch=monkeypatch)
        await self._requeue(session, restored)
        report = await replay_report(session, restored)
        claim = report["claims"][11]
        assert claim["consistency"]["outcome"] == "agree"
        assert claim["consistency"]["filter_semantics"]["unresolved"] is False
        assert claim["consistency"]["filter_semantics"]["resolved_by"] == "confirmation"
        # Two claims are now checked against the authors' published results, and the counts line, the
        # summary sentence and the claims' own rows are still one answer.
        assert report["claim_counts"]["checked"] == 2
        assert "2 checked against the authors' published results" in report["claim_counts"]["label"]
        from app.services.validation_report_summary import report_contradictions

        assert report_contradictions(report) == []


async def _no_fetch(url):
    """A replayed queue holds no bytes of its own: a check that tries to download has lost the answer
    bioAF already has, and this makes that a failure rather than a silent network call."""
    raise AssertionError(f"the queued check tried to download {url}; it should reuse what the recovery computed")
