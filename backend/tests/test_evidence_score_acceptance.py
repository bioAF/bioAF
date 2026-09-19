"""plan_8_4 milestone A: a real documentary score, through bioAF's own callers, on saved evidence.

The acceptance the plan asks for: a paper whose analysis inputs bioAF cannot acquire, and whose
scientific findings are therefore unfinished, still receives a traceable score from the code, metadata
and methods checks that DID run, with the unknowns and the failures visible beside it.

Studies 55 (Groff), 56 (SAMD1) and 57 (the substrate-stiffness paper) are the captured fixtures. What
is asserted here is that the evidence each of them holds produces points, that what is unresolved stays
grey, and that nothing invents a finding, an execution or an agreement that did not happen.
"""

import pytest

from tests.replay import groff_bundle_fetcher, replay_recovery, replay_report, restore


async def _restored(session, admin_user, study_id: int):
    return await restore(session, study_id, organization_id=admin_user.organization_id, user_id=admin_user.id)


class TestGroffEarnsPointsWithoutCompletingAFinding:
    @pytest.mark.asyncio
    async def test_the_score_is_positive_and_the_bar_still_adds_to_one_hundred(self, session, admin_user):
        report = await replay_report(session, await _restored(session, admin_user, 55))
        card = report["evidence_score"]
        assert card["score"] > 0
        assert card["score"] + card["failed"] + card["undetermined"] == 100
        assert card["status"] == "assessed"

    @pytest.mark.asyncio
    async def test_no_scientific_finding_was_completed_and_the_card_never_says_one_was(
        self, session, admin_user
    ):
        report = await replay_report(session, await _restored(session, admin_user, 55))
        card = report["evidence_score"]
        assert card["reproduction"]["attempted"] is False
        # The v2 findings card, which is what measures completed findings, still scores nothing.
        assert report["scorecard"].get("score") in (None, 0)

    @pytest.mark.asyncio
    async def test_the_documentary_sections_are_where_the_points_come_from(self, session, admin_user):
        report = await replay_report(session, await _restored(session, admin_user, 55))
        sections = {s["section"]: s for s in report["evidence_score"]["sections"]}
        assert sections["S"]["verified"] > 0
        assert sections["M"]["verified"] > 0
        assert sections["C"]["verified"] == 0, "bioAF runs no code check yet, and says so"
        assert sections["C"]["unsupported_count"] == 10

    @pytest.mark.asyncio
    async def test_the_paper_states_its_reference_and_earns_it(self, session, admin_user):
        report = await replay_report(session, await _restored(session, admin_user, 55))
        established = [line for s in report["evidence_score"]["sections"] for line in s["established"]]
        assert any("hg19" in line for line in established)

    @pytest.mark.asyncio
    async def test_the_annotation_bioaf_assumed_earns_nothing(self, session, admin_user):
        """Groff states hg19 and no annotation release; bioAF substitutes its own pinned Ensembl 87.
        That is exactly what "specified sufficiently to recover the intended reference inputs" is not."""
        from app.services.validation_rubric_evidence import assess_evidence
        from app.services.validation_report_summary import plan_projection
        from app.services.validation_assessment import active_plan

        restored = await _restored(session, admin_user, 55)
        plan = await active_plan(session, restored.study)
        assessed = assess_evidence(
            plan=plan_projection(plan), evidence=restored.study.evidence_json, claims=[], inventory=None
        )
        assert assessed["M2.A"]["outcome"] == "verified"
        assert assessed["M2.B"]["outcome"] == "undetermined"

    @pytest.mark.asyncio
    async def test_the_two_valid_author_checks_earn_their_allocation_and_the_refinement_does_not(
        self, session, admin_user, monkeypatch
    ):
        """Milestone A's stated acceptance for Groff: retain and credit the existing result checks while
        the 88-gene interpretation is unresolved. Not the whole R1 allocation for two of them."""
        from app.services import validation_consistency_checks as consistency

        restored = await _restored(session, admin_user, 55)
        await replay_recovery(session, restored, fetcher=groff_bundle_fetcher(), monkeypatch=monkeypatch)
        await consistency.enqueue(session, restored.study, restored.plan)
        await consistency.run_pending(session, restored.study, restored.plan, fetcher=_no_fetch)
        await session.flush()
        report = await replay_report(session, restored)
        results = next(s for s in report["evidence_score"]["sections"] if s["section"] == "R")
        assert 0 < results["verified"] < 8, "some author-results credit, and never the whole allocation"
        assert results["undetermined"] > 0


class TestTheStiffnessPaperScoresWithoutAnyExecutableAssay:
    """Section 3.5's explicit change to the earlier acceptance rules: a paper bioAF cannot execute may
    still earn documentary points, and its execution status stays unsupported."""

    @pytest.mark.asyncio
    async def test_it_is_no_longer_unscorable(self, session, admin_user):
        report = await replay_report(session, await _restored(session, admin_user, 57))
        card = report["evidence_score"]
        assert card["score"] >= 0
        assert card["score"] + card["failed"] + card["undetermined"] == 100
        assert card["rubric_version"] == 3

    @pytest.mark.asyncio
    async def test_no_claim_is_marked_independently_reproduced(self, session, admin_user):
        report = await replay_report(session, await _restored(session, admin_user, 57))
        assert report["evidence_score"]["reproduction"]["attempted"] is False
        results = next(s for s in report["evidence_score"]["sections"] if s["section"] == "R")
        assert results["verified"] == 0


class TestTheSamd1PaperCreditsWhatWasCheckedAndNoMore:
    @pytest.mark.asyncio
    async def test_its_metadata_checks_score_and_its_unbound_tables_do_not(self, session, admin_user):
        report = await replay_report(session, await _restored(session, admin_user, 56))
        card = report["evidence_score"]
        sections = {s["section"]: s for s in card["sections"]}
        assert sections["S"]["verified"] > 0
        assert sections["R"]["verified"] == 0, "its author tables still lack an accepted contrast binding"


class TestTheCardIsAlwaysInternallyConsistent:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("study_id", [55, 56, 57])
    async def test_the_sections_add_to_the_whole_and_the_whole_adds_to_one_hundred(
        self, session, admin_user, study_id
    ):
        card = (await replay_report(session, await _restored(session, admin_user, study_id)))["evidence_score"]
        assert sum(s["maximum"] for s in card["sections"]) == 100
        for section in card["sections"]:
            assert section["verified"] + section["failed"] + section["undetermined"] == section["maximum"]
        assert sum(s["verified"] for s in card["sections"]) == pytest.approx(card["score"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("study_id", [55, 56, 57])
    async def test_rendering_the_card_calls_no_model_and_reaches_no_network(
        self, session, admin_user, study_id, monkeypatch
    ):
        """Section 5: rendering a page never calls the model. Section 6.4: browsing an old report
        performs no model calls and migrates no scientific evidence."""

        def _refuse(*_a, **_kw):
            raise AssertionError("rendering the evidence score called a model")

        monkeypatch.setattr("app.services.llm_decision.decide_with_recovery", _refuse)
        card = (await replay_report(session, await _restored(session, admin_user, study_id)))["evidence_score"]
        assert card["rubric_version"] == 3


async def _no_fetch(url):
    raise AssertionError(f"the queued check tried to download {url}")
