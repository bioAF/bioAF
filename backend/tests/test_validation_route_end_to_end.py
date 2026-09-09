"""plan_7 step 12: the whole route, driven through the real driver loop.

The test whose absence let step 11's blocker ship. Ten steps were each verified individually against
live GEO and every one of them worked; the route they compose into had no wire between step 2 and
step 5, and nothing noticed, because no test ever drove `plan_ready -> classified` in one go.

**Only the external boundaries are faked**: GEO over HTTP, object storage, the LLM provider, and the
Kubernetes notebook execution. Everything between them is the real driver, the real services and
real database rows. A test that fakes a service seam proves the seam, not the route.

**Coverage runs to the terminal state and to the exported report**, never to an intermediate state
with a well-formed bundle on it. Reaching `reproducing` with `evidence["level3"]` populated is the
same shape of proof that produced the step 11 blocker.

Cases for the execution arms (published code, generated fallback, uncomparable output) join this
file as steps 17 and 18 land; each is listed in plan_7 step 12's table.
"""

import gzip
import hashlib
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.file import File
from app.models.notebook_session import ComputeSession
from app.models.notebook_session_file import NotebookSessionFile
from app.models.organization import Organization
from app.models.template_notebook import TemplateNotebook
from app.models.validation_study import ValidationStudy
from app.services.notebook_execution_service import NotebookExecutionService
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService

# ---- the paper this study is reproducing, in the shapes GEO actually serves ----

_GSE = "GSE274331"
_SUPPL = f"https://ftp.ncbi.nlm.nih.gov/geo/series/GSE274nnn/{_GSE}/suppl/"

_LISTING = f"""<html><body><pre>
<a href="{_GSE}_counts.tsv.gz">{_GSE}_counts.tsv.gz</a>
<a href="{_GSE}_sample_metadata.tsv">{_GSE}_sample_metadata.tsv</a>
<a href="{_GSE}_RAW.tar">{_GSE}_RAW.tar</a>
</pre></body></html>"""

# Six samples, two clean arms, integer counts. Three genes move the way the paper says they do and
# the rest are flat, so the universe the enrichment test sees is a plausible one rather than three
# genes: an overlap of 3 out of 3 is only meaningful against a background of thousands.
_MOVERS = {
    "AAAS": ((100, 110, 105), (400, 420, 410)),
    "BRCA1": ((500, 520, 510), (120, 118, 125)),
    "CDK2": ((80, 85, 82), (300, 310, 305)),
}


def _counts_matrix(n_flat=400) -> str:
    rows = ["gene\tCTRL_1\tCTRL_2\tCTRL_3\tKD_1\tKD_2\tKD_3"]
    for gene, (ctrl, kd) in _MOVERS.items():
        rows.append("\t".join([gene, *(str(v) for v in ctrl), *(str(v) for v in kd)]))
    for i in range(n_flat):
        base = 200 + i
        rows.append("\t".join([f"FLAT{i:04d}", *[str(base + d) for d in (0, 3, -2, 1, -1, 2)]]))
    return "\n".join(rows) + "\n"


_COUNTS = _counts_matrix()

_METADATA = "sample\tcondition\nCTRL_1\tControl\nCTRL_2\tControl\nCTRL_3\tControl\nKD_1\tKD\nKD_2\tKD\nKD_3\tKD\n"

_DESIGN = {
    "contrasts": [
        {
            "name": "KD vs Control",
            "test_condition": "KD",
            "reference_condition": "Control",
            "test_samples": ["KD_1", "KD_2", "KD_3"],
            "reference_samples": ["CTRL_1", "CTRL_2", "CTRL_3"],
        }
    ],
    "thresholds": {"log2fc": 1.0, "padj": 0.05},
}

_PAPER_SET = {
    "kind": "gene",
    "namespace": "symbol",
    "entities": [
        {"id": "AAAS", "direction": "up"},
        {"id": "BRCA1", "direction": "down"},
        {"id": "CDK2", "direction": "up"},
    ],
    "n_sig": 3,
}

_CLAIM = {"kind": "gene", "namespace": "symbol", "confirmed": True, "finding_set": _PAPER_SET}


def _reproduced_table(n_flat=400) -> str:
    """What the reproduction notebook writes back: the same three genes moving the same way, plus
    the genes it tested and found nothing in. The flat rows are what make `n_tested` the universe
    the concordance test is scored against."""
    rows = ["gene,log2FoldChange,padj", "AAAS,2.0,0.001", "BRCA1,-2.1,0.002", "CDK2,1.9,0.004"]
    rows += [f"FLAT{i:04d},0.05,0.8" for i in range(n_flat)]
    return "\n".join(rows) + "\n"


_REPRODUCED_TABLE = _reproduced_table()


# ---- the external boundaries, and nothing else ----


class _FakeStorage:
    """Object storage. Holds what the driver writes and serves it back."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def write_bytes(self, uri, data, *, content_type="application/octet-stream"):
        self.objects[uri] = data

    async def write_text(self, uri, text, *, content_type="text/plain"):
        self.objects[uri] = text.encode()

    async def read_text(self, uri):
        if uri not in self.objects:
            raise FileNotFoundError(uri)
        return self.objects[uri].decode()

    def build_uri(self, bucket: str, path: str) -> str:
        return f"gs://{bucket}/{path}"


class _FakeGeo:
    """GEO's two surfaces: the supplementary listing (text) and the files themselves (bytes)."""

    def __init__(self, listing: str, files: dict[str, bytes]):
        self.listing = listing
        self.files = files
        self.text_calls: list[str] = []
        self.byte_calls: list[str] = []

    async def fetch_text(self, url: str) -> str:
        self.text_calls.append(url)
        if url.endswith("filelist.txt"):
            raise RuntimeError("404: this series has no _RAW.tar manifest")
        if url == _SUPPL:
            return self.listing
        raise RuntimeError(f"404 {url}")

    async def fetch_bytes(self, url: str) -> bytes:
        self.byte_calls.append(url)
        if url not in self.files:
            raise RuntimeError(f"404 {url}")
        return self.files[url]


class _FakeLlm:
    """One provider, dispatching on what is being asked. Every call is recorded."""

    def __init__(self, answers: dict[str, str]):
        self.answers = answers
        self.calls: list[str] = []

    # Every decision the route can ask for, keyed by a phrase from its own system prompt. An
    # unrecognised prompt is an assertion failure rather than a default, so a new model call added
    # to the route without a fixture is loud instead of silently answered with the wrong shape.
    _ROUTES = (
        ("choosing which file from a public GEO deposit", "select_deposit"),
        ("ratifying the verdict", "ratify"),
        ("judging whether a paper says enough", "sufficiency"),
        ("choosing which file in a published analysis repository", "entry_point"),
        ("writing the analysis a paper describes", "generate"),
        ("could the paper's number plausibly be noise", "signal"),
        ("naming the MOST LIKELY explanation", "cause"),
    )

    async def submit(self, prompt, payload, model, api_key, attachments=None):
        for marker, key in self._ROUTES:
            if marker in prompt:
                self.calls.append(key)
                if key not in self.answers:
                    raise AssertionError(f"the route asked for '{key}' and this test supplied no answer")
                return self.answers[key]
        self.calls.append("unknown")
        raise AssertionError(f"unexpected LLM call: {prompt[:120]}")


def _fenced(obj) -> str:
    return "```json\n" + json.dumps(obj) + "\n```"


_SELECTION_ANSWER = _fenced(
    {
        "primary_matrix": f"{_GSE}_counts.tsv.gz",
        "metadata_file": f"{_GSE}_sample_metadata.tsv",
        "value_type": "counts",
        "reason": "the only per-gene matrix in the deposit; the tar holds raw reads",
        "confidence": 0.92,
        "declined": False,
    }
)

_RATIFY_ACCEPT = _fenced({"action": "accept", "reasoning": "the finding reproduced on the authors' own matrix"})

_SUFFICIENT = _fenced({"answer": "yes", "reason": "the methods name the tool and the thresholds", "confidence": 0.9})

_ENTRY_POINT_ANSWER = _fenced(
    {
        "entry_point": "analysis.R",
        "arguments": "",
        "reason": "the only script in the repository",
        "confidence": 0.9,
    }
)

_GENERATED_ANSWER = _fenced(
    {
        "language": "R",
        "source": "library(DESeq2)\n# the analysis the paper describes\n",
        "entry_point": "generated_analysis.R",
        "assumptions": ["the paper does not state the FDR threshold, so 0.05 was assumed"],
        "reason": "the methods name DESeq2 and a two-arm design",
        "confidence": 0.6,
    }
)

_SIGNAL_ANSWER = _fenced({"verdict": "not likely", "reason": "the paper's number looks real", "confidence": 0.6})
_CAUSE_ANSWER = _fenced(
    {"candidate": "bioaf_input_mapping", "reason": "we chose the file and the column mapping", "confidence": 0.5}
)

_ALL_ANSWERS = {
    "select_deposit": _SELECTION_ANSWER,
    "ratify": _RATIFY_ACCEPT,
    "sufficiency": _SUFFICIENT,
    "entry_point": _ENTRY_POINT_ANSWER,
    "generate": _GENERATED_ANSWER,
    "signal": _SIGNAL_ANSWER,
    "cause": _CAUSE_ANSWER,
}


class _NotebookRunner:
    """Kubernetes, faked at the service boundary but writing REAL rows.

    `_read_reproduction_output` joins `notebook_session_files` to `files` and reads object storage,
    so a fake that skipped those rows would fake the thing under test. This creates the session, the
    output File and the link, and writes the table into the fake storage.
    """

    def __init__(
        self,
        storage: _FakeStorage,
        *,
        output: str | None = _REPRODUCED_TABLE,
        status="completed",
        code_output: str | None = _REPRODUCED_TABLE,
        code_output_name: str = "findings.csv",
        code_status: str = "completed",
        code_transcript: str = "",
    ):
        self.storage = storage
        self.output = output
        self.status = status
        self.code_output = code_output
        self.code_output_name = code_output_name
        self.code_status = code_status
        self.code_transcript = code_transcript
        self.launches: list[dict] = []

    async def execute_template(self, session, *, org_id, user_id, template_id, parameters, input_file_ids, **kw):
        self.launches.append({"template_id": template_id, "parameters": parameters, "input_file_ids": input_file_ids})
        cs = ComputeSession(
            user_id=user_id,
            organization_id=org_id,
            session_type="notebook",
            resource_profile="small",
            cpu_cores=2,
            memory_gb=8,
            status=self.status,
            experiment_id=kw.get("experiment_id"),
        )
        session.add(cs)
        await session.flush()
        if self.output is not None:
            uri = f"gs://bioaf-working/sessions/{cs.id}/findings.csv"
            await self.storage.write_text(uri, self.output)
            f = File(
                organization_id=org_id,
                filename="findings.csv",
                storage_uri=uri,
                file_type="table",
                source_type="notebook_output",
                uploader_user_id=user_id,
            )
            session.add(f)
            await session.flush()
            session.add(NotebookSessionFile(session_id=cs.id, file_id=f.id, access_type="output"))
            await session.flush()
        return cs

    async def execute_fetched_code(self, session, *, org_id, user_id, code_uri, entry_point, arguments, **kw):
        """The untrusted-execution entry point, faked at the same boundary and writing the same
        real rows, so the code arm's output travels the path the template arm's does."""
        self.launches.append({"code_uri": code_uri, "entry_point": entry_point, "arguments": arguments})
        cs = ComputeSession(
            user_id=user_id,
            organization_id=org_id,
            session_type="headless",
            resource_profile="small",
            cpu_cores=2,
            memory_gb=8,
            status=self.code_status,
            experiment_id=kw.get("experiment_id"),
        )
        cs.failure_message = self.code_transcript
        session.add(cs)
        await session.flush()
        if self.code_output is not None:
            uri = f"gs://bioaf-untrusted/sessions/{cs.id}/{self.code_output_name}"
            await self.storage.write_text(uri, self.code_output)
            f = File(
                organization_id=org_id,
                filename=self.code_output_name,
                storage_uri=uri,
                file_type="table",
                source_type="notebook_output",
                uploader_user_id=user_id,
            )
            session.add(f)
            await session.flush()
            session.add(NotebookSessionFile(session_id=cs.id, file_id=f.id, access_type="output"))
            await session.flush()
        return cs

    async def poll_execution(self, session, cs):
        return cs


# ---- the harness ----


class _Route:
    """Every external boundary of the deposit route, wired at once."""

    def __init__(
        self,
        monkeypatch,
        *,
        llm_answers=None,
        notebook_output=_REPRODUCED_TABLE,
        code_repo=None,
        code_output=_REPRODUCED_TABLE,
        code_output_name="findings.csv",
        code_status="completed",
        code_transcript="",
        untrusted=False,
    ):
        from app.services import validation_driver_service as drv
        from app.services.literature import deposit_inventory_service as inv

        self.storage = _FakeStorage()
        self.geo = _FakeGeo(
            _LISTING,
            {
                _SUPPL + f"{_GSE}_counts.tsv.gz": gzip.compress(_COUNTS.encode()),
                _SUPPL + f"{_GSE}_sample_metadata.tsv": _METADATA.encode(),
            },
        )
        self.llm = _FakeLlm({**_ALL_ANSWERS, **(llm_answers or {})})
        self.notebook = _NotebookRunner(
            self.storage,
            output=notebook_output,
            code_output=code_output,
            code_output_name=code_output_name,
            code_status=code_status,
            code_transcript=code_transcript,
        )
        self.code_repo = code_repo
        self.untrusted = untrusted

        monkeypatch.setattr(drv, "get_storage_adapter", lambda: self.storage)
        monkeypatch.setattr(drv, "_deposit_bytes_fetcher", self.geo.fetch_bytes)
        monkeypatch.setattr(inv, "_http_fetch_text", self.geo.fetch_text)
        monkeypatch.setattr(drv, "get_client", lambda provider: self.llm)

        async def _cfg(sess, org_id, feature):
            return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

        monkeypatch.setattr(drv.llm_provider_config_service, "get_for_feature", _cfg)
        monkeypatch.setattr(NotebookExecutionService, "execute_template", self.notebook.execute_template)
        monkeypatch.setattr(NotebookExecutionService, "execute_fetched_code", self.notebook.execute_fetched_code)
        monkeypatch.setattr(NotebookExecutionService, "poll_execution", self.notebook.poll_execution)

        if code_repo is not None:
            # GitHub, at the same byte boundary GEO uses: the driver reaches both through
            # `_deposit_bytes_fetcher`, so one fake serves them both.
            geo_bytes = self.geo.fetch_bytes

            async def _bytes(url: str) -> bytes:
                if "github.com" in url or "api.github.com" in url:
                    if "/commits/" in url:
                        return json.dumps({"sha": "abc1234def"}).encode()
                    return code_repo
                return await geo_bytes(url)

            monkeypatch.setattr(drv, "_deposit_bytes_fetcher", _bytes)

    async def enable_untrusted_execution(self, session):
        """What step 16a's terraform writes. Without it the code arm cannot run anywhere, which is
        itself one of the cases this file holds."""
        from app.platform.platform_config_service import PlatformConfigService

        await PlatformConfigService.set(session, "untrusted_bucket_name", "bioaf-untrusted-lab-abc")
        await PlatformConfigService.set(session, "untrusted_runner_sa_email", "bioaf-untrusted-runner@p.iam.g.com")


async def _tick_to_rest(session, study, *, limit=40, twice=False, settle=2):
    """Run the driver until the study stops moving, as the lifespan loop does.

    ``settle`` is 2 rather than 1 because a handler can do real work without changing state: the
    reproducing handler LAUNCHES on one tick and advances on the next, and treating the first as
    rest would stop the walk one state short of the verdict.

    ``twice`` ticks every state a second time before accepting that it is at rest, which is the
    idempotency assertion: a repeated tick must not launch, download or ask for anything again.
    """
    unchanged = 0
    for _ in range(limit):
        before = study.state
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        if twice:
            await ValidationDriverService.advance_active_studies(session)
            await session.refresh(study)
        unchanged = unchanged + 1 if study.state == before else 0
        if unchanged >= settle:
            return study.state
    raise AssertionError(f"the study never came to rest (stuck at {study.state})")


@pytest_asyncio.fixture
async def autonomous_org(session, admin_user):
    org = (
        await session.execute(select(Organization).where(Organization.id == admin_user.organization_id))
    ).scalar_one()
    org.lit_validation_autonomy = "autonomous"
    await session.flush()
    return org


@pytest_asyncio.fixture
async def deseq2_template(session, admin_user):
    tmpl = TemplateNotebook(
        organization_id=admin_user.organization_id,
        name="Differential Expression (DESeq2, headless)",
        category="differential_expression",
        notebook_path="notebooks/de_bulk_deseq2.ipynb",
        parameters_json={"id_column": "gene_id"},
        is_builtin=True,
    )
    session.add(tmpl)
    await session.flush()
    return tmpl


def _code_repo(files: dict[str, bytes] | None = None) -> bytes:
    """A GitHub tarball, in the shape GitHub actually serves: everything under `<repo>-<sha>/`."""
    import io
    import tarfile

    members = files or {"analysis.R": b"library(DESeq2)\n", "README.md": b"# the paper's analysis\n"}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name=f"lab-paper-abc1234/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


async def _plan_ready_study(session, admin_user, *, accession=_GSE, code_availability=None):
    """A study read, planned and parked at the C1 gate, as `read_and_plan` leaves it."""
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1000/plan7", source_accession=accession
    )
    plan = await ReproductionPlanService.create_plan(
        session,
        study,
        admin_user.id,
        accessions=["GSM1", "GSM2"],
        pipeline_key="nf-core/rnaseq",
        pipeline_version="3.14.0",
        reference_genome="GRCh38",
        mapping_confidence="exact",
        differential_design=_DESIGN,
    )
    plan.finding_claim_json = _CLAIM
    # Where the authors said their code lives. `None` means the paper named none, which is what
    # sends the route down the generated arm.
    plan.code_availability_json = code_availability
    study.state = "plan_ready"
    await session.flush()
    return study


async def _report_text(session, study, admin_user) -> str:
    """The exported report, which is where step 12's coverage ends. Reaching an intermediate state
    with a well-formed bundle is the shape of proof that produced the step 11 blocker."""
    from app.services.provenance.report_service import ProvenanceReportService

    result = await ProvenanceReportService.generate(
        session=session,
        entity_type="validation_study",
        entity_id=study.id,
        org_id=admin_user.organization_id,
        user_email=admin_user.email,
        format="md",
    )
    return result.content if isinstance(result.content, str) else result.content.decode()


class TestTheDepositRouteReachesAVerdict:
    """Case 1 of plan_7 step 12: deposit route, template reproduction, to a terminal state."""

    @pytest.mark.asyncio
    async def test_approval_alone_carries_the_study_to_classified(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        """One approval and the driver's own ticks. No human step in between, and no test reaching
        in to hand the route a selection it should have made itself."""
        _Route(monkeypatch)
        study = await _plan_ready_study(session, admin_user)

        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        state = await _tick_to_rest(session, study)

        assert state == "classified"
        assert study.classification in {"validated", "partially_validated", "inconclusive"}

    @pytest.mark.asyncio
    async def test_every_stage_of_the_route_left_its_evidence(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        """The eight steps, each visible on the bundle. A route that reached the end with a stage
        missing would be a route that skipped it."""
        _Route(monkeypatch)
        study = await _plan_ready_study(session, admin_user)
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        await _tick_to_rest(session, study)

        evidence = study.evidence_json
        assert evidence["route"] == "deposit"
        assert evidence["deposit_inventory"]["entries"]  # step 1
        assert evidence["deposit_selection"]["primary_matrix"] == f"{_GSE}_counts.tsv.gz"  # step 2
        assert evidence["deposit"]["files"]  # step 5
        assert evidence["deposit_inspection"]["value_type_observed"] == "counts"  # step 6
        assert evidence["deposit_metadata_association"]  # step 7
        assert evidence["level3"]["template_id"] == deseq2_template.id  # step 8
        assert evidence["level3_result"]["concordance"]

    @pytest.mark.asyncio
    async def test_the_finding_reproduced_from_the_authors_own_matrix(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        """Concordance is scored against the paper's own set, from the paper's own deposit. This is
        the answer the route exists to produce."""
        _Route(monkeypatch)
        study = await _plan_ready_study(session, admin_user)
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        await _tick_to_rest(session, study)

        conc = study.evidence_json["level3_result"]["concordance"]
        assert conc["verdict"] == "agree"
        assert conc["concordant"] == 3

    @pytest.mark.asyncio
    async def test_the_verdict_says_which_kind_of_validation_produced_it(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        """Step 9's rule: a verdict from the authors' own processed data tests their statistics, not
        their processing, and the classification must say so rather than let the route be inferred."""
        _Route(monkeypatch)
        study = await _plan_ready_study(session, admin_user)
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        await _tick_to_rest(session, study)

        verdict = study.evidence_json["classification_result"]
        assert verdict["route"] == "deposit"
        assert verdict["reproduction_method"] == "deseq2"
        assert "not by re-running the paper from its raw reads" in verdict["reasoning"]

    @pytest.mark.asyncio
    async def test_the_deposit_is_downloaded_from_geo_and_checksummed(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        """GEO revises supplementary files in place, so what WE downloaded is the only thing that
        makes this verdict reproducible later."""
        route = _Route(monkeypatch)
        study = await _plan_ready_study(session, admin_user)
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        await _tick_to_rest(session, study)

        matrix = next(f for f in study.evidence_json["deposit"]["files"] if f["filename"].endswith("counts.tsv.gz"))
        assert matrix["md5"] == hashlib.md5(gzip.compress(_COUNTS.encode())).hexdigest()
        assert matrix["url"].startswith(_SUPPL)
        assert route.geo.byte_calls  # it really went to GEO


class TestTheRouteIsIdempotent:
    @pytest.mark.asyncio
    async def test_ticking_twice_at_every_state_changes_nothing(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        """The lifespan loop ticks on a timer and a restart re-enters mid-route. A second visit must
        poll what was launched, not launch a second one."""
        route = _Route(monkeypatch)
        study = await _plan_ready_study(session, admin_user)
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )

        state = await _tick_to_rest(session, study, twice=True)

        assert state == "classified"
        assert route.llm.calls.count("select_deposit") == 1
        assert len(route.notebook.launches) == 1
        assert len(route.geo.byte_calls) == 2  # the matrix and the metadata, once each
        files = (await session.execute(select(File).where(File.experiment_id == study.experiment_id))).scalars().all()
        assert len([f for f in files if f.source_type == "external_deposit"]) == 2


class TestTheExportCarriesTheRoute:
    @pytest.mark.asyncio
    async def test_the_report_names_the_deposit_it_ran_on(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        """A report that is right on screen and lossy on export fails the same reader. The deposited
        file, its URL and its checksum have to survive into the export."""
        from app.services.provenance.report_service import ProvenanceReportService

        _Route(monkeypatch)
        study = await _plan_ready_study(session, admin_user)
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        await _tick_to_rest(session, study)

        result = await ProvenanceReportService.generate(
            session=session,
            entity_type="validation_study",
            entity_id=study.id,
            org_id=admin_user.organization_id,
            user_email=admin_user.email,
            format="json",
        )
        body = json.loads(result.content) if isinstance(result.content, (str, bytes)) else result.content
        evidence = body["report"]["entity"]["evidence"] if "report" in body else body["entity"]["evidence"]
        assert evidence["route"] == "deposit"
        assert evidence["deposit"]["files"][0]["md5"]
        assert evidence["level3_result"]["concordance"]["verdict"] == "agree"


class TestTheRouteFailsHonestly:
    @pytest.mark.asyncio
    async def test_a_notebook_that_writes_nothing_does_not_score_a_comparison(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        """Study 13's lesson, held end to end: a headless notebook that raised still exits cleanly,
        and an empty output must never be scored as `we looked and found nothing`."""
        _Route(monkeypatch, notebook_output=None)
        study = await _plan_ready_study(session, admin_user)
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        await _tick_to_rest(session, study)

        assert study.evidence_json.get("level3_result") is None
        assert study.evidence_json["level3_failed"]["reason"]
        assert study.state in {"comparing", "classified"}

    @pytest.mark.asyncio
    async def test_a_deposit_geo_will_not_list_holds_with_a_reason(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        """An unreachable GEO is a reason to take the pipeline route, never a silent park at
        `acquiring_processed`, which is exactly what shipped before step 11."""
        route = _Route(monkeypatch)

        async def _dead(url):
            raise RuntimeError("connection reset by peer")

        monkeypatch.setattr(route.geo, "fetch_text", _dead)
        from app.services.literature import deposit_inventory_service as inv

        monkeypatch.setattr(inv, "_http_fetch_text", _dead)

        study = await _plan_ready_study(session, admin_user)
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        state = await _tick_to_rest(session, study)

        assert state == "acquiring_processed"
        assert study.evidence_json["deposit_failed"]["reason"]


class TestThePipelineRouteStillWorks:
    @pytest.mark.asyncio
    async def test_approving_onto_raw_reads_launches_fetchngs_not_a_download(
        self, session, admin_user, monkeypatch, autonomous_org
    ):
        """Both routes are held, so the deposit route cannot quietly become the only one."""
        from app.services.pipeline_run_service import PipelineRunService

        route = _Route(monkeypatch)
        launches: list = []

        async def _launch(session_, org_id, user_id, data, *, via_assistant=False):
            from app.models.pipeline_run import PipelineRun

            launches.append(data)
            run = PipelineRun(
                organization_id=org_id,
                experiment_id=data.experiment_id,
                pipeline_name=data.pipeline_key,
                pipeline_version="test",
                status="running",
                parameters_json=dict(data.parameters or {}),
                submitted_by_user_id=user_id,
            )
            session_.add(run)
            await session_.flush()
            return run

        monkeypatch.setattr(PipelineRunService, "launch_run", _launch)
        study = await _plan_ready_study(session, admin_user)

        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="pipeline"
        )
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)

        assert study.state == "acquiring_data"
        assert "fetchngs" in launches[0].pipeline_key
        assert route.geo.byte_calls == []  # nothing was downloaded from the deposit


class TestBothRoutesRunSideBySide:
    @pytest.mark.asyncio
    async def test_the_sibling_takes_the_raw_route_while_this_one_takes_the_deposit(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        """`both` is two studies over one paper: they answer different questions, so each has to
        reach its own terminal state rather than one standing in for the other."""
        from app.services.pipeline_run_service import PipelineRunService

        _Route(monkeypatch)

        async def _launch(session_, org_id, user_id, data, *, via_assistant=False):
            from app.models.pipeline_run import PipelineRun

            run = PipelineRun(
                organization_id=org_id,
                experiment_id=data.experiment_id,
                pipeline_name=data.pipeline_key,
                pipeline_version="test",
                status="running",
                parameters_json=dict(data.parameters or {}),
                submitted_by_user_id=user_id,
            )
            session_.add(run)
            await session_.flush()
            return run

        monkeypatch.setattr(PipelineRunService, "launch_run", _launch)
        study = await _plan_ready_study(session, admin_user)

        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="both"
        )
        sibling_id = study.evidence_json["sibling_study_id"]
        await _tick_to_rest(session, study)
        sibling = (await session.execute(select(ValidationStudy).where(ValidationStudy.id == sibling_id))).scalar_one()

        assert study.state == "classified"
        assert study.evidence_json["route"] == "deposit"
        assert sibling.evidence_json["route"] == "pipeline"
        assert sibling.state == "acquiring_data"


class TestThePublishedCodeArm:
    """Case 2 of plan_7 step 12: published code runs and its output is comparable."""

    @pytest.mark.asyncio
    async def test_the_authors_own_code_is_fetched_pinned_and_run(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        route = _Route(monkeypatch, code_repo=_code_repo())
        await route.enable_untrusted_execution(session)
        study = await _plan_ready_study(
            session, admin_user, code_availability=[{"kind": "github", "url": "https://github.com/lab/paper"}]
        )
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        state = await _tick_to_rest(session, study)

        assert state == "classified"
        execution = study.evidence_json["code_execution"]
        assert execution["method"] == "authors_code"
        assert execution["source"]["repo_url"] == "https://github.com/lab/paper"
        assert execution["source"]["commit_sha"] == "abc1234def"

    @pytest.mark.asyncio
    async def test_the_repo_and_commit_reach_the_report(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        route = _Route(monkeypatch, code_repo=_code_repo())
        await route.enable_untrusted_execution(session)
        study = await _plan_ready_study(
            session, admin_user, code_availability=[{"kind": "github", "url": "https://github.com/lab/paper"}]
        )
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        await _tick_to_rest(session, study)

        text = await _report_text(session, study, admin_user)
        assert "github.com/lab/paper" in text
        assert "abc1234def" in text

    @pytest.mark.asyncio
    async def test_the_template_arm_is_not_also_run(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        """Once an arm is attempted its result is the result."""
        route = _Route(monkeypatch, code_repo=_code_repo())
        await route.enable_untrusted_execution(session)
        study = await _plan_ready_study(
            session, admin_user, code_availability=[{"kind": "github", "url": "https://github.com/lab/paper"}]
        )
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        await _tick_to_rest(session, study)

        assert all("template_id" not in launch for launch in route.notebook.launches)


class TestThePublishedCodeArmFails:
    """Case 3: published code is attempted and fails. Its outcome and transcript survive to the
    report, NO generated run starts behind it, and the study still reaches a terminal state."""

    async def _run(self, session, admin_user, monkeypatch, transcript):
        route = _Route(
            monkeypatch,
            code_repo=_code_repo(),
            code_status="failed",
            code_output=None,
            code_transcript=transcript,
        )
        await route.enable_untrusted_execution(session)
        study = await _plan_ready_study(
            session, admin_user, code_availability=[{"kind": "github", "url": "https://github.com/lab/paper"}]
        )
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        state = await _tick_to_rest(session, study)
        return route, study, state

    @pytest.mark.asyncio
    async def test_dependencies_that_will_not_install_are_the_finding(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        _, study, state = await self._run(
            session,
            admin_user,
            monkeypatch,
            "ERROR: Could not find a version that satisfies the requirement DESeq2==1.2.3",
        )
        assert state == "classified"
        assert study.evidence_json["code_execution"]["outcome"] == "dependency_unresolvable"

    @pytest.mark.asyncio
    async def test_the_transcript_survives_to_the_report(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        _, study, _ = await self._run(
            session, admin_user, monkeypatch, "ERROR: Could not find a version that satisfies DESeq2"
        )
        observation = study.evidence_json["code_execution"]["observation"]
        assert "Could not find a version" in observation["transcript_tail"]

        text = await _report_text(session, study, admin_user)
        assert "dependencies would not install" in text

    @pytest.mark.asyncio
    async def test_no_generated_run_starts_behind_it(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        """Silently replacing `dependency_unresolvable` with a generated run that agrees would turn
        the single most useful finding this feature can produce into a false reproduction."""
        route, study, _ = await self._run(
            session, admin_user, monkeypatch, "ERROR: Could not find a version that satisfies DESeq2"
        )
        assert "generate" not in route.llm.calls
        assert study.evidence_json["code_execution"]["method"] == "authors_code"


class TestTheGeneratedArm:
    """Case 4: no usable code, generated fallback. One execution only, ranked last.

    Under the owner's ladder (2026-09-07) a wired template does not keep this arm from firing: "if no
    code exists, we attempt to reverse engineer from the methods section". So these studies register
    the deseq2 template like any other and the generated arm still wins, which is the assertion
    `test_a_wired_template_does_not_keep_the_paper_from_being_reproduced` exists to hold.
    """

    async def _run(self, session, admin_user, monkeypatch, **kw):
        route = _Route(monkeypatch, **kw)
        await route.enable_untrusted_execution(session)
        study = await _plan_ready_study(session, admin_user, code_availability=[])
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        state = await _tick_to_rest(session, study)
        return route, study, state

    @pytest.mark.asyncio
    async def test_a_wired_template_does_not_keep_the_paper_from_being_reproduced(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        """bioAF's template runs OUR analysis on their data. That is a different claim from
        reproducing THEIR analysis, so it is not a reason to skip the attempt."""
        _, study, state = await self._run(session, admin_user, monkeypatch)
        assert state == "classified"
        assert study.evidence_json["code_execution"]["method"] == "llm_from_methods"

    @pytest.mark.asyncio
    async def test_a_paper_with_no_code_still_gets_an_analysis(self, session, admin_user, monkeypatch, autonomous_org):
        _, study, state = await self._run(session, admin_user, monkeypatch)
        assert state == "classified"
        assert study.evidence_json["code_execution"]["method"] == "llm_from_methods"

    @pytest.mark.asyncio
    async def test_the_generated_source_and_its_assumptions_are_on_the_bundle(
        self, session, admin_user, monkeypatch, autonomous_org
    ):
        """A reader disputing the result has to be able to read exactly what ran."""
        _, study, _ = await self._run(session, admin_user, monkeypatch)
        source = study.evidence_json["code_execution"]["source"]
        assert source["generated_by_model"] == "claude-opus-4-8"
        assert any("FDR" in a for a in source["assumptions"])
        assert study.evidence_json["generated_analysis"]["source"].startswith("library(DESeq2)")

    @pytest.mark.asyncio
    async def test_it_generates_exactly_once(self, session, admin_user, monkeypatch, autonomous_org):
        """Repeating measures the generator, not the paper."""
        route, _, _ = await self._run(session, admin_user, monkeypatch)
        assert route.llm.calls.count("generate") == 1

    @pytest.mark.asyncio
    async def test_the_report_says_it_was_generated_rather_than_published(
        self, session, admin_user, monkeypatch, autonomous_org
    ):
        _, study, _ = await self._run(session, admin_user, monkeypatch)
        text = await _report_text(session, study, admin_user)
        assert "generated from the paper" in text
        assert "claude-opus-4-8" in text


class TestDiscoveryCouldNotEstablishACapability:
    """Case 5: UNKNOWN reaches the checklist and the issues section, and NO is never rendered for a
    timeout."""

    @pytest.mark.asyncio
    async def test_a_geo_outage_at_read_time_is_unknown_not_absent(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        from app.services.validation_driver_service import ValidationDriverService
        from app.services.validation_issue_service import ValidationIssueService

        route = _Route(monkeypatch)
        study = await _plan_ready_study(session, admin_user)

        async def _dead(url):
            raise RuntimeError("connection reset by peer")

        monkeypatch.setattr("app.services.literature.accession_manifest_service._http_fetch_text", _dead)
        monkeypatch.setattr("app.services.literature.deposit_inventory_service._http_fetch_text", _dead)
        plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
        await ValidationDriverService._discover_capabilities(session, study, plan, has_full_text=True)

        caps = study.evidence_json["capabilities"]
        assert caps["deposit_exists"]["value"] == "unknown"
        assert caps["preprocessed_data"]["value"] != "no"

        issues = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        assert any("data deposit" in i["step"] for i in issues)
        assert route.geo.byte_calls == []

    @pytest.mark.asyncio
    async def test_the_report_shows_unknown_with_its_reason(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        from app.services.validation_driver_service import ValidationDriverService

        _Route(monkeypatch)
        study = await _plan_ready_study(session, admin_user)

        async def _dead(url):
            raise RuntimeError("connection reset by peer")

        monkeypatch.setattr("app.services.literature.accession_manifest_service._http_fetch_text", _dead)
        monkeypatch.setattr("app.services.literature.deposit_inventory_service._http_fetch_text", _dead)
        plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
        await ValidationDriverService._discover_capabilities(session, study, plan, has_full_text=True)

        text = await _report_text(session, study, admin_user)
        assert "Unknown" in text
        assert "Data deposit exists" in text


class TestAnUnsupportedOutput:
    """Case 6: an execution produced an unsupported output. Reported as uncomparable with the output
    retained, NOT as `ran_no_output`."""

    @pytest.mark.asyncio
    async def test_output_bioaf_cannot_compare_is_named_as_our_limitation(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        route = _Route(
            monkeypatch,
            code_repo=_code_repo(),
            code_output="%PDF-1.4 a figure",
            code_output_name="figure3.pdf",
        )
        await route.enable_untrusted_execution(session)
        study = await _plan_ready_study(
            session, admin_user, code_availability=[{"kind": "github", "url": "https://github.com/lab/paper"}]
        )
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        state = await _tick_to_rest(session, study)

        assert state == "classified"
        assert study.evidence_json["code_execution"]["outcome"] == "ran_output_uncomparable"

        text = await _report_text(session, study, admin_user)
        assert "limitation of bioAF" in text

    @pytest.mark.asyncio
    async def test_it_is_not_reported_as_having_written_nothing(
        self, session, admin_user, monkeypatch, autonomous_org, deseq2_template
    ):
        """ "Wrote nothing" and "wrote something we cannot compare" are two different findings about
        a paper."""
        route = _Route(monkeypatch, code_repo=_code_repo(), code_output="%PDF-1.4", code_output_name="figure3.pdf")
        await route.enable_untrusted_execution(session)
        study = await _plan_ready_study(
            session, admin_user, code_availability=[{"kind": "github", "url": "https://github.com/lab/paper"}]
        )
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        await _tick_to_rest(session, study)
        assert study.evidence_json["code_execution"]["outcome"] != "ran_no_output"
