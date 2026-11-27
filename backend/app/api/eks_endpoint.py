"""EKS Kubernetes API endpoint administrator API (security plan 04C).

- ``GET    /api/v1/infrastructure/eks-endpoint``: the recorded mode, preserved exposure and any
  pending cutover. ``pending_hardening`` stays true while an existing cluster keeps its preserved
  endpoint.
- ``POST   /api/v1/infrastructure/eks-endpoint/cutover``: record a reviewed cutover to
  ``private_only`` or ``public_restricted``. The next compute plan carries the endpoint change and
  its apply commits it after a live readback.
- ``DELETE /api/v1/infrastructure/eks-endpoint/cutover``: drop a pending cutover while the live
  endpoint is unchanged.

AWS installations only. See docs/operations/eks-endpoint-cutover.md.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.exceptions import ValidationError
from app.platform.cloud_provider import get_cloud_provider
from app.schemas.eks_endpoint import EksEndpointCutoverRequest, EksEndpointStatus
from app.services import audit_service, eks_endpoint

router = APIRouter(prefix="/api/v1/infrastructure/eks-endpoint", tags=["eks_endpoint"])


async def _require_aws(session: AsyncSession) -> None:
    if await get_cloud_provider(session) != "aws":
        raise ValidationError("The EKS endpoint settings apply to AWS installations only.")


@router.get("", response_model=EksEndpointStatus)
async def get_eks_endpoint(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> EksEndpointStatus:
    await _require_aws(session)
    return EksEndpointStatus.model_validate(await eks_endpoint.current_status(session))


@router.post("/cutover", response_model=EksEndpointStatus)
async def request_eks_endpoint_cutover(
    body: EksEndpointCutoverRequest,
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
) -> EksEndpointStatus:
    await _require_aws(session)
    user_id = int(current_user["sub"])
    cutover = await eks_endpoint.request_cutover(
        session,
        mode=body.mode,
        public_access_cidrs=body.public_access_cidrs,
        user_id=user_id,
        acknowledged=body.recovery_paths_verified,
    )
    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="eks_endpoint",
        entity_id=0,
        action="cutover_requested",
        details={"mode": cutover["mode"], "public_access_cidrs": cutover["public_access_cidrs"]},
        previous_value=cutover["snapshot"],
    )
    await session.commit()
    return EksEndpointStatus.model_validate(await eks_endpoint.current_status(session))


@router.delete("/cutover", response_model=EksEndpointStatus)
async def cancel_eks_endpoint_cutover(
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
) -> EksEndpointStatus:
    await _require_aws(session)
    await eks_endpoint.cancel_cutover(session)
    await audit_service.log_action(
        session, user_id=int(current_user["sub"]), entity_type="eks_endpoint", entity_id=0, action="cutover_cancelled"
    )
    await session.commit()
    return EksEndpointStatus.model_validate(await eks_endpoint.current_status(session))
