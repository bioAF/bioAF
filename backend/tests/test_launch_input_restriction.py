"""An internal caller can narrow a launch to an exact set of input files; it never widens them."""

from types import SimpleNamespace

from app.services.pipeline_run_service import PipelineRunService


def _file(id_, org=1, source_type="upload"):
    return SimpleNamespace(
        id=id_,
        organization_id=org,
        source_type=source_type,
        deleted_at=None,
        filename=f"f{id_}.fastq.gz",
        storage_uri=f"s3://b/f{id_}",
        tags_json=[],
        file_type="fastq",
    )


def _sample(id_, files):
    return SimpleNamespace(
        id=id_,
        uuid=None,
        external_id=f"S{id_}",
        prep_notes=None,
        donor_source=None,
        organism=None,
        files=files,
        custom_fields=[],
    )


def _ids(sheet):
    return {s.id: sorted(f.id for f in s.input_files) for s in sheet}


def test_only_narrows_each_samples_files():
    samples = [_sample(1, [_file(10), _file(11)]), _sample(2, [_file(20)])]
    sheet = PipelineRunService._sheet_inputs(samples, include_derived=False, org_id=1, only={10, 20})
    assert _ids(sheet) == {1: [10], 2: [20]}


def test_only_never_widens_past_eligibility():
    samples = [_sample(1, [_file(10, org=2), _file(11, source_type="pipeline_output")])]
    sheet = PipelineRunService._sheet_inputs(samples, include_derived=False, org_id=1, only={10, 11})
    assert _ids(sheet) == {1: []}


def test_no_restriction_keeps_every_eligible_file():
    samples = [_sample(1, [_file(10), _file(11)])]
    assert _ids(PipelineRunService._sheet_inputs(samples, include_derived=False, org_id=1)) == {1: [10, 11]}
