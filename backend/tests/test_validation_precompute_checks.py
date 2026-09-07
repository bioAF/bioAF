"""plan_7 step 14: the cheap checks, before any Kubernetes compute.

Step 6 inspects the matrix, but only after download and only on the GEO route. These four run at
READ time, on either route, and cost no additional HTTP call because step 13 has already fetched the
deposit inventory and the sample manifest.

**The timing is the point.** The C1 gate is pre-approval. Run these in the driver and the approver
authorises the spend without the information the checks exist to give them.

**Deterministic blocks, judgment advises.** A species mismatch is a string comparison and it
invalidates every number downstream, so it blocks approval. The three sufficiency judgments are
opinions about a paper, and refusing on one would contradict this plan's own rule that nothing about
a paper rules it in or out. A thin methods section is a FINDING, and step 18 exists to attempt it
anyway.
"""

import pytest

from app.services.validation_precompute_checks import (
    MISMATCH,
    OK,
    UNKNOWN,
    check_sample_data,
    check_species,
    run_precompute_checks,
)


class _Client:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.prompts: list[str] = []

    async def submit(self, prompt, payload, model, api_key, attachments=None):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.response


def _judgment(answer="yes", reason="the methods name every tool and threshold", confidence=0.9):
    import json

    return "```json\n" + json.dumps({"answer": answer, "reason": reason, "confidence": confidence}) + "\n```"


def _entry(filename, classification, level="series", gsm=None):
    from app.services.literature.deposit_inventory_service import DepositEntry

    return DepositEntry(
        filename=filename, url=f"https://x/{filename}", classification=classification, level=level, gsm=gsm
    )


# ---- species: a fact, and it blocks ----


class TestSpecies:
    def test_matching_organisms_are_ok(self):
        assert check_species("Homo sapiens", ["Homo sapiens", "Homo sapiens"])["verdict"] == OK

    def test_a_human_plan_over_mouse_data_is_a_mismatch(self):
        """Nothing in the product checks species today, and a human-genome run on mouse data is a
        confident wrong answer that costs hours first."""
        result = check_species("Homo sapiens", ["Mus musculus"])
        assert result["verdict"] == MISMATCH
        assert result["blocking"] is True

    def test_the_mismatch_names_both_organisms_and_where_each_came_from(self):
        result = check_species("Homo sapiens", ["Mus musculus"])
        assert "Homo sapiens" in result["detail"]
        assert "Mus musculus" in result["detail"]
        assert "paper" in result["detail"].lower()
        assert "deposit" in result["detail"].lower()

    def test_it_is_a_comparison_not_an_opinion(self):
        """Deterministic: no model made this call and none can be blamed for it."""
        assert check_species("Homo sapiens", ["Mus musculus"])["decided_by"] == "measurement"

    @pytest.mark.parametrize(
        "plan_organism,deposit",
        [("homo sapiens", ["Homo sapiens"]), ("Homo Sapiens ", [" homo sapiens"])],
    )
    def test_case_and_whitespace_do_not_invent_a_mismatch(self, plan_organism, deposit):
        assert check_species(plan_organism, deposit)["verdict"] == OK

    def test_a_deposit_that_declares_nothing_is_unknown_and_does_not_block(self):
        """Not knowing is not the same as disagreeing, and blocking on it would refuse every deposit
        whose series matrix omits the field."""
        result = check_species("Homo sapiens", [])
        assert result["verdict"] == UNKNOWN
        assert result["blocking"] is False

    def test_a_plan_with_no_organism_is_unknown(self):
        result = check_species(None, ["Homo sapiens"])
        assert result["verdict"] == UNKNOWN
        assert result["blocking"] is False

    def test_a_mixed_species_deposit_is_a_mismatch_when_the_plan_is_not_among_them(self):
        assert check_species("Danio rerio", ["Homo sapiens", "Mus musculus"])["verdict"] == MISMATCH

    def test_a_mixed_species_deposit_containing_the_plan_organism_is_ok(self):
        """A xenograft or a spike-in deposits two organisms and the plan names the one being
        analysed. Refusing that would be a wrong answer about a correct study."""
        assert check_species("Homo sapiens", ["Homo sapiens", "Mus musculus"])["verdict"] == OK


# ---- the deposit against what the paper describes ----


class TestSampleData:
    def test_a_deposit_matching_the_paper_is_ok(self):
        entries = [_entry(f"GSM{i}_counts.tsv", "matrix_counts", level="sample", gsm=f"GSM{i}") for i in range(6)]
        assert check_sample_data(paper_sample_count=6, entries=entries)["verdict"] == OK

    def test_twenty_one_described_samples_and_five_bigwigs_is_a_mismatch(self):
        """The example from the plan, and it is worth knowing before the spend rather than after."""
        entries = [_entry(f"s{i}.bigwig", "coverage", level="sample", gsm=f"GSM{i}") for i in range(5)]
        result = check_sample_data(paper_sample_count=21, entries=entries)
        assert result["verdict"] == MISMATCH
        assert "21" in result["detail"]

    def test_a_mismatch_here_never_blocks(self):
        """Advisory. The deposit route can still work from a series-level matrix that has no
        per-sample files at all."""
        entries = [_entry("s1.bigwig", "coverage", level="sample", gsm="GSM1")]
        assert check_sample_data(paper_sample_count=21, entries=entries)["blocking"] is False

    def test_a_series_level_matrix_is_not_counted_as_a_missing_sample(self):
        """One file holding every sample as a COLUMN is the commonest usable shape, and counting it
        as one sample would flag every well-formed deposit."""
        entries = [_entry("GSE1_counts.tsv", "matrix_counts")]
        assert check_sample_data(paper_sample_count=6, entries=entries)["verdict"] == OK

    def test_no_stated_sample_count_is_unknown(self):
        assert check_sample_data(paper_sample_count=None, entries=[])["verdict"] == UNKNOWN

    def test_an_empty_deposit_is_unknown_rather_than_a_mismatch(self):
        """Nothing listed is a discovery problem, and step 13's row already says so."""
        assert check_sample_data(paper_sample_count=6, entries=[])["verdict"] == UNKNOWN


# ---- the two judgments ----


class TestTheJudgments:
    @pytest.mark.asyncio
    async def test_a_detailed_methods_section_is_ok_with_the_models_reasoning(self):
        checks = await run_precompute_checks(
            methods_text="We aligned with STAR 2.7.10a to GRCh38 and called DE with DESeq2 at padj < 0.05.",
            samples_text="Six samples: three control, three knockdown.",
            plan_organism="Homo sapiens",
            deposit_organisms=["Homo sapiens"],
            paper_sample_count=6,
            entries=[_entry("GSE1_counts.tsv", "matrix_counts")],
            client=_Client(_judgment("yes")),
            model="claude-opus-4-8",
            api_key=None,
        )
        methods = checks["methods_detailed_enough"]
        assert methods["verdict"] == OK
        assert methods["decided_by"] == "model"
        assert methods["model"] == "claude-opus-4-8"
        assert methods["reason"]
        assert methods["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_a_thin_methods_section_is_a_finding_that_does_not_block(self):
        """Advisory, including for step 18: an inadequate-methods judgment is recorded and carried
        into the report, and it does not prevent the attempt."""
        checks = await run_precompute_checks(
            methods_text="Standard protocols were used.",
            samples_text="Samples were sequenced.",
            plan_organism="Homo sapiens",
            deposit_organisms=["Homo sapiens"],
            paper_sample_count=6,
            entries=[_entry("GSE1_counts.tsv", "matrix_counts")],
            client=_Client(_judgment("no", reason="no aligner, genome build or threshold is named")),
            model="m",
            api_key=None,
        )
        assert checks["methods_detailed_enough"]["verdict"] == MISMATCH
        assert checks["methods_detailed_enough"]["blocking"] is False
        assert checks["samples_described_enough"]["blocking"] is False

    @pytest.mark.asyncio
    async def test_two_model_calls_and_no_more(self):
        """The cost, stated plainly: two LLM calls on every paper read, including papers nobody
        approves. That is the price of the approver seeing this before authorising compute."""
        client = _Client(_judgment())
        await run_precompute_checks(
            methods_text="m",
            samples_text="s",
            plan_organism="Homo sapiens",
            deposit_organisms=["Homo sapiens"],
            paper_sample_count=1,
            entries=[_entry("GSE1_counts.tsv", "matrix_counts")],
            client=client,
            model="m",
            api_key=None,
        )
        assert len(client.prompts) == 2

    @pytest.mark.asyncio
    async def test_a_provider_failure_leaves_the_judgments_unknown_and_the_facts_answered(self):
        from app.services.llm_provider_clients import ProviderError

        issues: list[dict] = []
        checks = await run_precompute_checks(
            methods_text="m",
            samples_text="s",
            plan_organism="Homo sapiens",
            deposit_organisms=["Mus musculus"],
            paper_sample_count=6,
            entries=[_entry("GSE1_counts.tsv", "matrix_counts")],
            client=_Client(error=ProviderError("down", error_class="server")),
            model="m",
            api_key=None,
            on_issue=issues.append,
        )
        assert checks["methods_detailed_enough"]["verdict"] == UNKNOWN
        # The deterministic half is unaffected, and it is the half that blocks.
        assert checks["species_matches"]["verdict"] == MISMATCH
        assert issues, "a failed judgment left no record"

    @pytest.mark.asyncio
    async def test_no_methods_text_asks_nothing_and_answers_unknown(self):
        """A paper we could not read is not a paper with a thin methods section."""
        client = _Client(_judgment())
        checks = await run_precompute_checks(
            methods_text="",
            samples_text="",
            plan_organism="Homo sapiens",
            deposit_organisms=["Homo sapiens"],
            paper_sample_count=1,
            entries=[_entry("GSE1_counts.tsv", "matrix_counts")],
            client=client,
            model="m",
            api_key=None,
        )
        assert checks["methods_detailed_enough"]["verdict"] == UNKNOWN
        assert client.prompts == []


class TestWhatBlocks:
    """The split the DESIRED STATE asks for: the outcome of these decides whether spending compute
    is worthwhile."""

    @pytest.mark.asyncio
    async def test_only_the_species_check_ever_blocks(self):
        checks = await run_precompute_checks(
            methods_text="thin",
            samples_text="thin",
            plan_organism="Homo sapiens",
            deposit_organisms=["Mus musculus"],
            paper_sample_count=21,
            entries=[_entry("s1.bigwig", "coverage", level="sample", gsm="GSM1")],
            client=_Client(_judgment("no")),
            model="m",
            api_key=None,
        )
        blocking = [k for k, v in checks.items() if v.get("blocking")]
        assert blocking == ["species_matches"]


class TestTheDepositsOwnSpeciesDeclaration:
    """The series matrix states the organism per sample. That is the depositor's own statement of
    what the data IS, which is the same authority `library_strategy` carries over a paper's prose."""

    def test_it_reads_the_organism_line(self):
        from app.services.literature.accession_manifest_service import parse_series_organisms

        matrix = '!Sample_title\t"a"\t"b"\n!Sample_organism_ch1\t"Homo sapiens"\t"Homo sapiens"\n'
        assert parse_series_organisms(matrix) == ["Homo sapiens"]

    def test_a_two_organism_deposit_keeps_both(self):
        from app.services.literature.accession_manifest_service import parse_series_organisms

        matrix = '!Sample_organism_ch1\t"Homo sapiens"\t"Mus musculus"\n'
        assert parse_series_organisms(matrix) == ["Homo sapiens", "Mus musculus"]

    def test_a_matrix_that_omits_the_field_yields_nothing(self):
        from app.services.literature.accession_manifest_service import parse_series_organisms

        assert parse_series_organisms('!Sample_title\t"a"\n') == []


class TestTheSpeciesHoldBlocksApproval:
    """What CONSUMES the checks. Computing them and only rendering them would leave the DESIRED
    STATE sentence unmet: the outcome of these decides whether spending compute is worthwhile.

    Not a driver hold: `_hold_deposit` is deposit-route specific and these run BEFORE approval, so
    there is no driver state to hold in. The gate already blocks on other conditions; this is one
    more.
    """

    @staticmethod
    async def _study_at_gate(session, admin_user, checks):
        from app.models.validation_study import ValidationStudy
        from app.services.reproduction_plan_service import ReproductionPlanService

        study = ValidationStudy(
            organization_id=admin_user.organization_id,
            requested_by_user_id=admin_user.id,
            source_accession="GSE1",
            state="plan_ready",
            evidence_json={"precompute_checks": checks},
        )
        session.add(study)
        await session.flush()
        await ReproductionPlanService.create_plan(
            session, study, admin_user.id, pipeline_key="nf-core/rnaseq", reference_genome="GRCh38"
        )
        return study

    @pytest.mark.asyncio
    async def test_a_species_mismatch_refuses_approval_and_says_which_organisms(self, session, admin_user):
        from fastapi import HTTPException

        from app.services.validation_study_service import ValidationStudyService

        study = await self._study_at_gate(
            session, admin_user, {"species_matches": check_species("Homo sapiens", ["Mus musculus"])}
        )
        with pytest.raises(HTTPException) as e:
            await ValidationStudyService.approve_plan(
                session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
            )
        assert e.value.status_code == 400
        assert "Homo sapiens" in e.value.detail
        assert "Mus musculus" in e.value.detail
        assert study.state == "plan_ready"

    @pytest.mark.asyncio
    async def test_a_matching_species_approves_normally(self, session, admin_user):
        from app.services.validation_study_service import ValidationStudyService

        study = await self._study_at_gate(
            session, admin_user, {"species_matches": check_species("Homo sapiens", ["Homo sapiens"])}
        )
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        assert study.state == "acquiring_processed"

    @pytest.mark.asyncio
    async def test_an_unknown_species_does_not_block(self, session, admin_user):
        """A deposit that declares no organism is not a deposit that declares the wrong one."""
        from app.services.validation_study_service import ValidationStudyService

        study = await self._study_at_gate(session, admin_user, {"species_matches": check_species("Homo sapiens", [])})
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        assert study.state == "acquiring_processed"

    @pytest.mark.asyncio
    async def test_a_thin_methods_judgment_never_blocks_approval(self, session, admin_user):
        """Advisory means advisory. A thin methods section is a FINDING, and step 18 exists to
        attempt it anyway."""
        from app.services.validation_study_service import ValidationStudyService

        checks = await run_precompute_checks(
            methods_text="Standard protocols were used.",
            samples_text="Samples were sequenced.",
            plan_organism="Homo sapiens",
            deposit_organisms=["Homo sapiens"],
            paper_sample_count=21,
            entries=[_entry("s1.bigwig", "coverage", level="sample", gsm="GSM1")],
            client=_Client(_judgment("no")),
            model="m",
            api_key=None,
        )
        study = await self._study_at_gate(session, admin_user, checks)
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        assert study.state == "acquiring_processed"

    @pytest.mark.asyncio
    async def test_a_deliberate_override_is_recorded_and_lets_it_through(self, session, admin_user):
        """A person may override, and the override is on the record so a divergent verdict can be
        argued against the choice that produced it."""
        from app.services.validation_study_service import ValidationStudyService

        study = await self._study_at_gate(
            session, admin_user, {"species_matches": check_species("Homo sapiens", ["Mus musculus"])}
        )
        await ValidationStudyService.override_species_mismatch(
            session, study.id, admin_user.organization_id, admin_user.id, reason="the deposit's annotation is wrong"
        )
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        assert study.state == "acquiring_processed"
        override = study.evidence_json["species_override"]
        assert override["reason"] == "the deposit's annotation is wrong"
        assert override["by_user_id"] == admin_user.id
