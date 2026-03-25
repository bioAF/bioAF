from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.audit import AuditLogEntry, AuditLogResponse
from app.schemas.experiment import (
    CustomFieldResponse,
    ExperimentCreate,
    ExperimentDetailResponse,
    ExperimentListResponse,
    ExperimentResponse,
    ExperimentStatusUpdate,
    ExperimentUpdate,
    FieldDefaultResponse,
    ProjectSummary,
    SampleResponseBrief,
    BatchResponseBrief,
    UserSummary,
)
from app.schemas.sample import SampleBulkCreate, SampleCreate, SampleResponse
from app.schemas.batch import BatchCreate, BatchResponse
from app.services.experiment_service import ExperimentService
from app.services.sample_service import SampleService
from app.services.batch_service import BatchService
from app.services.csv_service import parse_sample_csv

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


def _user_summary(user) -> UserSummary | None:
    if not user:
        return None
    return UserSummary(id=user.id, name=user.name, email=user.email)


def _experiment_response(exp) -> ExperimentResponse:
    return ExperimentResponse(
        id=exp.id,
        name=exp.name,
        project=ProjectSummary(id=exp.project.id, name=exp.project.name) if exp.project else None,
        template_id=exp.template_id,
        template_name=exp.template.name if exp.template else None,
        hypothesis=exp.hypothesis,
        description=exp.description,
        status=exp.status,
        start_date=exp.start_date,
        expected_sample_count=exp.expected_sample_count,
        owner=_user_summary(exp.owner),
        design_type=exp.design_type,
        protocol_version=exp.protocol_version,
        variables_json=exp.variables_json,
        sample_count=len(exp.samples) if exp.samples else 0,
        batch_count=len(exp.batches) if exp.batches else 0,
        created_at=exp.created_at,
        updated_at=exp.updated_at,
    )


@router.get("", response_model=ExperimentListResponse)
async def list_experiments(
    request: Request,
    page: int = 1,
    page_size: int = 25,
    project_id: int | None = None,
    status: str | None = None,
    owner_user_id: int | None = None,
    search: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    experiments, total = await ExperimentService.list_experiments(
        session,
        org_id,
        page=page,
        page_size=page_size,
        project_id=project_id,
        status=status,
        owner_user_id=owner_user_id,
        search=search,
    )

    return ExperimentListResponse(
        experiments=[_experiment_response(e) for e in experiments],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ExperimentResponse)
async def create_experiment(
    body: ExperimentCreate,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    experiment = await ExperimentService.create_experiment(session, org_id, user_id, body)
    await session.commit()

    # Reload with relationships
    experiment = await ExperimentService.get_experiment(session, experiment.id, org_id)
    return _experiment_response(experiment)


@router.get("/{experiment_id}", response_model=ExperimentDetailResponse)
async def get_experiment(
    experiment_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    experiment = await ExperimentService.get_experiment(session, experiment_id, org_id)
    if not experiment:
        raise HTTPException(404, "Experiment not found")

    _, audit_count = await ExperimentService.get_audit_trail(session, experiment_id, org_id, page=1, page_size=1)

    return ExperimentDetailResponse(
        id=experiment.id,
        name=experiment.name,
        project=ProjectSummary(id=experiment.project.id, name=experiment.project.name) if experiment.project else None,
        template_id=experiment.template_id,
        template_name=experiment.template.name if experiment.template else None,
        hypothesis=experiment.hypothesis,
        description=experiment.description,
        status=experiment.status,
        start_date=experiment.start_date,
        expected_sample_count=experiment.expected_sample_count,
        owner=_user_summary(experiment.owner),
        sample_count=len(experiment.samples),
        batch_count=len(experiment.batches),
        created_at=experiment.created_at,
        updated_at=experiment.updated_at,
        samples=[
            SampleResponseBrief(
                id=s.id,
                sample_id_external=s.sample_id_external,
                organism=s.organism,
                tissue_type=s.tissue_type,
                molecule_type=s.molecule_type,
                library_prep_method=s.library_prep_method,
                library_layout=s.library_layout,
                qc_status=s.qc_status,
                status=s.status,
                created_at=s.created_at,
            )
            for s in experiment.samples
        ],
        batches=[
            BatchResponseBrief(
                id=b.id,
                name=b.name,
                instrument_model=b.instrument_model,
                instrument_platform=b.instrument_platform,
                sample_count=len([s for s in experiment.samples if s.batch_id == b.id]),
                created_at=b.created_at,
            )
            for b in experiment.batches
        ],
        custom_fields=[
            CustomFieldResponse(
                id=cf.id,
                field_name=cf.field_name,
                field_value=cf.field_value,
                field_type=cf.field_type,
            )
            for cf in experiment.custom_fields
        ],
        field_defaults=[
            FieldDefaultResponse(
                id=fd.id,
                field_name=fd.field_name,
                default_value=fd.default_value,
                is_required=fd.is_required,
            )
            for fd in experiment.field_defaults
        ],
        audit_trail_count=audit_count,
    )


@router.patch("/{experiment_id}", response_model=ExperimentResponse)
async def update_experiment(
    experiment_id: int,
    body: ExperimentUpdate,
    current_user: dict = require_permission("experiments", "edit"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    experiment = await ExperimentService.update_experiment(session, experiment_id, org_id, user_id, body)
    if not experiment:
        raise HTTPException(404, "Experiment not found")

    await session.commit()
    experiment = await ExperimentService.get_experiment(session, experiment_id, org_id)
    return _experiment_response(experiment)


@router.patch("/{experiment_id}/status", response_model=ExperimentResponse)
async def update_experiment_status(
    experiment_id: int,
    body: ExperimentStatusUpdate,
    current_user: dict = require_permission("experiments", "change_status"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    experiment = await ExperimentService.update_status(session, experiment_id, org_id, user_id, body.status)
    await session.commit()
    experiment = await ExperimentService.get_experiment(session, experiment_id, org_id)
    return _experiment_response(experiment)


@router.get("/{experiment_id}/samples")
async def list_experiment_samples(
    experiment_id: int,
    request: Request,
    batch_id: int | None = None,
    qc_status: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    samples = await SampleService.list_samples(
        session, experiment_id, batch_id=batch_id, qc_status=qc_status, status=status
    )
    return [
        SampleResponse(
            id=s.id,
            sample_id_external=s.sample_id_external,
            organism=s.organism,
            tissue_type=s.tissue_type,
            donor_source=s.donor_source,
            treatment_condition=s.treatment_condition,
            chemistry_version=s.chemistry_version,
            batch={"id": s.batch.id, "name": s.batch.name} if s.batch else None,
            viability_pct=float(s.viability_pct) if s.viability_pct is not None else None,
            cell_count=s.cell_count,
            prep_notes=s.prep_notes,
            molecule_type=s.molecule_type,
            library_prep_method=s.library_prep_method,
            library_layout=s.library_layout,
            qc_status=s.qc_status,
            qc_notes=s.qc_notes,
            parent_sample_id=s.parent_sample_id,
            collection_timestamp=s.collection_timestamp,
            collection_method=s.collection_method,
            status=s.status,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in samples
    ]


@router.post("/{experiment_id}/samples", response_model=SampleResponse)
async def create_sample(
    experiment_id: int,
    body: SampleCreate,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    sample = await SampleService.create_sample(session, experiment_id, user_id, body)
    await session.commit()

    sample = await SampleService.get_sample(session, sample.id)
    return SampleResponse(
        id=sample.id,
        sample_id_external=sample.sample_id_external,
        organism=sample.organism,
        tissue_type=sample.tissue_type,
        donor_source=sample.donor_source,
        treatment_condition=sample.treatment_condition,
        chemistry_version=sample.chemistry_version,
        batch={"id": sample.batch.id, "name": sample.batch.name} if sample.batch else None,
        viability_pct=float(sample.viability_pct) if sample.viability_pct is not None else None,
        cell_count=sample.cell_count,
        prep_notes=sample.prep_notes,
        molecule_type=sample.molecule_type,
        library_prep_method=sample.library_prep_method,
        library_layout=sample.library_layout,
        qc_status=sample.qc_status,
        qc_notes=sample.qc_notes,
        parent_sample_id=sample.parent_sample_id,
        collection_timestamp=sample.collection_timestamp,
        collection_method=sample.collection_method,
        status=sample.status,
        created_at=sample.created_at,
        updated_at=sample.updated_at,
    )


@router.post("/{experiment_id}/samples/bulk")
async def bulk_create_samples(
    experiment_id: int,
    body: SampleBulkCreate,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    samples = await SampleService.bulk_create_samples(session, experiment_id, user_id, body.samples)
    await session.commit()
    return {"created": len(samples)}


@router.post("/{experiment_id}/samples/upload")
async def upload_samples_csv(
    experiment_id: int,
    file: UploadFile = File(...),
    current_user: dict = require_permission("experiments", "upload"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    content = await file.read()
    parsed_samples, parse_errors = parse_sample_csv(content, experiment_id)

    if not parsed_samples and parse_errors:
        raise HTTPException(400, detail={"errors": parse_errors})

    created = []
    create_errors = []
    for i, sample_data in enumerate(parsed_samples):
        try:
            sample = await SampleService.create_sample(session, experiment_id, user_id, sample_data)
            created.append(sample)
        except HTTPException as e:
            create_errors.append(f"Sample {i + 1}: {e.detail}")

    if created:
        await session.commit()

    return {
        "created_count": len(created),
        "error_count": len(parse_errors) + len(create_errors),
        "errors": parse_errors + create_errors,
    }


@router.get("/{experiment_id}/batches")
async def list_experiment_batches(
    experiment_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    batches = await BatchService.list_batches(session, experiment_id)
    return [
        BatchResponse(
            id=b.id,
            name=b.name,
            prep_date=b.prep_date,
            operator=_user_summary(b.operator),
            sequencer_run_id=b.sequencer_run_id,
            instrument_model=b.instrument_model,
            instrument_platform=b.instrument_platform,
            quality_score_encoding=b.quality_score_encoding,
            notes=b.notes,
            sample_count=len(b.samples) if b.samples else 0,
            created_at=b.created_at,
            updated_at=b.updated_at,
        )
        for b in batches
    ]


@router.post("/{experiment_id}/batches", response_model=BatchResponse)
async def create_batch(
    experiment_id: int,
    body: BatchCreate,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    batch = await BatchService.create_batch(session, experiment_id, user_id, body)
    await session.commit()

    batch = await BatchService.get_batch(session, batch.id)
    return BatchResponse(
        id=batch.id,
        name=batch.name,
        prep_date=batch.prep_date,
        operator=_user_summary(batch.operator),
        sequencer_run_id=batch.sequencer_run_id,
        instrument_model=batch.instrument_model,
        instrument_platform=batch.instrument_platform,
        quality_score_encoding=batch.quality_score_encoding,
        notes=batch.notes,
        sample_count=len(batch.samples) if batch.samples else 0,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


@router.get("/{experiment_id}/files")
async def list_experiment_files(
    experiment_id: int,
    request: Request,
    page: int = 1,
    page_size: int = 25,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    experiment = await ExperimentService.get_experiment(session, experiment_id, org_id)
    if not experiment:
        raise HTTPException(404, "Experiment not found")

    from app.schemas.file import FileListResponse, FileResponse
    from app.schemas.experiment import UserSummary
    from app.services.file_service import FileService

    files, total = await FileService.list_files(
        session,
        org_id,
        experiment_id=experiment_id,
        page=page,
        page_size=page_size,
    )
    return FileListResponse(
        files=[
            FileResponse(
                id=f.id,
                filename=f.filename,
                gcs_uri=f.gcs_uri,
                size_bytes=f.size_bytes,
                md5_checksum=f.md5_checksum,
                file_type=f.file_type,
                tags=f.tags_json if isinstance(f.tags_json, list) else [],
                uploader=UserSummary(id=f.uploader.id, name=f.uploader.name, email=f.uploader.email)
                if f.uploader
                else None,
                experiment_id=f.experiment_id,
                upload_timestamp=f.upload_timestamp,
                created_at=f.created_at,
            )
            for f in files
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{experiment_id}/audit", response_model=AuditLogResponse)
async def get_experiment_audit(
    experiment_id: int,
    request: Request,
    page: int = 1,
    page_size: int = 25,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    entries, total = await ExperimentService.get_audit_trail(
        session, experiment_id, org_id, page=page, page_size=page_size
    )

    return AuditLogResponse(
        entries=[
            AuditLogEntry(
                id=e.id,
                timestamp=e.timestamp,
                user=None,
                entity_type=e.entity_type,
                entity_id=e.entity_id,
                action=e.action,
                details=e.details_json,
                previous_value=e.previous_value_json,
            )
            for e in entries
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
