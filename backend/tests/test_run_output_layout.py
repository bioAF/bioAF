"""Where a pipeline run's outputs live, and which URIs belong to that place.

The launcher writes a run's outputs, the monitor and the registration CLI collect them, the
validation driver ingests fetched FASTQ from them, and the validation reaper deletes them. Each
of those used its own copy of "where", and each trusted a stored ``outdir`` it had never checked,
so a launch parameter could point collection at another bucket and the reaper at another run.
These tests pin the one shared answer and the membership rule every consumer applies.
"""

from __future__ import annotations

import pytest

from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.nfs import NfsStorageProvider
from app.adapters.storage.s3 import S3StorageProvider
from app.platform.run_output_layout import (
    directory_prefix,
    results_bucket_from_config,
    run_output_key,
    uri_within,
)


class TestResultsBucket:
    def test_the_configured_results_bucket_wins(self):
        assert results_bucket_from_config({"results_bucket_name": "r", "raw_bucket_name": "bioaf-raw-x"}) == "r"

    def test_it_is_derived_from_a_bioaf_raw_bucket_when_unset(self):
        assert results_bucket_from_config({"raw_bucket_name": "bioaf-raw-acme"}) == "bioaf-results-acme"
        assert results_bucket_from_config({"results_bucket_name": "null", "raw_bucket_name": "bioaf-raw-a"}) == (
            "bioaf-results-a"
        )

    def test_nothing_resolves_to_none(self):
        assert results_bucket_from_config({}) is None
        assert results_bucket_from_config({"raw_bucket_name": "custom-raw"}) is None


class TestRunOutputKey:
    def test_an_experiment_run(self):
        assert run_output_key(run_id=7, experiment_id=3) == "experiments/3/pipeline-runs/7"

    def test_a_project_scoped_run(self):
        assert run_output_key(run_id=7, experiment_id=None, project_id=5) == "projects/5/pipeline-runs/7"

    def test_the_experiment_wins_when_both_are_set(self):
        assert run_output_key(run_id=7, experiment_id=3, project_id=5) == "experiments/3/pipeline-runs/7"

    @pytest.mark.parametrize("run_id", [None, 0, -1])
    def test_a_run_without_an_id_has_no_location(self, run_id):
        with pytest.raises(ValueError):
            run_output_key(run_id=run_id, experiment_id=3)

    def test_a_run_without_an_owner_has_no_location(self):
        with pytest.raises(ValueError):
            run_output_key(run_id=7, experiment_id=None, project_id=None)


def test_directory_prefix_is_separator_terminated():
    assert directory_prefix("gs://b/nextflow-work/run-1") == "gs://b/nextflow-work/run-1/"
    assert directory_prefix("gs://b/nextflow-work/run-1/") == "gs://b/nextflow-work/run-1/"


def _adapters(tmp_path):
    return [
        GcsStorageProvider(org_slug="t"),
        S3StorageProvider(org_slug="t"),
        NfsStorageProvider(root=str(tmp_path)),
    ]


def _uri(storage, bucket, key):
    return storage.build_uri(bucket, key)


class TestUriWithin:
    def test_objects_under_the_directory_belong(self, tmp_path):
        for s in _adapters(tmp_path):
            prefix = _uri(s, "res", "experiments/3/pipeline-runs/1")
            assert uri_within(s, _uri(s, "res", "experiments/3/pipeline-runs/1/fastq/a.fq.gz"), prefix)
            assert uri_within(s, _uri(s, "res", "experiments/3/pipeline-runs/1/x"), prefix + "/")

    def test_a_neighbouring_run_does_not(self, tmp_path):
        for s in _adapters(tmp_path):
            prefix = _uri(s, "res", "nextflow-work/run-1")
            for neighbour in ("run-10/x", "run-100/x", "run-11/x", "run-1x"):
                assert not uri_within(s, _uri(s, "res", f"nextflow-work/{neighbour}"), prefix)

    def test_another_bucket_or_scheme_does_not(self, tmp_path):
        gcs, s3, nfs = _adapters(tmp_path)
        prefix = "gs://res/experiments/3/pipeline-runs/1"
        assert not uri_within(gcs, "gs://other/experiments/3/pipeline-runs/1/a", prefix)
        assert not uri_within(gcs, "gs://res-evil/experiments/3/pipeline-runs/1/a", prefix)
        assert not uri_within(gcs, "s3://res/experiments/3/pipeline-runs/1/a", prefix)
        assert not uri_within(s3, "gs://res/experiments/3/pipeline-runs/1/a", prefix)

    @pytest.mark.parametrize(
        "key",
        [
            "experiments/3/pipeline-runs/1/../2/a",
            "experiments/3/pipeline-runs/1/./a",
            "experiments/3/pipeline-runs/1//a",
            "experiments/3/pipeline-runs/1/fastq/..",
        ],
    )
    def test_dot_and_empty_segments_never_belong(self, tmp_path, key):
        for s in _adapters(tmp_path):
            assert not uri_within(s, _uri(s, "res", key), _uri(s, "res", "experiments/3/pipeline-runs/1"))

    def test_the_directory_itself_is_not_an_object_in_it(self, tmp_path):
        for s in _adapters(tmp_path):
            prefix = _uri(s, "res", "experiments/3/pipeline-runs/1")
            assert not uri_within(s, prefix, prefix)
            assert not uri_within(s, prefix + "/", prefix)

    def test_unparseable_input_does_not_belong(self, tmp_path):
        for s in _adapters(tmp_path):
            assert not uri_within(s, "/data/results/x", _uri(s, "res", "a"))
            assert not uri_within(s, "", _uri(s, "res", "a"))
            assert not uri_within(s, None, _uri(s, "res", "a"))
