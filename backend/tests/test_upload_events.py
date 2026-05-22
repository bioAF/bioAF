from app.services.event_types import DATA_UPLOADED
from app.services.upload_service import UploadService


def test_data_uploaded_event_carries_experiment_id_when_scoped():
    """A file uploaded into an experiment carries experiment_id so the
    notification can deep-link to that experiment's Files tab."""
    payload = UploadService._data_uploaded_event(
        org_id=1,
        user_id=2,
        file_id=10,
        filename="sample_R1.fastq.gz",
        file_type="fastq",
        experiment_id=55,
    )
    assert payload["event_type"] == DATA_UPLOADED
    assert payload["entity_type"] == "file"
    assert payload["entity_id"] == 10
    assert payload["metadata"]["experiment_id"] == 55


def test_data_uploaded_event_omits_experiment_id_when_global():
    """A standalone upload has no experiment, so the notification falls back to
    the Data & Files page."""
    payload = UploadService._data_uploaded_event(
        org_id=1,
        user_id=2,
        file_id=10,
        filename="reference.fa",
        file_type="fasta",
        experiment_id=None,
    )
    assert "experiment_id" not in payload["metadata"]
