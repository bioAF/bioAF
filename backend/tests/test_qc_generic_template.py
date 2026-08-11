"""The `generic` QC template: the storage half of the generic MultiQC engine.

Locates every multiqc_data.json a run wrote (nf-core nests them by aligner or
analysis branch), parses them with the shared registry, merges, and caches. This
is what a pipeline type with no tailored template resolves to, so its job is to
produce real numbers or an honest nothing, never another type's shape.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.adapters.models import StorageObjectNotFound
from app.services.qc.templates import generic

FIXTURES = Path(__file__).parent / "fixtures" / "multiqc"


class FakeStorage:
    """Minimal stand-in for the storage adapter surface extract() touches."""

    def __init__(self, objects: dict[str, str]):
        self.objects = dict(objects)
        self.writes: dict[str, str] = {}

    def build_uri(self, bucket: str, path: str) -> str:
        return f"gs://{bucket}/{path}"

    async def list_objects(self, uri: str):
        return [
            SimpleNamespace(storage_uri=key, size_bytes=len(value))
            for key, value in self.objects.items()
            if key.startswith(uri)
        ]

    async def read_text(self, uri: str) -> str:
        if uri not in self.objects:
            raise StorageObjectNotFound(uri)
        return self.objects[uri]

    async def write_text(self, uri: str, text: str, content_type: str | None = None) -> None:
        self.writes[uri] = text
        self.objects[uri] = text


BUCKET = "results-bucket"
RUN = SimpleNamespace(id=42, experiment_id=7)
PREFIX = f"gs://{BUCKET}/experiments/7/pipeline-runs/42/"


def _fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def storage(monkeypatch):
    def _install(objects: dict[str, str]) -> FakeStorage:
        adapter = FakeStorage(objects)
        monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: adapter)
        return adapter

    return _install


# --------------------------------------------------------------------------
# Template contract
# --------------------------------------------------------------------------


def test_exposes_the_template_contract():
    """The dashboard service dispatches on these attributes."""
    for attribute in (
        "EMPTY_METRICS",
        "MULTIQC_PLOTS",
        "render_config",
        "compute_quality",
        "generate_summary",
        "extract",
    ):
        assert hasattr(generic, attribute), attribute


def test_render_config_is_labelled_generic_not_scrnaseq():
    config = generic.render_config()

    assert config["template"] == "generic"
    assert config["sections"]


def test_render_config_satisfies_the_dashboard_response_schema():
    """The config is served straight back through QCDashboardConfig, so a shape
    the schema rejects 500s the dashboard endpoint rather than failing here."""
    from app.schemas.qc_dashboard import QCDashboardConfig

    config = QCDashboardConfig(**generic.render_config())

    assert config.template == "generic"
    assert all(plot.file_glob for plot in config.plots)


def test_every_metric_the_engine_emits_has_a_display_label():
    """A metric with no spec renders as a bare key, which is how a dashboard
    ends up showing `reads_mapped_genome_unique` to a scientist."""
    from app.services.qc.multiqc_registry import GENERIC_METRIC_KEYS

    labelled = set(generic.render_config()["metrics"])

    assert set(GENERIC_METRIC_KEYS) <= labelled


def test_quality_is_not_invented_without_metrics():
    """The generic engine has no type-specific thresholds, so it must not
    manufacture a pass/fail verdict."""
    assert generic.compute_quality(dict(generic.EMPTY_METRICS)) == "pending_review"


def test_summary_states_plainly_when_nothing_was_found():
    summary = generic.generate_summary(dict(generic.EMPTY_METRICS))

    assert "no standard qc metrics" in summary.lower()


def test_summary_reports_depth_and_sample_count_when_present():
    summary = generic.generate_summary({**generic.EMPTY_METRICS, "total_samples": 4, "total_sequences": 6_677_908})

    assert "4" in summary
    assert "6,677,908" in summary


# --------------------------------------------------------------------------
# Locating the report
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finds_a_nested_multiqc_report(storage):
    """nf-core/rnaseq writes multiqc/star_salmon/multiqc_report_data/, chipseq
    writes multiqc/broad_peak/multiqc_data/. Neither is flat."""
    storage(
        {f"{PREFIX}multiqc/star_salmon/multiqc_report_data/multiqc_data.json": _fixture_text("bulk_rnaseq_run17.json")}
    )

    metrics = await generic.extract(None, RUN, results_bucket=BUCKET)

    assert metrics["total_sequences"] == 6_677_908


@pytest.mark.asyncio
async def test_returns_empty_metrics_when_no_report_exists(storage):
    storage({f"{PREFIX}some_other_output.txt": "irrelevant"})

    metrics = await generic.extract(None, RUN, results_bucket=BUCKET)

    assert metrics == dict(generic.EMPTY_METRICS)


@pytest.mark.asyncio
async def test_malformed_report_does_not_raise(storage):
    storage({f"{PREFIX}multiqc/multiqc_data/multiqc_data.json": "{not json"})

    metrics = await generic.extract(None, RUN, results_bucket=BUCKET)

    assert metrics["total_sequences"] is None


@pytest.mark.asyncio
async def test_missing_results_bucket_yields_empty_metrics(storage):
    storage({})

    metrics = await generic.extract(None, RUN, results_bucket="")

    assert metrics == dict(generic.EMPTY_METRICS)


# --------------------------------------------------------------------------
# Merging several reports
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merges_multiple_reports_preferring_the_wider_one(storage):
    """A run can emit several MultiQC reports (one per analysis branch). The
    report covering more samples is the better source for a shared key."""
    narrow = json.loads(_fixture_text("bulk_rnaseq_run17.json"))
    narrow_samples = dict(list(narrow["report_saved_raw_data"]["multiqc_fastqc"].items())[:1])
    narrow["report_saved_raw_data"]["multiqc_fastqc"] = narrow_samples

    storage(
        {
            f"{PREFIX}multiqc/a_branch/multiqc_data/multiqc_data.json": json.dumps(narrow),
            f"{PREFIX}multiqc/b_branch/multiqc_data/multiqc_data.json": _fixture_text("bulk_rnaseq_run17.json"),
        }
    )

    metrics = await generic.extract(None, RUN, results_bucket=BUCKET)

    assert metrics["total_samples"] == 4


@pytest.mark.asyncio
async def test_merge_is_independent_of_listing_order(storage):
    """Object listing order is not guaranteed, so the verdict must not depend
    on it."""
    reports = {
        f"{PREFIX}multiqc/z_branch/multiqc_data/multiqc_data.json": _fixture_text("chipseq_run22.json"),
        f"{PREFIX}multiqc/a_branch/multiqc_data/multiqc_data.json": _fixture_text("atacseq_run24.json"),
    }

    storage(reports)
    forward = await generic.extract(None, RUN, results_bucket=BUCKET, skip_cache=True)

    storage(dict(reversed(list(reports.items()))))
    backward = await generic.extract(None, RUN, results_bucket=BUCKET, skip_cache=True)

    assert forward == backward


@pytest.mark.asyncio
async def test_a_report_contributes_keys_the_other_lacks(storage):
    """Merging is per key, not whole-report: a branch that alone carries a
    metric still supplies it."""
    storage(
        {
            f"{PREFIX}multiqc/a/multiqc_data/multiqc_data.json": _fixture_text("chipseq_run22.json"),
            f"{PREFIX}multiqc/b/multiqc_data/multiqc_data.json": _fixture_text("bulk_rnaseq_run17.json"),
        }
    )

    metrics = await generic.extract(None, RUN, results_bucket=BUCKET)

    # Only the rnaseq report has STAR.
    assert metrics["reads_mapped_genome_unique"] is not None


# --------------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uses_the_cached_metrics_when_present(storage):
    adapter = storage(
        {
            f"{PREFIX}qc_metrics.json": json.dumps({**generic.EMPTY_METRICS, "total_sequences": 123}),
            f"{PREFIX}multiqc/multiqc_data/multiqc_data.json": _fixture_text("bulk_rnaseq_run17.json"),
        }
    )

    metrics = await generic.extract(None, RUN, results_bucket=BUCKET)

    assert metrics["total_sequences"] == 123
    assert not adapter.writes


@pytest.mark.asyncio
async def test_skip_cache_reparses_and_rewrites(storage):
    adapter = storage(
        {
            f"{PREFIX}qc_metrics.json": json.dumps({**generic.EMPTY_METRICS, "total_sequences": 123}),
            f"{PREFIX}multiqc/multiqc_data/multiqc_data.json": _fixture_text("bulk_rnaseq_run17.json"),
        }
    )

    metrics = await generic.extract(None, RUN, results_bucket=BUCKET, skip_cache=True)

    assert metrics["total_sequences"] == 6_677_908
    assert f"{PREFIX}qc_metrics.json" in adapter.writes


@pytest.mark.asyncio
async def test_nothing_parsed_is_not_cached(storage):
    """Caching an empty result would freeze a transient miss into the run."""
    adapter = storage({f"{PREFIX}multiqc/multiqc_data/multiqc_data.json": "{not json"})

    await generic.extract(None, RUN, results_bucket=BUCKET)

    assert not adapter.writes


# --------------------------------------------------------------------------
# The point of the whole exercise
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_produces_metrics_for_a_report_the_scrnaseq_parser_cannot_read(storage):
    """MultiQC 1.31 output: the scrnaseq template returns all-null on this exact
    report. An unmapped pipeline type must do better than that."""
    storage({f"{PREFIX}multiqc/multiqc_data/multiqc_data.json": _fixture_text("scrnaseq_run11.json")})

    metrics = await generic.extract(None, RUN, results_bucket=BUCKET)

    # One sample across four files (two lanes x two mates); depth is per sample.
    assert metrics["total_sequences"] == 66_601_887
    assert metrics["total_samples"] == 1
    assert metrics["reads_mapped_genome_unique"] == 0.8755
    assert metrics["percent_gc"] == 46.0
