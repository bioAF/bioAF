"""A launch's parameters reach Nextflow as data, with exactly one authorized output location.

The head pod runs ``/bin/sh -c "cat /data/nextflow.config && nextflow run ..."``. The parameters
used to be joined into that string unquoted, so a value could run a shell command and a key or a
value could add a second ``--outdir``. These tests execute the generated command under a real
``/bin/sh`` with a recording ``nextflow`` on ``PATH`` and check the argv it received, then check
the launch boundary that refuses malformed and platform-owned keys before a run exists.

What Nextflow itself does with the argv was checked against ``nextflow/nextflow:25.10.4`` (see
plan 17 findings, D4a): ``--key value`` turns a value of ``--outdir`` into a second parameter,
``--key=value`` keeps it a string.
"""

from __future__ import annotations

import os
import subprocess

import pytest
from sqlalchemy import func, select

from app.adapters.compute.kubernetes import KubernetesComputeProvider
from app.adapters.job_specs import NextflowJobSpec
from app.exceptions import ValidationError
from app.models.pipeline_run import PipelineRun
from tests.test_launch_ownership import LAUNCH, PREFLIGHT, _auth, _launch, world  # noqa: F401

OUTDIR = "gs://bioaf-results-demo/experiments/3/pipeline-runs/7"


def _spec(parameters: dict, **kw) -> NextflowJobSpec:
    fields = dict(
        run_id=7,
        pipeline_key="nf-core/rnaseq",
        pipeline_source="https://github.com/nf-core/rnaseq",
        pipeline_version="3.14.0",
        parameters=parameters,
        sample_sheet="sample,fastq_1\nS1,s1.fq.gz\n",
        experiment_id=3,
    )
    fields.update(kw)
    return NextflowJobSpec(**fields)


def _execute(command: list[str], tmp_path) -> list[str]:
    """Run the pod command under /bin/sh with recording `cat` and `nextflow`; return nextflow's argv."""
    assert command[:2] == ["/bin/sh", "-c"]
    fake = tmp_path / "bin"
    fake.mkdir()
    record = tmp_path / "argv"
    (fake / "nextflow").write_text(f"#!/bin/sh\nprintf '%s\\0' \"$@\" > {record}\n")
    (fake / "cat").write_text("#!/bin/sh\nexit 0\n")
    for f in fake.iterdir():
        f.chmod(0o755)
    work = tmp_path / "cwd"
    work.mkdir()
    env = {"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(tmp_path)}
    subprocess.run(command, cwd=work, env=env, check=True, timeout=10)
    assert os.listdir(work) == [], f"the command had side effects: {os.listdir(work)}"
    return record.read_bytes().decode().split("\0")[:-1]


HOSTILE = {
    "sub": "$(touch PWNED)",
    "tick": "`touch PWNED2`",
    "semi": "x; touch PWNED3",
    "nl": "a\ntouch PWNED4",
    "flag": "--outdir gs://evil/",
    "dash": "-x",
    "quote": 'it\'s "quoted"',
    "space": "a b",
    "uni": "üñí",
    "neg": -5,
    "flt": 1.5,
    "yes": True,
}


class TestTheRenderedCommand:
    def test_every_value_stays_one_literal_argument(self, tmp_path):
        command = KubernetesComputeProvider._build_nextflow_command(_spec(HOSTILE), outdir=OUTDIR)
        argv = _execute(command, tmp_path)
        for key, value in HOSTILE.items():
            assert f"--{key}={value}" in argv
        assert [a for a in argv if a.startswith("--outdir")] == [f"--outdir={OUTDIR}"]

    def test_the_fixed_arguments_keep_their_order(self, tmp_path):
        command = KubernetesComputeProvider._build_nextflow_command(
            _spec({"genome": "GRCh38"}),
            outdir=OUTDIR,
            report_gcs_path="gs://raw/nextflow-reports/j 1/report.html",
            trace_gcs_path="gs://raw/nextflow-traces/j/trace.tsv",
        )
        argv = _execute(command, tmp_path)
        assert argv[:7] == [
            "run",
            "https://github.com/nf-core/rnaseq",
            "-r",
            "3.14.0",
            "-c",
            "/data/nextflow.config",
            "--input",
        ]
        assert argv[7] == "/data/samplesheet.csv"
        assert "gs://raw/nextflow-reports/j 1/report.html" in argv
        assert argv[argv.index("-with-trace") + 1] == "gs://raw/nextflow-traces/j/trace.tsv"
        assert argv[-1] == f"--outdir={OUTDIR}"

    @pytest.mark.parametrize("key", ["-x", "--outdir", "a b", "a;b", "outdir=gs://x", "x$(id)", "", "a\nb", "9lives"])
    def test_a_malformed_key_is_refused(self, key):
        with pytest.raises(ValidationError):
            KubernetesComputeProvider._build_nextflow_command(_spec({key: "v"}), outdir=OUTDIR)

    def test_input_is_platform_owned(self):
        with pytest.raises(ValidationError):
            KubernetesComputeProvider._build_nextflow_command(_spec({"input": "/etc/passwd"}), outdir=OUTDIR)

    def test_an_outdir_parameter_must_be_the_authorized_one(self, tmp_path):
        with pytest.raises(ValidationError):
            KubernetesComputeProvider._build_nextflow_command(_spec({"outdir": "gs://evil/"}), outdir=OUTDIR)
        command = KubernetesComputeProvider._build_nextflow_command(_spec({"outdir": OUTDIR + "/"}), outdir=OUTDIR)
        assert [a for a in _execute(command, tmp_path) if a.startswith("--outdir")] == [f"--outdir={OUTDIR}"]

    def test_internal_carriers_are_never_emitted(self, tmp_path):
        command = KubernetesComputeProvider._build_nextflow_command(
            _spec({"accessions": ["SRR1"], "fusion_enabled": True}), outdir=OUTDIR
        )
        argv = _execute(command, tmp_path)
        assert not [a for a in argv if a.startswith(("--accessions", "--fusion_enabled"))]

    @pytest.mark.parametrize("field", ["pipeline_source", "pipeline_version"])
    def test_a_source_or_version_that_reads_as_an_option_is_refused(self, field):
        with pytest.raises(ValidationError):
            KubernetesComputeProvider._build_nextflow_command(_spec({}, **{field: "-c/etc/x"}), outdir=OUTDIR)


class TestTheSubmittedOutputLocation:
    def _provider(self, cfg):
        provider = KubernetesComputeProvider()
        provider._cluster_config = cfg
        return provider

    def test_the_launch_writes_where_collection_reads(self, tmp_path):
        from app.adapters.storage.gcs import GcsStorageProvider
        from app.platform.run_output_layout import results_bucket_from_config, run_output_key

        cfg = {"raw_bucket_name": "bioaf-raw-demo"}
        storage = GcsStorageProvider(org_slug="demo")
        launch = self._provider(cfg)._resolve_nextflow_launch(_spec({}), "job", False, False, storage)
        expected = storage.build_uri(results_bucket_from_config(cfg), run_output_key(run_id=7, experiment_id=3))
        argv = _execute(list(launch.command), tmp_path)
        assert [a for a in argv if a.startswith("--outdir")] == [f"--outdir={expected}"]

    def test_a_replayed_spec_naming_another_outdir_is_refused(self):
        from app.adapters.storage.gcs import GcsStorageProvider

        provider = self._provider({"results_bucket_name": "bioaf-results-demo"})
        with pytest.raises(ValidationError):
            provider._resolve_nextflow_launch(
                _spec({"outdir": "gs://other/"}), "job", False, False, GcsStorageProvider(org_slug="demo")
            )


async def _runs(session) -> int:
    return (await session.execute(select(func.count()).select_from(PipelineRun))).scalar_one()


class TestTheLaunchBoundary:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "parameters",
        [
            {"outdir": "gs://other-bucket/"},
            {"input": "/etc/passwd"},
            {"a;touch x": "1"},
            {"-resume": "true"},
            {"outdir=gs://x": "1"},
        ],
    )
    async def test_refused_before_a_run_exists(self, client, session, admin_token, world, parameters):  # noqa: F811
        before = await _runs(session)
        captured: list = []
        body = {"pipeline_key": "nf-core/rnaseq", "experiment_id": world["exp"].id, "parameters": parameters}
        r = await _launch(client, admin_token, body, captured)
        assert r.status_code == 400, r.text
        assert captured == []
        assert await _runs(session) == before

    @pytest.mark.asyncio
    async def test_preflight_reports_the_same_refusal(self, client, admin_token, world):  # noqa: F811
        body = {
            "pipeline_key": "nf-core/rnaseq",
            "experiment_id": world["exp"].id,
            "parameters": {"outdir": "gs://other-bucket/"},
        }
        r = await client.post(PREFLIGHT, json=body, headers=_auth(admin_token))
        assert r.status_code == 200
        assert r.json()["can_launch"] is False

    @pytest.mark.asyncio
    async def test_ordinary_parameters_still_launch(self, client, admin_token, world):  # noqa: F811
        captured: list = []
        body = {
            "pipeline_key": "nf-core/rnaseq",
            "experiment_id": world["exp"].id,
            "parameters": {"strandedness": "forward", "skip_qc": True, "min_reads": -1, "note": "a b; c"},
        }
        r = await _launch(client, admin_token, body, captured)
        assert r.status_code == 200, r.text
        assert captured[0].parameters["note"] == "a b; c"


def test_launch_constant_is_shared():
    assert LAUNCH
