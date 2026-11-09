"""The one mapping from a Sample row to its ``SampleResponse`` wire shape.

The sample must be loaded through ``SampleService.get_sample`` or
``SampleService.list_samples``: both eager-load the batch summaries and custom
fields read here, so building a response issues no SQL.

``file_count`` is not derived from the row. Only the experiment sample listing
loads files and reports their count; detail, create and the update routes
report zero, which is the established wire value for them.
"""

from app.schemas.sample import SampleResponse


def sample_response(s, *, file_count: int = 0) -> SampleResponse:
    return SampleResponse(
        id=s.id,
        external_id=s.external_id,
        relabeled_from=s.relabeled_from,
        organism=s.organism,
        tissue_type=s.tissue_type,
        donor_source=s.donor_source,
        treatment_condition=s.treatment_condition,
        chemistry_version=s.chemistry_version,
        sample_batch={"id": s.sample_batch.id, "name": s.sample_batch.name} if s.sample_batch else None,
        sequencing_batch={"id": s.sequencing_batch.id, "code": s.sequencing_batch.code} if s.sequencing_batch else None,
        sequencing_batch_position=s.sequencing_batch_position,
        viability_pct=float(s.viability_pct) if s.viability_pct is not None else None,
        cell_count=s.cell_count,
        prep_notes=s.prep_notes,
        molecule_type=s.molecule_type,
        library_prep_method=s.library_prep_method,
        library_layout=s.library_layout,
        assay=s.assay,
        qc_status=s.qc_status,
        qc_notes=s.qc_notes,
        parent_sample_id=s.parent_sample_id,
        collection_timestamp=s.collection_timestamp,
        collection_method=s.collection_method,
        file_count=file_count,
        status=s.status,
        created_at=s.created_at,
        updated_at=s.updated_at,
        custom_fields=list(s.custom_fields or []),
    )
