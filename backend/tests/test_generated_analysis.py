"""plan_7 step 18: reproduce from the described methods, when no code was published.

The third reproduction method, and it is not like the other two. `deseq2` / `limma_trend` test the
finding using OUR analysis; `authors_code` tests it using THEIRS; this generates an analysis from the
paper's own prose and is the weakest of the three, ranking last under step 9's qualifier.

**When it runs**: rung 3 of step 17's method ladder, meaning no usable published code was AVAILABLE.
It does not run behind a published-code execution that was attempted and failed; that failure is the
result of its own arm.

**An inadequate-methods judgment does not stop it.** An earlier draft read step 14's sufficiency
check as a veto ("if step 14 said no, say so and stop"), which contradicted step 14's own statement
that the three sufficiency checks are advisory. Thin methods are a FINDING to carry, not a veto.

**It runs ONCE.** An earlier draft ran it repeatedly and reported the agreement between runs. Each
run is a fresh generation, so disagreement between runs measures the GENERATOR rather than the
paper, and the compute is spent either way. The nondeterminism is handled by what the report claims.
"""

import json

import pytest
import pytest_asyncio

from app.services.generated_analysis import (
    GENERATION_INTENT,
    generate_analysis,
)


class _Client:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.calls = 0
        self.prompts: list[str] = []
        self.payloads: list[str] = []

    async def submit(self, prompt, payload, model, api_key, attachments=None):
        self.calls += 1
        self.prompts.append(prompt)
        self.payloads.append(payload)
        if self.error:
            raise self.error
        return self.response


def _fenced(obj) -> str:
    return "```json\n" + json.dumps(obj) + "\n```"


_GENERATED = _fenced(
    {
        "language": "R",
        "source": "library(DESeq2)\ndds <- DESeqDataSetFromMatrix(counts, coldata, ~condition)\n",
        "entry_point": "generated_analysis.R",
        "assumptions": [
            "the paper does not state the FDR threshold, so 0.05 was assumed",
            "the paper does not name a normalization, so DESeq2's median-of-ratios was used",
        ],
        "reason": "the methods name DESeq2 and a two-arm design",
        "confidence": 0.6,
    }
)


async def _generate(**kw):
    return await generate_analysis(
        methods_text=kw.pop("methods_text", "We used DESeq2 to compare knockdown against control."),
        matrix_description=kw.pop("matrix_description", "a 37,248 x 6 counts matrix at /data/matrix.tsv"),
        design=kw.pop("design", {"test_samples": ["KD_1"], "reference_samples": ["CTRL_1"]}),
        methods_inadequate=kw.pop("methods_inadequate", False),
        client=kw.pop("client", _Client(_GENERATED)),
        model=kw.pop("model", "claude-opus-4-8"),
        api_key=None,
        **kw,
    )


class TestWhatItProduces:
    @pytest.mark.asyncio
    async def test_it_returns_runnable_source_and_an_entry_point(self):
        result = await _generate()
        assert result["source"].startswith("library(DESeq2)")
        assert result["entry_point"] == "generated_analysis.R"
        assert result["language"] == "R"

    @pytest.mark.asyncio
    async def test_it_records_every_assumption_the_generation_had_to_make(self):
        """What makes an inadequate-methods run readable rather than merely suspect: "the paper does
        not state the FDR threshold, so 0.05 was assumed" is exactly what a disputing reader needs."""
        result = await _generate()
        assert len(result["assumptions"]) == 2
        assert any("FDR" in a for a in result["assumptions"])

    @pytest.mark.asyncio
    async def test_it_names_the_model_that_generated_it(self):
        """A reader disputing the result has to be able to read exactly what ran, and know who
        wrote it."""
        result = await _generate()
        assert result["model"] == "claude-opus-4-8"
        assert result["method"] == "llm_from_methods"

    @pytest.mark.asyncio
    async def test_it_carries_the_generated_from_prose_qualifier(self):
        result = await _generate()
        assert "generated_from_prose" in result["qualifiers"]

    @pytest.mark.asyncio
    async def test_recorded_assumptions_earn_their_own_qualifier(self):
        result = await _generate()
        assert "assumptions_recorded" in result["qualifiers"]


class TestThinMethodsAreCarriedNotVetoed:
    @pytest.mark.asyncio
    async def test_an_inadequate_judgment_still_generates(self):
        """Advisory means advisory. Step 14's sufficiency check is a finding, and step 18 exists to
        attempt the paper anyway."""
        client = _Client(_GENERATED)
        result = await _generate(methods_inadequate=True, client=client)
        assert result["source"]
        assert client.calls == 1

    @pytest.mark.asyncio
    async def test_the_finding_travels_with_whatever_comes_out(self):
        """A successful generated execution does not erase the inadequate-methods finding. Both are
        reported: the execution result, and the evidential limitation on it."""
        result = await _generate(methods_inadequate=True)
        assert "methods_inadequate" in result["qualifiers"]

    @pytest.mark.asyncio
    async def test_the_model_is_told_the_description_was_assessed_as_thin(self):
        client = _Client(_GENERATED)
        await _generate(methods_inadequate=True, client=client)
        assert "thin" in client.payloads[0].lower() or "inadequate" in client.payloads[0].lower()


class TestItRunsOnce:
    @pytest.mark.asyncio
    async def test_one_generation_and_no_more(self):
        """Repeating measures the generator, not the paper, and the compute is spent either way.
        This decision was owner-approved and must not be reintroduced."""
        client = _Client(_GENERATED)
        await _generate(client=client)
        assert client.calls == 1

    @pytest.mark.asyncio
    async def test_a_failed_generation_is_a_terminal_outcome_not_a_retry(self):
        """Two terminal shapes, and neither waits: it either produces an executable analysis or
        finishes with that named limitation."""
        from app.services.llm_provider_clients import ProviderError

        client = _Client(error=ProviderError("down", error_class="server"))
        result = await _generate(client=client)
        assert result["outcome"] == "generation_failed"
        assert result["reason"]
        assert client.calls == 1

    @pytest.mark.asyncio
    async def test_a_model_that_writes_no_source_is_generation_failed(self):
        client = _Client(_fenced({"language": "R", "source": "", "reason": "the methods say nothing usable"}))
        result = await _generate(client=client)
        assert result["outcome"] == "generation_failed"
        assert result["reason"]


class TestTheFactualChecksStillApply:
    @pytest.mark.asyncio
    async def test_it_is_given_the_real_matrix_and_the_real_design(self):
        """This authorises generating from thin prose. It does not authorise inventing sample data:
        the analysis is written against the matrix that was actually acquired."""
        client = _Client(_GENERATED)
        await _generate(client=client)
        payload = client.payloads[0]
        assert "/data/matrix.tsv" in payload
        assert "KD_1" in payload
        assert "CTRL_1" in payload

    @pytest.mark.asyncio
    async def test_the_prompt_forbids_inventing_data(self):
        client = _Client(_GENERATED)
        await _generate(client=client)
        assert "invent" in client.prompts[0].lower() or "do not fabricate" in client.prompts[0].lower()

    def test_the_intent_reads_in_the_users_language(self):
        assert "generat" in GENERATION_INTENT.lower()
        assert "_" not in GENERATION_INTENT


class TestTheDriverWiresRungThree:
    """Rung 3 of step 17's method ladder. Without this wire the generated arm is a service nothing
    calls, which is exactly the shape of the step 11 blocker.

    The studies here carry `level3_skipped` rather than `level3`: the generated arm is what gives a
    study a finding-tier result when bioAF's own wiring cannot produce one. See
    `TestTheGeneratedArmDoesNotDisplaceBioafsOwnTemplate` for why that is where rungs 3 and 4 divide.
    """

    # Study 26's shape: a confirmed finding, and no route from bioAF's own wiring to it. That study
    # produced nothing at all, which is what the generated arm exists to change.
    _UNWIRED = {"reason": "no published file matched the interval route", "reason_code": "no_input_file"}

    _LEVEL3 = {
        "template_id": 1,
        "kind": "gene",
        "method": "deseq2",
        "input_file_ids": [],
        "parameters": {"counts_path": "/data/matrix.tsv"},
        "paper_finding_set": {"kind": "gene", "namespace": "symbol", "entities": [], "n_sig": 0},
    }

    @staticmethod
    async def _study(session, admin_user, evidence):
        from app.services.validation_study_service import ValidationStudyService

        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        study.state = "reproducing"
        study.evidence_json = evidence
        await session.flush()
        return study

    @staticmethod
    def _patch(monkeypatch, response):
        from types import SimpleNamespace

        from app.services import validation_driver_service as drv

        async def _cfg(sess, org_id, feature):
            return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

        class _C:
            async def submit(self, prompt, payload, model, api_key, attachments=None):
                return response

        monkeypatch.setattr(drv.llm_provider_config_service, "get_for_feature", _cfg)
        monkeypatch.setattr(drv, "get_client", lambda p: _C())

    @pytest.fixture
    def launches(self, monkeypatch):
        from types import SimpleNamespace

        from app.services.notebook_execution_service import NotebookExecutionService

        calls: list[dict] = []

        async def _template(session, **kw):
            calls.append({"method": "template", **kw})
            return SimpleNamespace(id=900, status="running")

        async def _fetched(session, **kw):
            calls.append({"method": "fetched_code", **kw})
            return SimpleNamespace(id=901, status="running")

        monkeypatch.setattr(NotebookExecutionService, "execute_template", _template)
        monkeypatch.setattr(NotebookExecutionService, "execute_fetched_code", _fetched)
        return calls

    @pytest_asyncio.fixture
    async def _untrusted(self, session):
        from app.platform.platform_config_service import PlatformConfigService

        await PlatformConfigService.set(session, "untrusted_bucket_name", "bioaf-untrusted-lab-abc")
        await PlatformConfigService.set(session, "untrusted_runner_sa_email", "u@p.iam.g.com")

    @pytest.mark.asyncio
    async def test_a_paper_with_no_code_generates_and_runs_the_analysis(
        self, session, admin_user, monkeypatch, launches, _untrusted
    ):
        from app.services.validation_driver_service import ValidationDriverService

        self._patch(monkeypatch, _GENERATED)
        study = await self._study(
            session,
            admin_user,
            {
                "level3_skipped": TestTheDriverWiresRungThree._UNWIRED,
                "code_resolution": {"outcome": "code_absent", "reason": "none published"},
            },
        )
        await ValidationDriverService._handle_reproducing(session, study)

        assert [c["method"] for c in launches] == ["fetched_code"]
        execution = study.evidence_json["code_execution"]
        assert execution["method"] == "llm_from_methods"
        assert "generated_from_prose" in execution["qualifiers"]

    @pytest.mark.asyncio
    async def test_the_generated_source_and_its_assumptions_are_kept(
        self, session, admin_user, monkeypatch, launches, _untrusted
    ):
        """A reader disputing the result has to be able to read what actually ran."""
        from app.services.validation_driver_service import ValidationDriverService

        self._patch(monkeypatch, _GENERATED)
        study = await self._study(
            session,
            admin_user,
            {"level3_skipped": TestTheDriverWiresRungThree._UNWIRED, "code_resolution": {"outcome": "code_absent"}},
        )
        await ValidationDriverService._handle_reproducing(session, study)

        source = study.evidence_json["code_execution"]["source"]
        assert source["generated_by_model"] == "claude-opus-4-8"
        assert source["assumptions"]
        assert study.evidence_json["generated_analysis"]["source"].startswith("library(DESeq2)")

    @pytest.mark.asyncio
    async def test_it_does_not_run_behind_a_failed_published_code_arm(
        self, session, admin_user, monkeypatch, launches, _untrusted
    ):
        """ "Else" at rung 3 means no usable code was AVAILABLE. It does not mean "the code we ran
        failed"."""
        from app.services.validation_driver_service import ValidationDriverService

        self._patch(monkeypatch, _GENERATED)
        study = await self._study(
            session,
            admin_user,
            {
                "level3": self._LEVEL3,
                "code_resolution": {"outcome": "resolved", "url": "https://github.com/lab/p", "files": []},
                "code_execution": {
                    "attempt": 1,
                    "method": "authors_code",
                    "session_id": 901,
                    "outcome": "dependency_unresolvable",
                    "source": {},
                    "observation": {"outcome": "dependency_unresolvable"},
                },
            },
        )
        await ValidationDriverService._handle_reproducing(session, study)

        assert launches == []
        assert study.evidence_json["code_execution"]["outcome"] == "dependency_unresolvable"

    @pytest.mark.asyncio
    async def test_a_generation_that_fails_reaches_a_terminal_state(
        self, session, admin_user, monkeypatch, launches, _untrusted
    ):
        """Neither terminal shape waits, and there is no loop through fresh generations."""
        from app.services.validation_driver_service import ValidationDriverService

        self._patch(monkeypatch, _fenced({"language": "R", "source": "", "reason": "nothing usable is described"}))
        study = await self._study(
            session,
            admin_user,
            {"level3_skipped": TestTheDriverWiresRungThree._UNWIRED, "code_resolution": {"outcome": "code_absent"}},
        )
        await ValidationDriverService._handle_reproducing(session, study)

        assert study.state == "comparing"
        assert study.evidence_json["code_execution"]["outcome"] == "generation_failed"
        assert launches == []

    @pytest.mark.asyncio
    async def test_an_install_that_cannot_execute_falls_to_the_template(
        self, session, admin_user, monkeypatch, launches
    ):
        """No isolated identity means nothing generated can be run either. The study still gets
        bioAF's own reproduction rather than holding."""
        from app.services.validation_driver_service import ValidationDriverService

        self._patch(monkeypatch, _GENERATED)
        study = await self._study(
            session, admin_user, {"level3": self._LEVEL3, "code_resolution": {"outcome": "code_absent"}}
        )
        await ValidationDriverService._handle_reproducing(session, study)
        assert [c["method"] for c in launches] == ["template"]


class TestTheGeneratedArmDoesNotDisplaceBioafsOwnTemplate:
    """plan_7's ladder puts the generated arm at rung 3 and bioAF's own template at rung 4, and its
    text for both rungs is "no usable published code". Read literally, a provisioned install would
    run a NONDETERMINISTIC generated analysis in preference to a deterministic template on every
    paper that published no code, which would move study 6.

    plan_7 states that outcome as a falsifier: "The pipeline route is byte-for-byte unchanged. Study
    6 must still reach Level 4 the same way. If step 4 or 8 moves it, the plan is wrong." The
    generated arm is also the weakest of the three methods and ranks last under step 9's qualifier,
    so preferring it over a validated template would weaken every such verdict.

    So the boundary between the two rungs is: the generated arm is what gives a study a finding-tier
    result when bioAF's own wiring cannot produce one. That is what "the reason a paper with no
    published code is still worth running" means, and it is the reading that satisfies both
    requirements. **Flagged to the owner rather than settled silently.**
    """

    @staticmethod
    async def _study(session, admin_user, evidence):
        from app.services.validation_study_service import ValidationStudyService

        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        study.state = "reproducing"
        study.evidence_json = evidence
        await session.flush()
        return study

    _LEVEL3 = TestTheDriverWiresRungThree._LEVEL3

    @pytest.mark.asyncio
    async def test_a_study_with_a_wired_template_still_runs_it(self, session, admin_user, monkeypatch):
        from app.platform.platform_config_service import PlatformConfigService
        from app.services.validation_driver_service import ValidationDriverService

        await PlatformConfigService.set(session, "untrusted_bucket_name", "bioaf-untrusted-lab-abc")
        await PlatformConfigService.set(session, "untrusted_runner_sa_email", "u@p.iam.g.com")
        TestTheDriverWiresRungThree._patch(monkeypatch, _GENERATED)

        launches: list[dict] = []
        from types import SimpleNamespace

        from app.services.notebook_execution_service import NotebookExecutionService

        async def _template(session_, **kw):
            launches.append({"method": "template", **kw})
            return SimpleNamespace(id=900, status="running")

        async def _fetched(session_, **kw):
            launches.append({"method": "fetched_code", **kw})
            return SimpleNamespace(id=901, status="running")

        monkeypatch.setattr(NotebookExecutionService, "execute_template", _template)
        monkeypatch.setattr(NotebookExecutionService, "execute_fetched_code", _fetched)

        study = await self._study(
            session, admin_user, {"level3": self._LEVEL3, "code_resolution": {"outcome": "code_absent"}}
        )
        await ValidationDriverService._handle_reproducing(session, study)

        assert [c["method"] for c in launches] == ["template"]

    @pytest.mark.asyncio
    async def test_a_study_bioaf_cannot_wire_gets_the_generated_arm(self, session, admin_user, monkeypatch):
        """Study 26's shape: a confirmed finding, and no route from bioAF's own wiring to it. That
        study produced nothing at all, which is what the generated arm exists to change."""
        from app.platform.platform_config_service import PlatformConfigService
        from app.services.notebook_execution_service import NotebookExecutionService
        from app.services.validation_driver_service import ValidationDriverService

        await PlatformConfigService.set(session, "untrusted_bucket_name", "bioaf-untrusted-lab-abc")
        await PlatformConfigService.set(session, "untrusted_runner_sa_email", "u@p.iam.g.com")
        TestTheDriverWiresRungThree._patch(monkeypatch, _GENERATED)

        launches: list[dict] = []
        from types import SimpleNamespace

        async def _fetched(session_, **kw):
            launches.append({"method": "fetched_code", **kw})
            return SimpleNamespace(id=901, status="running")

        monkeypatch.setattr(NotebookExecutionService, "execute_fetched_code", _fetched)

        study = await self._study(
            session,
            admin_user,
            {
                "level3_skipped": {
                    "reason": "no published file matched the interval route",
                    "reason_code": "no_input_file",
                },
                "code_resolution": {"outcome": "code_absent"},
            },
        )
        await ValidationDriverService._handle_reproducing(session, study)

        assert [c["method"] for c in launches] == ["fetched_code"]
        assert study.evidence_json["code_execution"]["method"] == "llm_from_methods"

    @pytest.mark.asyncio
    async def test_published_code_still_outranks_the_template(self, session, admin_user, monkeypatch):
        """The ladder's rungs 1 and 2 are unaffected: the authors' own code is the STRONGEST method
        and does displace bioAF's template, which is the whole point of step 17."""
        from app.platform.platform_config_service import PlatformConfigService
        from app.services.notebook_execution_service import NotebookExecutionService
        from app.services.validation_driver_service import ValidationDriverService

        await PlatformConfigService.set(session, "untrusted_bucket_name", "bioaf-untrusted-lab-abc")
        await PlatformConfigService.set(session, "untrusted_runner_sa_email", "u@p.iam.g.com")
        TestTheDriverWiresRungThree._patch(monkeypatch, _GENERATED)

        launches: list[dict] = []
        from types import SimpleNamespace

        async def _fetched(session_, **kw):
            launches.append({"method": "fetched_code", **kw})
            return SimpleNamespace(id=901, status="running")

        monkeypatch.setattr(NotebookExecutionService, "execute_fetched_code", _fetched)

        study = await self._study(
            session,
            admin_user,
            {
                "level3": self._LEVEL3,
                "code_resolution": {
                    "outcome": "resolved",
                    "url": "https://github.com/lab/p",
                    "commit_sha": "abc",
                    "files": [{"path": "analysis.R", "size_bytes": 10}],
                },
            },
        )
        await ValidationDriverService._handle_reproducing(session, study)
        assert study.evidence_json["code_execution"]["method"] == "authors_code"
