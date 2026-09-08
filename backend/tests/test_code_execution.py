"""plan_7 step 17: what running the authors' code produced, and what it says about the paper.

**The failure modes ARE the product.** A paper that fails to install and a paper that runs and
diverges are completely different findings, and today's vocabulary reports both as `inconclusive`.

**Observations and causes are stored separately, and this is the load-bearing decision.** An earlier
draft mapped each outcome straight onto a cause: `ran_output_diverges` -> "the code's logic is
wrong", `data_mismatch` -> "there is a problem with the samples". Both inferences are unsound, and
the second contradicts this plan's own founding evidence: study 26 diverged nearly two-fold and its
own reasoning concluded that a peak-caller difference ON OUR SIDE plausibly explained the gap, so
"the paper cannot be indicted". The mapping table would have overridden that.

So: the observation is deterministic and carries no cause. The assessment is model-made, kept in its
own key, and its candidate set ALWAYS includes bioAF's own input selection, column mapping, chosen
arguments and execution environment.

**"We ran their code and got a different number, and we do not know why" is a publishable, honest
result.** "Their logic is wrong" from the same evidence is not.
"""

import json

import pytest

from app.services.code_execution_service import (
    CODE_ERROR,
    CODE_INCOMPLETE,
    DEPENDENCY_UNRESOLVABLE,
    OUTCOMES,
    QUALIFIER_GENERATED_FROM_PROSE,
    QUALIFIER_METHODS_INADEQUATE,
    RAN_NO_OUTPUT,
    RAN_OUTPUT_UNCOMPARABLE,
    adapt_outputs,
    build_observation,
    choose_entry_point,
    classify_transcript,
)


class _Client:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.payloads: list[str] = []

    async def submit(self, prompt, payload, model, api_key, attachments=None):
        self.payloads.append(payload)
        if self.error:
            raise self.error
        return self.response


def _fenced(obj) -> str:
    return "```json\n" + json.dumps(obj) + "\n```"


# ---- the vocabulary ----


class TestTheVocabulary:
    def test_every_outcome_a_run_can_reach_is_named(self):
        """A verdict that names the defect instead of saying `inconclusive` is the whole product."""
        assert set(OUTCOMES) == {
            "code_absent",
            "code_unreachable",
            "dependency_unresolvable",
            "code_incomplete",
            "code_error",
            "data_mismatch",
            "generation_failed",
            "ran_no_output",
            "ran_output_uncomparable",
            "ran_output_diverges",
            "ran_output_agrees",
        }

    def test_methods_inadequate_is_a_qualifier_and_not_an_outcome(self):
        """A thin methods section no longer stops step 18 from generating and running, so the two
        facts are independent: an analysis generated from thin prose can execute and agree, and
        that is `ran_output_agrees` CARRYING `methods_inadequate`, not one or the other."""
        assert QUALIFIER_METHODS_INADEQUATE not in OUTCOMES

    def test_authors_misinterpreted_is_not_in_the_vocabulary(self):
        """The tool does not issue that verdict. Divergence is always "we could not reproduce", and
        the possibility that the paper read noise as signal is carried BESIDE the outcome."""
        assert "authors_misinterpreted" not in OUTCOMES


class TestReadingATranscript:
    """Deterministic, from the execution itself. No cause is inferred here."""

    def test_dependencies_that_will_not_install_are_named_as_such(self):
        transcript = "ERROR: Could not find a version that satisfies the requirement DESeq2==1.2.3\n"
        assert classify_transcript(transcript, exit_code=1) == DEPENDENCY_UNRESOLVABLE

    def test_an_import_of_something_never_published_is_code_incomplete(self):
        transcript = "Error in library(labUtils) : there is no package called 'labUtils'\n"
        assert classify_transcript(transcript, exit_code=1) == CODE_INCOMPLETE

    def test_a_missing_input_file_the_repo_expects_is_code_incomplete(self):
        transcript = "Error: cannot open file 'data/internal_annotations.rds': No such file or directory\n"
        assert classify_transcript(transcript, exit_code=1) == CODE_INCOMPLETE

    def test_code_that_installs_and_then_fails_on_its_own_is_code_error(self):
        transcript = "Error in dds$condition : object of type 'closure' is not subsettable\n"
        assert classify_transcript(transcript, exit_code=1) == CODE_ERROR

    def test_a_clean_exit_is_not_an_error_at_all(self):
        assert classify_transcript("Session complete.\n", exit_code=0) is None

    def test_a_notebook_that_raised_but_exited_cleanly_is_still_an_error(self):
        """Study 13's lesson. A headless notebook that raised exits its pod cleanly and reports
        `completed`, so the transcript is the only evidence there is."""
        assert classify_transcript("Traceback (most recent call last):\nValueError: bad\n", exit_code=0) == CODE_ERROR

    def test_an_unrecognised_failure_is_code_error_rather_than_a_guess(self):
        """Naming a cause we cannot see would be the defect this module exists to avoid."""
        assert classify_transcript("it stopped\n", exit_code=137) == CODE_ERROR


class TestTheObservation:
    """What happened, with evidence. No cause in this record."""

    def test_it_carries_the_outcome_the_exit_status_and_the_transcript(self):
        obs = build_observation(
            outcome=CODE_ERROR, exit_code=1, transcript_uri="gs://x/log.txt", transcript_tail="Error in dds"
        )
        assert obs["outcome"] == CODE_ERROR
        assert obs["exit_code"] == 1
        assert obs["transcript_uri"] == "gs://x/log.txt"
        assert obs["transcript_tail"]

    def test_it_never_carries_a_cause(self):
        obs = build_observation(outcome=CODE_ERROR, exit_code=1, transcript_uri=None, transcript_tail="")
        assert "cause" not in obs
        assert "explanation" not in obs

    def test_a_divergence_records_both_numbers(self):
        """Always report our numbers alongside the paper's, so the user makes their own judgement."""
        obs = build_observation(
            outcome="ran_output_diverges",
            exit_code=0,
            transcript_uri=None,
            transcript_tail="",
            paper_value=7389,
            our_value=4054,
            metric="peak_count",
        )
        assert obs["paper_value"] == 7389
        assert obs["our_value"] == 4054
        assert obs["metric"] == "peak_count"


# ---- the entry point ----


class TestTheEntryPoint:
    _FILES = [
        {"path": "README.md", "size_bytes": 100},
        {"path": "scripts/run_deseq2.R", "size_bytes": 4000},
        {"path": "scripts/helpers.R", "size_bytes": 900},
    ]

    @pytest.mark.asyncio
    async def test_the_model_states_a_choice_a_reason_and_a_confidence(self):
        client = _Client(
            _fenced(
                {
                    "entry_point": "scripts/run_deseq2.R",
                    "arguments": "--counts /data/matrix.tsv",
                    "reason": "the only script that runs a differential test",
                    "confidence": 0.8,
                }
            )
        )
        decision = await choose_entry_point(
            files=self._FILES, readme="# run scripts/run_deseq2.R", client=client, model="m", api_key=None
        )
        assert decision["entry_point"] == "scripts/run_deseq2.R"
        assert decision["reason"]
        assert decision["confidence"] == 0.8
        assert decision["decided_by"] == "model"

    @pytest.mark.asyncio
    async def test_a_file_the_repo_does_not_have_is_refused_before_anything_runs(self):
        """Deterministic code checks the file exists. A model naming a script that is not there
        would launch a pod that fails for a reason nobody could see."""
        client = _Client(_fenced({"entry_point": "run_everything.sh", "reason": "x", "confidence": 0.9}))
        decision = await choose_entry_point(files=self._FILES, readme="", client=client, model="m", api_key=None)
        assert decision["entry_point"] is None
        assert decision["reason"]

    @pytest.mark.asyncio
    async def test_only_the_listing_and_the_readme_are_sent(self):
        """The repository's CONTENTS are not the question. What is being asked is which file starts
        the analysis, which the names and the README answer."""
        client = _Client(_fenced({"entry_point": "scripts/run_deseq2.R", "reason": "x", "confidence": 0.5}))
        await choose_entry_point(files=self._FILES, readme="# the readme", client=client, model="m", api_key=None)
        payload = client.payloads[0]
        assert "scripts/run_deseq2.R" in payload
        assert "# the readme" in payload

    @pytest.mark.asyncio
    async def test_a_provider_failure_leaves_no_entry_point_rather_than_a_guess(self):
        from app.services.llm_provider_clients import ProviderError

        client = _Client(error=ProviderError("down", error_class="server"))
        decision = await choose_entry_point(files=self._FILES, readme="", client=client, model="m", api_key=None)
        assert decision["entry_point"] is None


# ---- the output adapters ----


class TestTheOutputAdapters:
    """Three supported shapes, tried in order. An earlier draft required every execution to
    normalize to id / lfc / padj and called anything else `ran_no_output`, which would be a false
    statement about a paper whose script computed "we identified 1,412 peaks"."""

    def test_a_differential_table_lands_where_the_concordance_service_already_reads(self):
        adapted = adapt_outputs(
            outputs=[{"path": "results/de.csv", "text": "gene,log2FoldChange,padj\nA,2.0,0.01\n"}],
            claims=[],
        )
        assert adapted["kind"] == "differential_table"
        assert adapted["outcome"] is None  # the concordance service decides agree vs diverge
        assert adapted["table_text"].startswith("gene,")

    def test_a_numeric_claim_is_matched_to_a_claim_the_extractor_captured(self):
        adapted = adapt_outputs(
            outputs=[{"path": "results/summary.json", "text": json.dumps({"peak_count": 1412})}],
            claims=[{"metric_key": "peak_count", "claimed_value": 1400.0, "unit": "peaks"}],
        )
        assert adapted["kind"] == "numeric_claim"
        assert adapted["computed_metrics"] == {"peak_count": 1412}

    def test_an_unmatched_number_is_retained_and_reported_rather_than_scored(self):
        """An unmatched output can support neither agreement nor divergence, and inventing the match
        would be the same defect as scoring a claim against a metric bioAF does not compute."""
        adapted = adapt_outputs(
            outputs=[{"path": "results/summary.json", "text": json.dumps({"cells_classified_pct": 37})}],
            claims=[{"metric_key": "peak_count", "claimed_value": 1400.0, "unit": "peaks"}],
        )
        assert adapted["outcome"] == RAN_OUTPUT_UNCOMPARABLE
        assert adapted["unmatched"]
        assert "cells_classified_pct" in json.dumps(adapted["unmatched"])

    def test_output_we_cannot_compare_is_a_limitation_of_ours_stated_as_such(self):
        adapted = adapt_outputs(
            outputs=[{"path": "results/figure3.pdf", "text": ""}],
            claims=[],
        )
        assert adapted["outcome"] == RAN_OUTPUT_UNCOMPARABLE
        assert "bioAF" in adapted["reason"]

    def test_writing_nothing_at_all_is_a_different_outcome_from_writing_something_uncomparable(self):
        assert adapt_outputs(outputs=[], claims=[])["outcome"] == RAN_NO_OUTPUT

    def test_a_run_with_one_comparable_claim_and_one_unsupported_reports_both(self):
        """A reader must be able to see the boundary of what was tested rather than infer a verdict
        from silence."""
        adapted = adapt_outputs(
            outputs=[
                {"path": "results/summary.json", "text": json.dumps({"peak_count": 1412, "figure_ratio": 0.4})},
            ],
            claims=[{"metric_key": "peak_count", "claimed_value": 1400.0, "unit": "peaks"}],
        )
        assert adapted["computed_metrics"] == {"peak_count": 1412}
        assert "figure_ratio" in json.dumps(adapted["unmatched"])

    def test_the_differential_table_wins_when_a_run_writes_both(self):
        """Ordered, because a differential table is the strongest comparison available and a
        summary number beside it is a weaker restatement."""
        adapted = adapt_outputs(
            outputs=[
                {"path": "results/summary.json", "text": json.dumps({"peak_count": 1412})},
                {"path": "results/de.csv", "text": "gene,log2FoldChange,padj\nA,2.0,0.01\n"},
            ],
            claims=[{"metric_key": "peak_count", "claimed_value": 1400.0, "unit": "peaks"}],
        )
        assert adapted["kind"] == "differential_table"


class TestQualifiers:
    def test_a_generated_run_carries_its_own_qualifier(self):
        assert QUALIFIER_GENERATED_FROM_PROSE == "generated_from_prose"

    def test_a_qualifier_does_not_change_the_outcome(self):
        """`ran_output_agrees` + `methods_inadequate` reads "the analysis we generated from the
        paper's description reproduces its result, but the description was thin enough that our
        interpretation of it may not be the authors'." A weaker claim, same outcome."""
        obs = build_observation(
            outcome="ran_output_agrees",
            exit_code=0,
            transcript_uri=None,
            transcript_tail="",
            qualifiers=[QUALIFIER_METHODS_INADEQUATE, QUALIFIER_GENERATED_FROM_PROSE],
        )
        assert obs["outcome"] == "ran_output_agrees"
        assert set(obs["qualifiers"]) == {QUALIFIER_METHODS_INADEQUATE, QUALIFIER_GENERATED_FROM_PROSE}


# ---- the second entry point on the execution service ----


class TestExecuteFetchedCode:
    """A SECOND ENTRY POINT on `NotebookExecutionService`, not a parallel service. A fork would
    double the surface that image, storage and service-account wiring must be kept correct in; the
    spec assembly, the adapter, `poll_execution` and `_finalize_success` are all generic and are
    reused wholesale."""

    @pytest.fixture
    def _adapter(self, monkeypatch):
        from types import SimpleNamespace

        from app.adapters.models import ServiceState
        from app.services import notebook_execution_service as nes

        launched: list[dict] = []

        class _Adapter:
            async def launch_session(self, spec):
                launched.append(spec)
                return SimpleNamespace(
                    status=ServiceState.RUNNING,
                    provider_details={"pod_name": "bioaf-notebook-1", "namespace": spec.get("namespace")},
                )

        monkeypatch.setattr(nes, "get_notebook_adapter", lambda: _Adapter())
        return launched

    async def _configure_identity(self, session):
        from app.platform.platform_config_service import PlatformConfigService

        await PlatformConfigService.set(session, "untrusted_bucket_name", "bioaf-untrusted-lab-abc")
        await PlatformConfigService.set(session, "untrusted_runner_sa_email", "bioaf-untrusted-runner@p.iam.g.com")

    @pytest.mark.asyncio
    async def test_it_refuses_when_this_install_has_no_isolated_identity(self, session, admin_user, _adapter):
        """No fallback. Running a stranger's code as the notebook runner is the exposure step 16a
        exists to end, so an install without the identity says so in a plain sentence."""
        from app.exceptions import ValidationError
        from app.services.notebook_execution_service import NotebookExecutionService

        with pytest.raises(ValidationError) as e:
            await NotebookExecutionService.execute_fetched_code(
                session,
                org_id=admin_user.organization_id,
                user_id=admin_user.id,
                code_uri="gs://bioaf-untrusted-lab-abc/study-1/code.tar.gz",
                entry_point="analysis.R",
                arguments="",
                input_file_ids=[],
            )
        assert "isolated identity" in str(e.value)
        assert _adapter == []

    @pytest.mark.asyncio
    async def test_it_runs_in_the_untrusted_namespace_as_the_untrusted_identity(self, session, admin_user, _adapter):
        from app.services.notebook_execution_service import NotebookExecutionService

        await self._configure_identity(session)
        await NotebookExecutionService.execute_fetched_code(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            code_uri="gs://bioaf-untrusted-lab-abc/study-1/code.tar.gz",
            entry_point="analysis.R",
            arguments="--counts /data/matrix.tsv",
            input_file_ids=[],
        )

        spec = _adapter[0]
        assert spec["namespace"] == "bioaf-untrusted"
        assert spec["notebook_runner_sa_email"] == "bioaf-untrusted-runner@p.iam.g.com"
        assert spec["working_bucket"] == "bioaf-untrusted-lab-abc"

    @pytest.mark.asyncio
    async def test_the_entry_point_and_its_arguments_reach_the_pod(self, session, admin_user, _adapter):
        from app.services.notebook_execution_service import NotebookExecutionService

        await self._configure_identity(session)
        await NotebookExecutionService.execute_fetched_code(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            code_uri="gs://bioaf-untrusted-lab-abc/study-1/code.tar.gz",
            entry_point="scripts/run.R",
            arguments="--counts /data/matrix.tsv",
            input_file_ids=[],
        )
        spec = _adapter[0]
        assert spec["fetched_code"]["entry_point"] == "scripts/run.R"
        assert spec["fetched_code"]["arguments"] == "--counts /data/matrix.tsv"
        assert spec["fetched_code"]["code_uri"].endswith("code.tar.gz")

    @pytest.mark.asyncio
    async def test_the_run_is_capped_so_a_hang_becomes_an_outcome(self, session, admin_user, _adapter):
        """A hang has to become `code_error` with a reason rather than a study that ticks forever."""
        from app.services.notebook_execution_service import NotebookExecutionService

        await self._configure_identity(session)
        await NotebookExecutionService.execute_fetched_code(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            code_uri="gs://x/code.tar.gz",
            entry_point="a.R",
            arguments="",
            input_file_ids=[],
        )
        assert _adapter[0]["timeout_seconds"] > 0

    @pytest.mark.asyncio
    async def test_the_builtin_gate_is_not_touched_by_this_path(self, session, admin_user, _adapter):
        """The boundary is preserved, not deleted: `execute_template` still refuses a non-builtin
        template, and this path never consults a template at all."""
        from app.services.notebook_execution_service import NotebookExecutionService

        await self._configure_identity(session)
        cs = await NotebookExecutionService.execute_fetched_code(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            code_uri="gs://x/code.tar.gz",
            entry_point="a.R",
            arguments="",
            input_file_ids=[],
        )
        assert cs.id is not None
        assert "template_id" not in _adapter[0]


class TestTheClassifierFoldsItIn:
    """`ran_output_uncomparable` needs a classifier answer of its own. It is not `not_computed` (we
    computed something) and it is not divergence (we did not compare): it is a completed execution
    whose comparison bioAF does not support, and the verdict has to say that rather than absorb it
    into `inconclusive`."""

    def test_an_uncomparable_execution_is_named_in_the_reasoning(self):
        from app.services.validation_classifier_service import classify_study

        result = classify_study([], {}, code_outcome="ran_output_uncomparable", reproduction_method="authors_code")
        assert "bioAF" in result["reasoning"]
        assert result["code_outcome"] == "ran_output_uncomparable"

    def test_a_code_arm_that_never_executed_is_named_as_such(self):
        """`dependency_unresolvable` on a five-year-old repo is a true and useful statement about
        that paper, and today the feature reports it as `inconclusive` and says nothing."""
        from app.services.validation_classifier_service import classify_study

        result = classify_study([], {}, code_outcome="dependency_unresolvable", reproduction_method="authors_code")
        assert "dependencies" in result["reasoning"].lower()

    def test_a_qualifier_weakens_what_the_verdict_claims_without_changing_it(self):
        from app.services.validation_classifier_service import classify_study

        result = classify_study(
            [],
            {},
            code_outcome="ran_output_agrees",
            code_qualifiers=["methods_inadequate", "generated_from_prose"],
            reproduction_method="llm_from_methods",
        )
        assert "generated" in result["reasoning"].lower()
        assert result["code_qualifiers"] == ["methods_inadequate", "generated_from_prose"]

    def test_a_study_with_no_code_arm_reads_exactly_as_it_did_before(self):
        from app.services.validation_classifier_service import classify_study

        before = classify_study([], {})
        assert before["code_outcome"] is None
        assert "code" not in before["reasoning"].lower() or before["classification"]
