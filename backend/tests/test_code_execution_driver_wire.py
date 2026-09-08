"""plan_7 step 17: where the method is chosen, launched, polled and landed.

**The part whose absence would repeat the step 11 blocker.** Steps 16, 17 and 18 describe services;
nothing in them says who calls them, in what order, or how a study gets from an execution to a
verdict. This is that wire, and it lives in `_handle_reproducing`, which is single-method today.

The ladder, evaluated once on the first visit:

    1. code artifact from step 16 resolved and pinned  -> "authors_code"
    2. else repository from step 16 resolved and pinned -> "authors_code"
    3. else no usable published code                    -> "llm_from_methods" (step 18)
    4. else                                             -> bioAF's own template

**"Else" at rung 3 means no usable code was AVAILABLE. It does not mean "the code we ran failed."**
Once an arm is attempted, its result IS the result. Silently replacing `dependency_unresolvable`
with a generated run that agrees would turn the single most useful finding this feature can produce
into a false reproduction.

**A code-arm failure does not go through `_degrade_to_level2`.** That helper collapses everything
into one prose string, which destroys the outcome vocabulary, the transcript and the observation
record, and leaves step 19's code section nothing to render but free text.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.services.notebook_execution_service import NotebookExecutionService
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService

_LEVEL3 = {
    "template_id": 1,
    "kind": "gene",
    "method": "deseq2",
    "input_file_ids": [],
    "paper_finding_set": {"kind": "gene", "namespace": "symbol", "entities": [], "n_sig": 0},
}

_RESOLVED_CODE = {
    "outcome": "resolved",
    "kind": "github",
    "url": "https://github.com/lab/paper",
    "commit_sha": "abc1234",
    "files": [{"path": "analysis.R", "size_bytes": 400}, {"path": "README.md", "size_bytes": 40}],
    "reason": "pinned",
}


async def _study_at_reproducing(session, admin_user, evidence):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    study.state = "reproducing"
    study.evidence_json = evidence
    await session.flush()
    return study


@pytest_asyncio.fixture
async def untrusted_ready(session):
    from app.platform.platform_config_service import PlatformConfigService

    await PlatformConfigService.set(session, "untrusted_bucket_name", "bioaf-untrusted-lab-abc")
    await PlatformConfigService.set(session, "untrusted_runner_sa_email", "bioaf-untrusted-runner@p.iam.g.com")


@pytest.fixture
def launches(monkeypatch):
    """Both entry points, recorded, so the ladder's choice is visible."""
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


class TestTheLadder:
    @pytest.mark.asyncio
    async def test_published_code_is_attempted_first(self, session, admin_user, launches, untrusted_ready):
        study = await _study_at_reproducing(session, admin_user, {"level3": _LEVEL3, "code_resolution": _RESOLVED_CODE})
        await ValidationDriverService._handle_reproducing(session, study)

        assert [c["method"] for c in launches] == ["fetched_code"]
        assert study.evidence_json["code_execution"]["method"] == "authors_code"

    @pytest.mark.asyncio
    async def test_the_repo_url_and_commit_are_on_the_record(self, session, admin_user, launches, untrusted_ready):
        """A concordance scored against the authors' OWN code is a stronger claim than one scored
        against ours, and step 9's rule says the verdict has to be able to say so."""
        study = await _study_at_reproducing(session, admin_user, {"level3": _LEVEL3, "code_resolution": _RESOLVED_CODE})
        await ValidationDriverService._handle_reproducing(session, study)

        source = study.evidence_json["code_execution"]["source"]
        assert source["repo_url"] == "https://github.com/lab/paper"
        assert source["commit_sha"] == "abc1234"

    @pytest.mark.asyncio
    async def test_with_no_usable_code_it_falls_to_the_template_arm(self, session, admin_user, launches):
        """Rung 4. Step 18's generated arm is the rung above it and lands in its own test file."""
        study = await _study_at_reproducing(
            session, admin_user, {"level3": _LEVEL3, "code_resolution": {"outcome": "code_absent", "reason": "none"}}
        )
        await ValidationDriverService._handle_reproducing(session, study)
        assert [c["method"] for c in launches] == ["template"]

    @pytest.mark.asyncio
    async def test_a_study_with_no_code_resolution_at_all_behaves_as_it_did_before(self, session, admin_user, launches):
        """Every study predating this wire has no `code_resolution`, and must keep working."""
        study = await _study_at_reproducing(session, admin_user, {"level3": _LEVEL3})
        await ValidationDriverService._handle_reproducing(session, study)
        assert [c["method"] for c in launches] == ["template"]

    @pytest.mark.asyncio
    async def test_an_install_with_no_isolated_identity_falls_back_to_the_template(self, session, admin_user, launches):
        """Not a hold and not an error: the study still gets bioAF's own reproduction, and the
        report says the authors' code could not be run here."""
        study = await _study_at_reproducing(session, admin_user, {"level3": _LEVEL3, "code_resolution": _RESOLVED_CODE})
        await ValidationDriverService._handle_reproducing(session, study)

        assert [c["method"] for c in launches] == ["template"]
        assert study.evidence_json["code_execution"]["outcome"]


class TestOnceAnArmIsAttempted:
    @pytest.mark.asyncio
    async def test_a_failed_code_arm_is_preserved_as_that_arms_outcome(
        self, session, admin_user, launches, untrusted_ready, monkeypatch
    ):
        """A dependency install that will not resolve is an execution-arm FINDING, not a reason to
        try something else."""

        async def _load(_session, sid):
            return SimpleNamespace(id=sid, status="failed")

        async def _poll(_session, cs):
            return SimpleNamespace(id=cs.id, status="failed", failure_message="pip could not resolve DESeq2")

        monkeypatch.setattr(ValidationDriverService, "_load_compute_session", _load)
        monkeypatch.setattr(NotebookExecutionService, "poll_execution", _poll)

        study = await _study_at_reproducing(
            session,
            admin_user,
            {
                "level3": _LEVEL3,
                "code_resolution": _RESOLVED_CODE,
                "code_execution": {"attempt": 1, "method": "authors_code", "session_id": 901, "source": {}},
            },
        )
        await ValidationDriverService._handle_reproducing(session, study)

        execution = study.evidence_json["code_execution"]
        assert execution["outcome"] in ("code_error", "dependency_unresolvable")
        assert execution["observation"]["transcript_tail"] or execution["observation"]["outcome"]
        assert study.state == "comparing"

    @pytest.mark.asyncio
    async def test_no_generated_run_starts_behind_a_failed_code_arm(
        self, session, admin_user, launches, untrusted_ready, monkeypatch
    ):
        """Silently replacing `dependency_unresolvable` with a generated run that agrees would turn
        the single most useful finding this feature can produce into a false reproduction."""

        async def _load(_session, sid):
            return SimpleNamespace(id=sid, status="failed")

        async def _poll(_session, cs):
            return SimpleNamespace(id=cs.id, status="failed", failure_message="boom")

        monkeypatch.setattr(ValidationDriverService, "_load_compute_session", _load)
        monkeypatch.setattr(NotebookExecutionService, "poll_execution", _poll)

        study = await _study_at_reproducing(
            session,
            admin_user,
            {
                "level3": _LEVEL3,
                "code_resolution": _RESOLVED_CODE,
                "code_execution": {"attempt": 1, "method": "authors_code", "session_id": 901, "source": {}},
            },
        )
        await ValidationDriverService._handle_reproducing(session, study)
        assert launches == []

    @pytest.mark.asyncio
    async def test_a_code_arm_failure_does_not_collapse_into_prose(
        self, session, admin_user, launches, untrusted_ready, monkeypatch
    ):
        """`_degrade_to_level2` writes one prose string and is right for the template arm. For the
        code arm it destroys the product."""

        async def _load(_session, sid):
            return SimpleNamespace(id=sid, status="failed")

        async def _poll(_session, cs):
            return SimpleNamespace(id=cs.id, status="failed", failure_message="boom")

        monkeypatch.setattr(ValidationDriverService, "_load_compute_session", _load)
        monkeypatch.setattr(NotebookExecutionService, "poll_execution", _poll)

        study = await _study_at_reproducing(
            session,
            admin_user,
            {
                "level3": _LEVEL3,
                "code_resolution": _RESOLVED_CODE,
                "code_execution": {"attempt": 1, "method": "authors_code", "session_id": 901, "source": {}},
            },
        )
        await ValidationDriverService._handle_reproducing(session, study)
        assert study.evidence_json.get("level3_failed") is None


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_a_repeated_tick_polls_rather_than_launching_a_second_run(
        self, session, admin_user, launches, untrusted_ready, monkeypatch
    ):
        """`session_id` is the idempotency key, exactly as `level3_run_session_id` already is for
        the template arm."""

        async def _load(_session, sid):
            return SimpleNamespace(id=sid, status="running")

        async def _poll(_session, cs):
            return SimpleNamespace(id=cs.id, status="running")

        monkeypatch.setattr(ValidationDriverService, "_load_compute_session", _load)
        monkeypatch.setattr(NotebookExecutionService, "poll_execution", _poll)

        study = await _study_at_reproducing(session, admin_user, {"level3": _LEVEL3, "code_resolution": _RESOLVED_CODE})
        await ValidationDriverService._handle_reproducing(session, study)
        assert len(launches) == 1

        await ValidationDriverService._handle_reproducing(session, study)
        assert len(launches) == 1

    @pytest.mark.asyncio
    async def test_the_code_arms_session_id_is_not_the_template_arms(
        self, session, admin_user, launches, untrusted_ready
    ):
        """A study can have both a template result and a code-arm result, and collapsing them into
        one key loses one."""
        study = await _study_at_reproducing(session, admin_user, {"level3": _LEVEL3, "code_resolution": _RESOLVED_CODE})
        await ValidationDriverService._handle_reproducing(session, study)

        assert study.evidence_json["code_execution"]["session_id"] == 901
        assert study.evidence_json.get("level3_run_session_id") is None


class TestEveryPathReachesATerminalState:
    @pytest.mark.asyncio
    async def test_a_code_arm_that_writes_nothing_still_advances(
        self, session, admin_user, launches, untrusted_ready, monkeypatch
    ):
        """None of step 17's outcomes is a hold. There is no path through this ladder that parks."""

        async def _load(_session, sid):
            return SimpleNamespace(id=sid, status="completed")

        async def _poll(_session, cs):
            return SimpleNamespace(id=cs.id, status="completed", failure_message=None)

        async def _outputs(_session, _cs):
            return []

        monkeypatch.setattr(ValidationDriverService, "_load_compute_session", _load)
        monkeypatch.setattr(NotebookExecutionService, "poll_execution", _poll)
        monkeypatch.setattr(ValidationDriverService, "_read_code_outputs", _outputs)

        study = await _study_at_reproducing(
            session,
            admin_user,
            {
                "level3": _LEVEL3,
                "code_resolution": _RESOLVED_CODE,
                "code_execution": {"attempt": 1, "method": "authors_code", "session_id": 901, "source": {}},
            },
        )
        await ValidationDriverService._handle_reproducing(session, study)

        assert study.state == "comparing"
        assert study.evidence_json["code_execution"]["outcome"] == "ran_no_output"

    @pytest.mark.asyncio
    async def test_output_bioaf_cannot_compare_is_its_own_outcome(
        self, session, admin_user, launches, untrusted_ready, monkeypatch
    ):
        async def _load(_session, sid):
            return SimpleNamespace(id=sid, status="completed")

        async def _poll(_session, cs):
            return SimpleNamespace(id=cs.id, status="completed", failure_message=None)

        async def _outputs(_session, _cs):
            return [{"path": "figure3.pdf", "text": "%PDF-1.4"}]

        monkeypatch.setattr(ValidationDriverService, "_load_compute_session", _load)
        monkeypatch.setattr(NotebookExecutionService, "poll_execution", _poll)
        monkeypatch.setattr(ValidationDriverService, "_read_code_outputs", _outputs)

        study = await _study_at_reproducing(
            session,
            admin_user,
            {
                "level3": _LEVEL3,
                "code_resolution": _RESOLVED_CODE,
                "code_execution": {"attempt": 1, "method": "authors_code", "session_id": 901, "source": {}},
            },
        )
        await ValidationDriverService._handle_reproducing(session, study)

        assert study.evidence_json["code_execution"]["outcome"] == "ran_output_uncomparable"
        assert study.state == "comparing"


class TestTheStudyThatCameBefore:
    @pytest.mark.asyncio
    async def test_a_study_with_no_level3_still_falls_through_to_comparing(self, session, admin_user, launches):
        study = await _study_at_reproducing(session, admin_user, {"computed_metrics": {}})
        await ValidationDriverService._handle_reproducing(session, study)
        assert study.state == "comparing"
        assert launches == []


class TestStepSixteenIsActuallyCalled:
    """The step 11 lesson applied a second time: a service nothing calls is not a feature.

    `resolve_code` and `choose_entry_point` are built and tested, and without this wire nothing in
    the application would ever call either.
    """

    @pytest.fixture
    def _no_launch(self, monkeypatch):
        async def _template(session, **kw):
            return SimpleNamespace(id=900, status="running")

        monkeypatch.setattr(NotebookExecutionService, "execute_template", _template)

    @pytest.mark.asyncio
    async def test_the_first_visit_resolves_the_paper_s_code(self, session, admin_user, monkeypatch, _no_launch):
        import io
        import json
        import tarfile

        from app.services.reproduction_plan_service import ReproductionPlanService

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            data = b"library(DESeq2)\n"
            info = tarfile.TarInfo(name="lab-paper-abc/analysis.R")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        tarball = buf.getvalue()

        async def _fetch(url):
            if "/commits/" in url:
                return json.dumps({"sha": "abc1234"}).encode()
            return tarball

        from app.services import validation_driver_service as drv

        monkeypatch.setattr(drv, "_deposit_bytes_fetcher", _fetch)

        study = await _study_at_reproducing(session, admin_user, {"level3": _LEVEL3})
        plan = await ReproductionPlanService.create_plan(session, study, admin_user.id, pipeline_key="nf-core/rnaseq")
        plan.code_availability_json = [{"kind": "github", "url": "https://github.com/lab/paper", "identifier": None}]
        await session.flush()

        await ValidationDriverService._handle_reproducing(session, study)

        resolution = study.evidence_json["code_resolution"]
        assert resolution["outcome"] == "resolved"
        assert resolution["commit_sha"] == "abc1234"

    @pytest.mark.asyncio
    async def test_a_paper_with_no_code_records_that_it_looked(self, session, admin_user, _no_launch):
        study = await _study_at_reproducing(session, admin_user, {"level3": _LEVEL3})
        await ValidationDriverService._handle_reproducing(session, study)
        assert study.evidence_json["code_resolution"]["outcome"] == "code_absent"

    @pytest.mark.asyncio
    async def test_the_attempted_fetch_answers_accessibility_on_the_checklist(
        self, session, admin_user, monkeypatch, _no_launch
    ):
        """Step 13 established that the source EXISTS and left accessibility open, because only an
        attempted fetch can settle it."""
        from app.services.reproduction_plan_service import ReproductionPlanService

        async def _fetch(url):
            raise RuntimeError("404 Not Found")

        from app.services import validation_driver_service as drv

        monkeypatch.setattr(drv, "_deposit_bytes_fetcher", _fetch)

        study = await _study_at_reproducing(
            session,
            admin_user,
            {
                "level3": _LEVEL3,
                "capabilities": {
                    "code_sources": [
                        {
                            "kind": "github",
                            "url": "https://github.com/lab/private",
                            "identifier": None,
                            "exists": "yes",
                            "accessible": "not_attempted",
                            "accessible_reason": None,
                        }
                    ]
                },
            },
        )
        plan = await ReproductionPlanService.create_plan(session, study, admin_user.id, pipeline_key="nf-core/rnaseq")
        plan.code_availability_json = [{"kind": "github", "url": "https://github.com/lab/private", "identifier": None}]
        await session.flush()

        await ValidationDriverService._handle_reproducing(session, study)

        source = study.evidence_json["capabilities"]["code_sources"][0]
        assert source["exists"] == "yes"
        assert source["accessible"] == "no"
        assert source["accessible_reason"]

    @pytest.mark.asyncio
    async def test_it_resolves_once_and_not_on_every_tick(self, session, admin_user, monkeypatch, _no_launch):
        calls: list[str] = []

        async def _fetch(url):
            calls.append(url)
            raise RuntimeError("503")

        from app.services import validation_driver_service as drv
        from app.services.reproduction_plan_service import ReproductionPlanService

        monkeypatch.setattr(drv, "_deposit_bytes_fetcher", _fetch)
        study = await _study_at_reproducing(session, admin_user, {"level3": _LEVEL3})
        plan = await ReproductionPlanService.create_plan(session, study, admin_user.id, pipeline_key="nf-core/rnaseq")
        plan.code_availability_json = [{"kind": "github", "url": "https://github.com/lab/p", "identifier": None}]
        await session.flush()

        await ValidationDriverService._handle_reproducing(session, study)
        first = len(calls)
        study.state = "reproducing"
        await ValidationDriverService._handle_reproducing(session, study)
        assert len(calls) == first


class TestTheCodeArmsOutputIsActuallyCompared:
    """Step 17's differential-table adapter "lands where `_extract_reproduced_set` and the
    concordance service already read it. This is today's path."

    Without that, the arm reports `ran_output_agrees` on the strength of having written a table,
    which is a claim of agreement made without comparing anything. That is the exact defect step 9
    exists to prevent, one layer down.
    """

    _PAPER_SET = {
        "kind": "gene",
        "namespace": "symbol",
        "entities": [{"id": "A", "direction": "up"}, {"id": "B", "direction": "down"}],
        "n_sig": 2,
    }

    def _level3(self):
        return {**_LEVEL3, "paper_finding_set": self._PAPER_SET, "universe": 20000}

    def _patch_poll(self, monkeypatch, table: str):
        async def _load(_session, sid):
            return SimpleNamespace(id=sid, status="completed", gcs_output_prefix=None, failure_message=None)

        async def _poll(_session, cs):
            return SimpleNamespace(id=cs.id, status="completed", failure_message=None, gcs_output_prefix=None)

        async def _outputs(_session, _cs):
            return [{"path": "results/de.csv", "text": table}]

        monkeypatch.setattr(ValidationDriverService, "_load_compute_session", _load)
        monkeypatch.setattr(NotebookExecutionService, "poll_execution", _poll)
        monkeypatch.setattr(ValidationDriverService, "_read_code_outputs", _outputs)

    async def _run(self, session, admin_user, table):
        study = await _study_at_reproducing(
            session,
            admin_user,
            {
                "level3": self._level3(),
                "code_resolution": _RESOLVED_CODE,
                "code_execution": {"attempt": 1, "method": "authors_code", "session_id": 901, "source": {}},
            },
        )
        await ValidationDriverService._handle_reproducing(session, study)
        return study

    @pytest.mark.asyncio
    async def test_a_table_that_reproduces_the_paper_scores_agreement(
        self, session, admin_user, monkeypatch, untrusted_ready
    ):
        table = "gene,log2FoldChange,padj\nA,2.0,0.001\nB,-2.1,0.002\n" + "".join(
            f"FLAT{i},0.05,0.9\n" for i in range(200)
        )
        self._patch_poll(monkeypatch, table)
        study = await self._run(session, admin_user, table)

        assert study.evidence_json["code_execution"]["outcome"] == "ran_output_agrees"
        assert study.evidence_json["level3_result"]["concordance"]["verdict"] == "agree"

    @pytest.mark.asyncio
    async def test_a_table_that_does_not_is_a_divergence_not_an_agreement(
        self, session, admin_user, monkeypatch, untrusted_ready
    ):
        """The load-bearing case. Reporting agreement because a table was written would be a claim
        made without comparing."""
        table = "gene,log2FoldChange,padj\nX,2.0,0.001\nY,-2.1,0.002\n" + "".join(
            f"FLAT{i},0.05,0.9\n" for i in range(200)
        )
        self._patch_poll(monkeypatch, table)
        study = await self._run(session, admin_user, table)

        assert study.evidence_json["code_execution"]["outcome"] == "ran_output_diverges"
        assert study.evidence_json["level3_result"]["concordance"]["verdict"] != "agree"

    @pytest.mark.asyncio
    async def test_both_numbers_reach_the_observation(self, session, admin_user, monkeypatch, untrusted_ready):
        """The report always shows ours beside the paper's, and the observation is where it reads
        them from."""
        table = "gene,log2FoldChange,padj\nX,2.0,0.001\n" + "".join(f"FLAT{i},0.05,0.9\n" for i in range(200))
        self._patch_poll(monkeypatch, table)
        study = await self._run(session, admin_user, table)

        observation = study.evidence_json["code_execution"]["observation"]
        assert observation["paper_value"] == 2
        assert observation["our_value"] == 1

    @pytest.mark.asyncio
    async def test_the_concordance_is_attributed_to_the_authors_own_code(
        self, session, admin_user, monkeypatch, untrusted_ready
    ):
        """A concordance scored against the authors' OWN code is a stronger claim than one scored
        against ours, and step 9's rule says the verdict has to be able to say so."""
        table = "gene,log2FoldChange,padj\nA,2.0,0.001\nB,-2.1,0.002\n" + "".join(
            f"FLAT{i},0.05,0.9\n" for i in range(200)
        )
        self._patch_poll(monkeypatch, table)
        study = await self._run(session, admin_user, table)

        assert study.evidence_json["level3_result"]["method"] == "authors_code"
