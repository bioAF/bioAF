from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_role
from app.database import get_session
from app.schemas.cellxgene import CellxgenePublicationResponse, CellxgenePublishRequest
from app.schemas.experiment import UserSummary
from app.schemas.file import FileResponse
from app.services.cellxgene_service import CellxgeneService

router = APIRouter(prefix="/api/cellxgene", tags=["cellxgene"])


def _pub_response(pub) -> CellxgenePublicationResponse:
    file_resp = None
    if pub.file:
        file_resp = FileResponse(
            id=pub.file.id,
            filename=pub.file.filename,
            gcs_uri=pub.file.gcs_uri,
            size_bytes=pub.file.size_bytes,
            md5_checksum=pub.file.md5_checksum,
            file_type=pub.file.file_type,
            tags=pub.file.tags_json if isinstance(pub.file.tags_json, list) else [],
            uploader=None,
            upload_timestamp=pub.file.upload_timestamp,
            created_at=pub.file.created_at,
        )
    return CellxgenePublicationResponse(
        id=pub.id,
        dataset_name=pub.dataset_name,
        stable_url=pub.stable_url,
        status=pub.status,
        file=file_resp,
        experiment_id=pub.experiment_id,
        published_by=UserSummary(id=pub.published_by.id, name=pub.published_by.name, email=pub.published_by.email)
        if pub.published_by
        else None,
        published_at=pub.published_at,
        created_at=pub.created_at,
    )


@router.post("/publish", response_model=CellxgenePublicationResponse)
async def publish_dataset(
    body: CellxgenePublishRequest,
    current_user: dict = require_role("admin", "comp_bio"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    # Check component enabled
    from app.services.component_service import ComponentService

    if not await ComponentService.is_enabled(session, "cellxgene"):
        raise HTTPException(400, "cellxgene component is not enabled")

    try:
        pub = await CellxgeneService.publish_dataset(
            session,
            org_id,
            user_id,
            file_id=body.file_id,
            experiment_id=body.experiment_id,
            dataset_name=body.dataset_name,
        )
        await session.commit()
        pub = await CellxgeneService.get_publication(session, org_id, pub.id)
        return _pub_response(pub)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{publication_id}")
async def unpublish_dataset(
    publication_id: int,
    current_user: dict = require_role("admin", "comp_bio"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    pub = await CellxgeneService.unpublish_dataset(session, org_id, publication_id, user_id)
    if not pub:
        raise HTTPException(404, "Publication not found")
    await session.commit()
    return {"status": "unpublished"}


@router.get("")
async def list_publications(
    request: Request,
    experiment_id: int | None = None,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    pubs = await CellxgeneService.list_publications(session, org_id, experiment_id)
    return [_pub_response(p) for p in pubs]


@router.get("/publishable-files")
async def list_publishable_files(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    files = await CellxgeneService.list_publishable_files(session, org_id)
    return [
        FileResponse(
            id=f.id,
            filename=f.filename,
            gcs_uri=f.gcs_uri,
            size_bytes=f.size_bytes,
            md5_checksum=f.md5_checksum,
            file_type=f.file_type,
            tags=f.tags_json if isinstance(f.tags_json, list) else [],
            uploader=None,
            experiment_id=f.experiment_id,
            upload_timestamp=f.upload_timestamp,
            created_at=f.created_at,
        )
        for f in files
    ]


@router.get("/{publication_id}", response_model=CellxgenePublicationResponse)
async def get_publication(
    publication_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    pub = await CellxgeneService.get_publication(session, org_id, publication_id)
    if not pub:
        raise HTTPException(404, "Publication not found")
    return _pub_response(pub)
