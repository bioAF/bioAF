"""Pipeline outputs must be discoverable from the experiment they ran on.

Outputs are persisted by PipelineOutputService.register_outputs which
attaches them to the run's experiment_id and creates sample_files links
for every sample in the run. Combined with FileService.list_files
inheritance, a search at the experiment OR sample level should surface
those outputs.
"""

import pytest

from app.services.file_service import FileService
from app.services.pipeline_output_service import PipelineOutputService


@pytest.mark.asyncio
async def test_pipeline_outputs_appear_under_experiment_and_sample(session, admin_user):
    from app.models.experiment import Experiment
    from app.models.pipeline_run import PipelineRun, PipelineRunSample
    from app.models.sample import Sample

    org_id = admin_user.organization_id

    exp = Experiment(
        organization_id=org_id,
        name="Pipeline Output Exp",
        owner_user_id=admin_user.id,
        status="processing",
    )
    session.add(exp)
    await session.flush()

    samples = [Sample(experiment_id=exp.id, status="registered") for _ in range(3)]
    session.add_all(samples)
    await session.flush()

    run = PipelineRun(
        organization_id=org_id,
        experiment_id=exp.id,
        pipeline_name="cpsam",
        status="complete",
        submitted_by_user_id=admin_user.id,
    )
    session.add(run)
    await session.flush()

    for s in samples:
        session.add(PipelineRunSample(pipeline_run_id=run.id, sample_id=s.id))
    await session.flush()
    await session.commit()

    collected = [
        {"filename": "Output1.txt", "gcs_uri": "gs://bucket/run/Output1.txt", "size_bytes": 100, "md5_hash": "a"},
        {"filename": "Output2.html", "gcs_uri": "gs://bucket/run/Output2.html", "size_bytes": 200, "md5_hash": "b"},
        {"filename": "Output3.h5ad", "gcs_uri": "gs://bucket/run/Output3.h5ad", "size_bytes": 300, "md5_hash": "c"},
    ]
    created = await PipelineOutputService.register_outputs(session, run, collected)
    await session.commit()
    assert len(created) == 3

    files, total = await FileService.list_files(session, org_id, experiment_id=exp.id)
    names = {f.filename for f in files}
    assert {"Output1.txt", "Output2.html", "Output3.h5ad"}.issubset(names)
    assert total >= 3

    files, _ = await FileService.list_files(session, org_id, sample_id=samples[0].id)
    names = {f.filename for f in files}
    assert {"Output1.txt", "Output2.html", "Output3.h5ad"}.issubset(names)
