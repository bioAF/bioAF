"""Which of a paper's contrasts does THIS run reproduce?

A paper reports one contrast per finding, across every assay it ran. A plan runs one pipeline. The
gate used `contrasts[0]` and nothing matched the two up, so a multi-assay paper offered whichever
contrast the model happened to list first: GSE273743's paper states six and puts the ChIP-seq one
last, so a chipseq run was offered an RNA-seq knockout contrast.

This cannot be assigned by rule without encoding every way a paper can word an assay, so it is a
semantic decision: the model is given the contrasts and the pipeline being run, and picks.
"""

import pytest

from app.services import contrast_selection as cs

_CONTRASTS = [
    {"name": "Nkx2.2 knockout vs control alpha cells (RNA-seq)", "assay": "RNA-seq"},
    {"name": "Klf4-OE vs empty vector chromatin accessibility (ATAC-seq)", "assay": "ATAC-seq"},
    {"name": "NKX2.2 differential binding Klf4 siRNA vs siScramble (ChIP-seq)", "assay": "ChIP-seq"},
]


def _response(index, reason="the only ChIP-seq contrast", confidence=0.95):
    import json

    return "```json\n" + json.dumps({"contrast_index": index, "reason": reason, "confidence": confidence}) + "\n```"


class TestThePrompt:
    def test_it_shows_every_contrast_and_the_pipeline_being_run(self):
        system, payload = cs.build_contrast_prompt(_CONTRASTS, pipeline_key="nf-core/chipseq", assay="ChIP-seq")
        assert "nf-core/chipseq" in payload
        assert "NKX2.2 differential binding" in payload
        assert "[0]" in payload and "[2]" in payload
        # Selecting nothing has to be presented as a real answer, or a multi-assay paper always
        # gets some contrast reproduced whether or not this run could measure it.
        assert "null" in system.lower()
        assert "nothing is a correct answer" in system.lower()

    def test_it_says_the_choice_is_about_what_the_run_can_reproduce(self):
        system, _ = cs.build_contrast_prompt(_CONTRASTS, pipeline_key="nf-core/chipseq", assay="ChIP-seq")
        assert "reproduce" in system.lower()


class TestParsing:
    def test_it_reads_the_index(self):
        out = cs.parse_contrast_selection(_response(2), n=3)
        assert out["contrast_index"] == 2
        assert out["confidence"] == 0.95
        assert out["reason"]

    def test_an_index_outside_the_list_is_refused(self):
        """An out-of-range pick would silently select nothing, or worse, wrap around."""
        assert cs.parse_contrast_selection(_response(9), n=3)["contrast_index"] is None
        assert cs.parse_contrast_selection(_response(-1), n=3)["contrast_index"] is None

    def test_an_explicit_none_is_a_real_answer(self):
        """A paper whose findings none of this pipeline can reproduce must be able to say so."""
        out = cs.parse_contrast_selection(_response(None, reason="no contrast was measured on ChIP"), n=3)
        assert out["contrast_index"] is None
        assert "ChIP" in out["reason"]

    def test_junk_selects_nothing(self):
        assert cs.parse_contrast_selection("no json", n=3)["contrast_index"] is None


class TestTheCall:
    @pytest.mark.asyncio
    async def test_it_returns_the_selection(self):
        class _C:
            async def submit(self, prompt, payload, model, api_key, attachments=None):
                return _response(2)

        out = await cs.select_contrast(
            _CONTRASTS, pipeline_key="nf-core/chipseq", assay="ChIP-seq", client=_C(), model="m", api_key=None
        )
        assert out["contrast_index"] == 2
        assert out["model"] == "m"
        assert out["decided_by"] == "model"

    @pytest.mark.asyncio
    async def test_a_single_contrast_needs_no_call(self):
        """The common case. One contrast is the answer whatever the model would say."""

        class _Boom:
            async def submit(self, **kwargs):
                raise AssertionError("must not ask which of one contrast to use")

        out = await cs.select_contrast(
            [_CONTRASTS[0]], pipeline_key="nf-core/rnaseq", assay="RNA-seq", client=_Boom(), model="m", api_key=None
        )
        assert out["contrast_index"] == 0
        assert out["decided_by"] == "only_contrast"

    @pytest.mark.asyncio
    async def test_no_contrasts_is_not_a_question(self):
        class _Boom:
            async def submit(self, **kwargs):
                raise AssertionError("must not ask about a QC-only paper")

        assert (
            await cs.select_contrast(
                [], pipeline_key="nf-core/chipseq", assay="ChIP", client=_Boom(), model="m", api_key=None
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_a_provider_failure_selects_nothing_rather_than_guessing(self):
        """Falling back to contrasts[0] is exactly the defect this replaces."""

        class _C:
            async def submit(self, **kwargs):
                raise RuntimeError("provider down")

        assert (
            await cs.select_contrast(
                _CONTRASTS, pipeline_key="nf-core/chipseq", assay="ChIP", client=_C(), model="m", api_key=None
            )
            is None
        )


# ---- the plan records which contrast this run reproduces ----

import pytest_asyncio  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.models.organization import Organization  # noqa: E402
from app.services import validation_extraction_service as ext  # noqa: E402
from app.services.validation_study_service import ValidationStudyService  # noqa: E402

_MULTI = """```json
{"accessions": ["GSE1"],
 "method": {"assay": "ChIP-seq", "tools": ["MACS2"], "reference_build": "GRCm38"},
 "differential_design": {"contrasts": [
   {"name": "KO vs control (RNA-seq)", "assay": "RNA-seq", "thresholds": {"log2fc": 1.0, "padj": 0.05}},
   {"name": "NKX2.2 differential binding (ChIP-seq)", "assay": "ChIP-seq", "thresholds": {"log2fc": null, "padj": 0.05}}],
  "thresholds": {"log2fc": 1.0, "padj": 0.05}},
 "claims": [], "data_availability": "deposited", "blockers": []}
```"""


@pytest_asyncio.fixture
async def _autonomous(session, admin_user):
    org = (
        await session.execute(select(Organization).where(Organization.id == admin_user.organization_id))
    ).scalar_one()
    org.lit_validation_autonomy = "autonomous"
    await session.flush()


@pytest.mark.asyncio
async def test_the_plan_records_the_contrast_this_run_reproduces(session, admin_user, monkeypatch, _autonomous):
    """The whole point: a chipseq run gets the ChIP-seq contrast, not whichever was listed first."""
    from tests.test_validation_extraction import _patch_llm

    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _MULTI)

    async def fake_select(contrasts, *, pipeline_key, assay, client, model, api_key, **kwargs):
        return {
            "contrast_index": 1,
            "reason": "the only ChIP-seq contrast",
            "confidence": 0.95,
            "decided_by": "model",
            "model": model,
        }

    monkeypatch.setattr(ext, "select_contrast", fake_select)
    plan = await ext.ValidationExtractionService.extract(
        session, study, "TEXT", admin_user.organization_id, admin_user.id
    )
    await session.commit()

    design = plan.differential_design_json
    assert design["selected_contrast"]["contrast_index"] == 1
    assert design["selected_contrast"]["decided_by"] == "model"
    assert design["selected_contrast"]["reason"]
    # And the selected contrast's own thresholds are the ones that apply, not the paper-level pair.
    assert design["contrasts"][1]["thresholds"] == {"log2fc": None, "padj": 0.05}


@pytest.mark.asyncio
async def test_a_run_matching_no_contrast_records_that_too(session, admin_user, monkeypatch, _autonomous):
    """Level-2 only, said out loud, rather than reproducing a contrast this run cannot measure."""
    from tests.test_validation_extraction import _patch_llm

    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _MULTI)

    async def fake_select(contrasts, *, pipeline_key, assay, client, model, api_key, **kwargs):
        return {
            "contrast_index": None,
            "reason": "no contrast was measured on this assay",
            "confidence": 0.9,
            "decided_by": "model",
            "model": model,
        }

    monkeypatch.setattr(ext, "select_contrast", fake_select)
    plan = await ext.ValidationExtractionService.extract(
        session, study, "TEXT", admin_user.organization_id, admin_user.id
    )
    await session.commit()

    sel = plan.differential_design_json["selected_contrast"]
    assert sel["contrast_index"] is None
    assert "no contrast" in sel["reason"]


def test_a_contrasts_assay_is_persisted():
    """The prompt asks for it and the selection needs it. It was being dropped on the floor, so every
    contrast reached the selector as `assay: undefined` and it had only names to go on."""
    design = ext._normalize_differential_design(
        {"contrasts": [{"name": "binding", "assay": "ChIP-seq"}, {"name": "expression"}]}
    )
    assert design["contrasts"][0]["assay"] == "ChIP-seq"
    assert design["contrasts"][1]["assay"] is None


class TestDistinguishingTwoContrastsOnOneAssay:
    """The failure this run surfaced. GSE273743's paper reports TWO ChIP-seq differential-binding
    contrasts: NKX2.2 alpha vs beta, and NKX2.2 under Klf4 knockdown. The pipeline key and the
    paper's assay word are identical for both, so the selector had nothing to choose on and picked
    the wrong one at 0.7 confidence. What distinguishes them is the DATA this run is scoped to."""

    def test_the_prompt_carries_the_scoped_accession_and_its_samples(self):
        system, payload = cs.build_contrast_prompt(
            _CONTRASTS,
            pipeline_key="nf-core/chipseq",
            assay="ChIP-seq",
            accession="GSE273743",
            sample_titles=["alphaTC, scramble, NKX2.2 ChIP, 1", "alphaTC, Klf4-KD, NKX2.2 ChIP, 1"],
        )
        assert "GSE273743" in payload
        assert "Klf4-KD" in payload
        assert "sample" in system.lower()

    def test_it_still_works_with_no_sample_information(self):
        _, payload = cs.build_contrast_prompt(
            _CONTRASTS, pipeline_key="nf-core/chipseq", assay="ChIP-seq", accession=None, sample_titles=[]
        )
        assert "nf-core/chipseq" in payload

    @pytest.mark.asyncio
    async def test_the_samples_reach_the_model(self):
        seen = {}

        class _C:
            async def submit(self, prompt, payload, model, api_key, attachments=None):
                seen["payload"] = payload
                return _response(2)

        await cs.select_contrast(
            _CONTRASTS,
            pipeline_key="nf-core/chipseq",
            assay="ChIP-seq",
            client=_C(),
            model="m",
            api_key=None,
            accession="GSE273743",
            sample_titles=["alphaTC, Klf4-KD, NKX2.2 ChIP, 1"],
        )
        assert "Klf4-KD" in seen["payload"]
