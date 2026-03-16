from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.schemas.dataset import DatasetExperimentSummary, DatasetFilterOptions, DatasetSearchResult
from app.schemas.experiment import UserSummary
from app.services.dataset_service import DatasetService

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


@router.get("", response_model=DatasetSearchResult)
async def search_datasets(
    request: Request,
    query: str | None = None,
    organism: str | None = None,
    tissue: str | None = None,
    chemistry: str | None = None,
    status: str | None = None,
    molecule_type: str | None = None,
    instrument_model: str | None = None,
    review_status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    batch_id: int | None = None,
    page: int = 1,
    page_size: int = 25,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    datasets, total = await DatasetService.search_datasets(
        session,
        org_id,
        query=query,
        organism=organism,
        tissue=tissue,
        chemistry=chemistry,
        status=status,
        molecule_type=molecule_type,
        instrument_model=instrument_model,
        review_status=review_status,
        date_from=date_from,
        date_to=date_to,
        batch_id=batch_id,
        page=page,
        page_size=page_size,
    )

    return DatasetSearchResult(
        experiments=[
            DatasetExperimentSummary(
                experiment_id=d["experiment_id"],
                experiment_name=d["experiment_name"],
                status=d["status"],
                organism=d.get("organism"),
                tissue=d.get("tissue"),
                molecule_type=d.get("molecule_type"),
                instrument_model=d.get("instrument_model"),
                review_status=d.get("review_status"),
                sample_count=d["sample_count"],
                file_count=d["file_count"],
                total_size_bytes=d["total_size_bytes"],
                pipeline_run_count=d["pipeline_run_count"],
                has_qc_dashboard=d["has_qc_dashboard"],
                has_cellxgene=d["has_cellxgene"],
                owner=UserSummary(id=d["owner"].id, name=d["owner"].name, email=d["owner"].email)
                if d.get("owner")
                else None,
                created_at=d["created_at"],
            )
            for d in datasets
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/filter-options", response_model=DatasetFilterOptions)
async def get_filter_options(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])
    options = await DatasetService.get_filter_options(session, org_id)
    return DatasetFilterOptions(**options)


@router.get("/{experiment_id}")
async def get_dataset_detail(
    experiment_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    detail = await DatasetService.get_dataset_detail(session, org_id, experiment_id)
    if not detail:
        raise HTTPException(404, "Dataset not found")

    return detail
