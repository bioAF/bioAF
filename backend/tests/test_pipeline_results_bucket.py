"""Stage 2b: explicit results-bucket resolution + fail-closed outdir.

Fixes the (!)D/(!)E findings: stop deriving the pipeline results bucket purely by
the bioaf-raw- -> bioaf-results- string swap (resolve results_bucket_name
explicitly first, derive only as a compat fallback), and refuse to launch when no
durable results bucket can be resolved instead of silently writing outputs to a
pod-local /data/results that is destroyed at pod cleanup.
"""

from __future__ import annotations

import pytest

from app.adapters.compute.kubernetes import KubernetesComputeProvider, _resolve_results_bucket

# --- _resolve_results_bucket: explicit results_bucket_name first, then derive ----


def test_results_bucket_prefers_explicit_results_bucket_name():
    cfg = {"results_bucket_name": "my-results", "raw_bucket_name": "bioaf-raw-x"}
    assert _resolve_results_bucket(cfg) == "my-results"


def test_results_bucket_ignores_null_sentinel():
    cfg = {"results_bucket_name": "null", "raw_bucket_name": "bioaf-raw-x"}
    assert _resolve_results_bucket(cfg) == "bioaf-results-x"


def test_results_bucket_derives_from_raw_when_explicit_absent():
    assert _resolve_results_bucket({"raw_bucket_name": "bioaf-raw-demo"}) == "bioaf-results-demo"


def test_results_bucket_none_when_raw_not_bioaf_prefixed():
    assert _resolve_results_bucket({"raw_bucket_name": "some-bucket"}) is None


def test_results_bucket_none_when_config_empty():
    assert _resolve_results_bucket({}) is None


# --- _durable_outdir: the run's authorized location, or fail closed (no silent /data/results) ----


def _provider(cfg):
    p = KubernetesComputeProvider()
    p._cluster_config = cfg
    return p


def test_a_stated_outdir_cannot_redirect_the_run():
    """The run's outdir is its authorized location; the renderer refuses any other (plan 17, D3)."""
    from app.adapters.job_specs import NextflowJobSpec
    from app.exceptions import ValidationError

    spec = NextflowJobSpec(
        run_id=7,
        pipeline_key="k",
        pipeline_source="src",
        pipeline_version="1",
        parameters={"outdir": "gs://user/explicit"},
        sample_sheet="",
        experiment_id=3,
    )
    outdir = _provider({"results_bucket_name": "bioaf-results-demo"})._durable_outdir(3, 7)
    with pytest.raises(ValidationError):
        KubernetesComputeProvider._build_nextflow_command(spec, outdir=outdir)


def test_durable_outdir_from_explicit_results_bucket():
    out = _provider({"results_bucket_name": "bioaf-results-demo"})._durable_outdir(3, 7)
    assert out == "gs://bioaf-results-demo/experiments/3/pipeline-runs/7"


def test_durable_outdir_derives_results_bucket_from_raw():
    out = _provider({"raw_bucket_name": "bioaf-raw-demo"})._durable_outdir(3, 7)
    assert out == "gs://bioaf-results-demo/experiments/3/pipeline-runs/7"


def test_durable_outdir_fails_closed_when_no_results_bucket():
    with pytest.raises(RuntimeError):
        _provider({})._durable_outdir(3, 7)


def test_durable_outdir_fails_closed_without_an_experiment():
    with pytest.raises(RuntimeError):
        _provider({"results_bucket_name": "r"})._durable_outdir(None, 7)
