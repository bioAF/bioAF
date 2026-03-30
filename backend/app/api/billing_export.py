"""BigQuery billing export setup endpoints (ADR-028).

- GET  /api/v1/infrastructure/billing-export/status    - check configuration state
- POST /api/v1/infrastructure/billing-export/enable    - create BQ dataset via Terraform
- POST /api/v1/infrastructure/billing-export/verify    - verify export data is flowing
- POST /api/v1/infrastructure/billing-export/teardown  - destroy BQ dataset via Terraform (SSE)
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.services.billing_export_service import BillingExportService
from app.services.credential_injector import load_gcp_credentials
from app.services.terraform_executor import TerraformExecutor, TerraformProgressEvent

logger = logging.getLogger("bioaf.billing_export_api")

router = APIRouter(tags=["billing_export"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BillingExportStatusResponse(BaseModel):
    configured: bool
    dataset_id: str
    console_url: str
    table_id: str = ""


class BillingExportEnableResponse(BaseModel):
    status: str
    message: str = ""


class BillingExportVerifyResponse(BaseModel):
    configured: bool
    table_id: str = ""
    message: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BILLING_EXPORT_CONFIG_KEYS = [
    "gcp_project_id",
    "gcp_credentials_configured",
    "gcp_credential_source",
    "gcp_service_account_key",
    "gcp_service_account_email",
    "terraform_initialized",
    "billing_export_configured",
    "billing_export_dataset",
    "billing_export_table",
]


async def _read_billing_config(session: AsyncSession) -> dict:
    rows = (
        await session.execute(
            text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)").bindparams(
                keys=BILLING_EXPORT_CONFIG_KEYS
            )
        )
    ).fetchall()
    return {r[0]: r[1] for r in rows}


async def deploy_billing_export_module(session: AsyncSession, user_id: int) -> dict:
    """Run Terraform plan + apply for the billing_export module.

    On success, stores the dataset ID in platform_config.
    """
    run = await TerraformExecutor.run_plan(session, user_id, module_name="billing_export")
    await session.commit()

    if run.status != "awaiting_confirmation":
        return {"status": "failed", "message": run.error_message or "Plan failed"}

    # Fully consume the generator to avoid closing it mid-iteration, which
    # would trigger GeneratorExit while asyncpg still has an operation in
    # flight (run_apply flushes progress updates internally).
    dataset_id = "billing_export"
    error_message: str | None = None
    async for event in TerraformExecutor.run_apply(session, run.id, user_id):
        if event.event_type == "apply_error":
            error_message = event.message
        elif event.event_type == "apply_complete":
            dataset_id = event.extra.get("outputs", {}).get("dataset_id", {}).get("value", "billing_export")

    if error_message is not None:
        return {"status": "failed", "message": error_message}

    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
        ).bindparams(k="billing_export_dataset", v=dataset_id)
    )
    await session.commit()
    return {"status": "completed"}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/infrastructure/billing-export/status",
    response_model=BillingExportStatusResponse,
)
async def billing_export_status(
    current_user: dict = require_permission("cost_center", "view"),
    session: AsyncSession = Depends(get_session),
) -> BillingExportStatusResponse:
    """Check whether BigQuery billing export is configured."""
    config = await _read_billing_config(session)
    project_id = config.get("gcp_project_id", "")
    dataset_id = config.get("billing_export_dataset", "")
    configured = config.get("billing_export_configured", "false") == "true"
    table_id = config.get("billing_export_table", "")

    console_url = f"https://console.cloud.google.com/billing/export?project={project_id}"

    return BillingExportStatusResponse(
        configured=configured,
        dataset_id=dataset_id,
        console_url=console_url,
        table_id=table_id,
    )


@router.post(
    "/api/v1/infrastructure/billing-export/enable",
    response_model=BillingExportEnableResponse,
)
async def billing_export_enable(
    current_user: dict = require_permission("cost_center", "view"),
    session: AsyncSession = Depends(get_session),
) -> BillingExportEnableResponse:
    """Create the BigQuery dataset and IAM bindings via Terraform."""
    config = await _read_billing_config(session)

    if config.get("terraform_initialized", "false") != "true":
        raise HTTPException(
            status_code=400,
            detail="Terraform has not been initialized. Run infrastructure bootstrap first.",
        )

    user_id = int(current_user["sub"])
    result = await deploy_billing_export_module(session, user_id)
    return BillingExportEnableResponse(**result)


@router.post(
    "/api/v1/infrastructure/billing-export/verify",
    response_model=BillingExportVerifyResponse,
)
async def billing_export_verify(
    current_user: dict = require_permission("cost_center", "view"),
    session: AsyncSession = Depends(get_session),
) -> BillingExportVerifyResponse:
    """Verify that the billing export table exists and data is flowing."""
    config = await _read_billing_config(session)
    dataset_id = config.get("billing_export_dataset", "")
    project_id = config.get("gcp_project_id", "")

    if not dataset_id:
        raise HTTPException(
            status_code=400,
            detail="Billing export dataset has not been created. Run enable first.",
        )

    try:
        creds = load_gcp_credentials(config)
    except Exception:
        logger.exception("Failed to load GCP credentials for BQ verification")
        creds = None
    result = await BillingExportService.verify_dataset(project_id, dataset_id, credentials=creds)

    if not result["found"]:
        return BillingExportVerifyResponse(
            configured=False,
            message="Billing export table not found. Data may take up to 24 hours to appear after enabling export.",
        )

    table_id = result["table_id"]

    # Persist configuration
    for key, value in [
        ("billing_export_configured", "true"),
        ("billing_export_table", table_id),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
            ).bindparams(k=key, v=value)
        )
    await session.commit()

    return BillingExportVerifyResponse(
        configured=True,
        table_id=table_id,
        message="Billing export verified and configured.",
    )


def _sse_event(event: TerraformProgressEvent) -> str:
    """Format a TerraformProgressEvent as an SSE data line."""
    payload = {
        "event_type": event.event_type,
        "message": event.message,
        "resource_address": event.resource_address,
        "resources_completed": event.resources_completed,
        "resources_total": event.resources_total,
        "log_line": event.log_line,
    }
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/api/v1/infrastructure/billing-export/teardown")
async def billing_export_teardown(
    current_user: dict = require_permission("cost_center", "view"),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Destroy the BigQuery billing export dataset via Terraform.

    Streams SSE progress events. On success, clears billing export
    entries from platform_config so the setup flow can be re-run.
    """
    config = await _read_billing_config(session)

    if config.get("terraform_initialized", "false") != "true":
        raise HTTPException(
            status_code=400,
            detail="Terraform has not been initialized.",
        )

    user_id = int(current_user["sub"])

    gen = TerraformExecutor.run_destroy(
        session=session,
        user_id=user_id,
        module_name="billing_export",
    )

    async def event_generator():
        completed = False
        try:
            async for event in gen:
                if event.event_type == "apply_complete":
                    completed = True
                yield _sse_event(event)
        except Exception as exc:
            logger.error("Billing export apply error: %s", exc, exc_info=True)
            error_event = TerraformProgressEvent(
                event_type="apply_error",
                message="Apply failed unexpectedly",
            )
            yield _sse_event(error_event)
        finally:
            if completed:
                for key in (
                    "billing_export_configured",
                    "billing_export_dataset",
                    "billing_export_table",
                ):
                    await session.execute(text("DELETE FROM platform_config WHERE key = :k").bindparams(k=key))
            await session.commit()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
