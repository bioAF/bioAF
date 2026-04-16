"""Manage a dedicated Google Sheets reader service account.

The reader SA is a single-purpose service account with only Sheets API
read access.  Users share their spreadsheets with this SA's email to
allow bioAF to read column headers during experiment creation.

Credentials for the reader SA are stored in the ``platform_config``
table alongside other GCP configuration.
"""

import base64
import json
import secrets
import time

from google.oauth2 import service_account
from googleapiclient import discovery as google_discovery
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Patchable aliases for tests
discovery_build = google_discovery.build

_SHEETS_SCOPE = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

_READER_SA_KEYS = [
    "sheets_reader_sa_email",
    "sheets_reader_sa_key",
    "sheets_reader_sa_created",
]

_GCP_KEYS = [
    "gcp_project_id",
    "gcp_credential_source",
    "gcp_service_account_key",
    "gcp_service_account_email",
]


async def _upsert(session: AsyncSession, key: str, value: str) -> None:
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_at = now()"
        ).bindparams(k=key, v=value)
    )


async def _delete_key(session: AsyncSession, key: str) -> None:
    await session.execute(text("DELETE FROM platform_config WHERE key = :k").bindparams(k=key))


async def _read_keys(session: AsyncSession, keys: list[str]) -> dict[str, str]:
    rows = (
        await session.execute(
            text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)").bindparams(keys=keys)
        )
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _load_primary_credentials(config: dict[str, str]) -> tuple[object, str]:
    """Load the primary SA credentials from platform_config values.

    Returns ``(credentials, project_id)``.
    Raises ``RuntimeError`` if GCP credentials are not configured.
    """
    project_id = config.get("gcp_project_id", "")
    if not project_id:
        raise RuntimeError("GCP project ID is not configured")

    source = config.get("gcp_credential_source", "vm_default")
    if source == "service_account_key":
        key_json = config.get("gcp_service_account_key", "")
        if not key_json:
            raise RuntimeError("GCP service account key is not configured")
        key_data = json.loads(key_json)
        creds = service_account.Credentials.from_service_account_info(
            key_data,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    else:
        import google.auth as _google_auth

        creds, _ = _google_auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        sa_email = config.get("gcp_service_account_email")
        if sa_email:
            from google.auth import impersonated_credentials

            creds = impersonated_credentials.Credentials(
                source_credentials=creds,
                target_principal=sa_email,
                target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )

    return creds, project_id


async def get_reader_sa_status(session: AsyncSession) -> dict[str, object]:
    """Return ``{exists: bool, email: str | None}``."""
    config = await _read_keys(session, _READER_SA_KEYS)
    exists = config.get("sheets_reader_sa_created") == "true"
    return {
        "exists": exists,
        "email": config.get("sheets_reader_sa_email") if exists else None,
    }


async def create_reader_sa(session: AsyncSession) -> dict[str, str]:
    """Create a dedicated reader SA via the IAM Admin API.

    Stores the SA email and JSON key in ``platform_config``.
    Returns ``{email: str}``.
    """
    # Check if one already exists
    status = await get_reader_sa_status(session)
    if status["exists"]:
        return {"email": str(status["email"])}

    # Load primary credentials
    gcp_config = await _read_keys(session, _GCP_KEYS)
    creds, project_id = _load_primary_credentials(gcp_config)

    # Create the service account
    iam_service = discovery_build("iam", "v1", credentials=creds, cache_discovery=False)
    account_id = f"bioaf-reader-{secrets.token_hex(4)}"

    sa = (
        iam_service.projects()
        .serviceAccounts()
        .create(
            name=f"projects/{project_id}",
            body={
                "accountId": account_id,
                "serviceAccount": {
                    "displayName": "bioAF Sheets Reader",
                    "description": "Read-only access to Google Sheets for bioAF field import",
                },
            },
        )
        .execute()
    )

    sa_email = sa["email"]

    # Create a JSON key for the new SA.
    # IAM has a propagation delay after SA creation -- retry up to 5
    # times with backoff before giving up on key creation.
    key_response = None
    for attempt in range(5):
        try:
            key_response = (
                iam_service.projects()
                .serviceAccounts()
                .keys()
                .create(
                    name=f"projects/{project_id}/serviceAccounts/{sa_email}",
                    body={"keyAlgorithm": "KEY_ALG_RSA_2048"},
                )
                .execute()
            )
            break
        except Exception as exc:
            if attempt < 4 and ("404" in str(exc) or "does not exist" in str(exc).lower()):
                time.sleep(2 * (attempt + 1))
                continue
            raise

    if key_response is None:
        raise RuntimeError(f"Failed to create key for {sa_email} after retries")

    key_json = base64.b64decode(key_response["privateKeyData"]).decode("utf-8")

    # Enable the Sheets API in the project
    sheets_api_enabled = False
    try:
        service_usage = discovery_build("serviceusage", "v1", credentials=creds, cache_discovery=False)
        service_usage.services().enable(name=f"projects/{project_id}/services/sheets.googleapis.com").execute()
        sheets_api_enabled = True
    except Exception:
        # The API may already be enabled -- check before warning
        try:
            svc = service_usage.services().get(name=f"projects/{project_id}/services/sheets.googleapis.com").execute()
            sheets_api_enabled = svc.get("state") == "ENABLED"
        except Exception:
            pass

    # Persist to platform_config
    await _upsert(session, "sheets_reader_sa_email", sa_email)
    await _upsert(session, "sheets_reader_sa_key", key_json)
    await _upsert(session, "sheets_reader_sa_created", "true")
    await session.commit()

    result: dict[str, object] = {"email": sa_email}
    if not sheets_api_enabled:
        result["warning"] = (
            "The Google Sheets API could not be enabled automatically. "
            "Enable it manually at: "
            "https://console.cloud.google.com/apis/library/sheets.googleapis.com"
        )
    return result


async def delete_reader_sa(session: AsyncSession) -> None:
    """Delete the reader SA and remove stored credentials."""
    config = await _read_keys(session, _READER_SA_KEYS + _GCP_KEYS)
    sa_email = config.get("sheets_reader_sa_email")
    if not sa_email:
        return

    # Delete from GCP
    try:
        gcp_config = {k: config[k] for k in _GCP_KEYS if k in config}
        creds, project_id = _load_primary_credentials(gcp_config)
        iam_service = discovery_build("iam", "v1", credentials=creds, cache_discovery=False)
        iam_service.projects().serviceAccounts().delete(
            name=f"projects/{project_id}/serviceAccounts/{sa_email}"
        ).execute()
    except Exception:
        # Best effort -- SA may already have been deleted externally
        pass

    # Remove from platform_config
    for key in _READER_SA_KEYS:
        await _delete_key(session, key)
    await session.commit()


async def get_reader_credentials(session: AsyncSession) -> service_account.Credentials:
    """Load the reader SA's credentials scoped to Sheets readonly.

    Raises ``RuntimeError`` if the reader SA is not configured.
    """
    config = await _read_keys(session, _READER_SA_KEYS)
    if config.get("sheets_reader_sa_created") != "true":
        raise RuntimeError(
            "Google Sheets reader service account is not configured. Set it up in Settings > Integrations > GCP."
        )
    key_json = config.get("sheets_reader_sa_key", "")
    if not key_json:
        raise RuntimeError("Reader SA key is missing from configuration")

    key_data = json.loads(key_json)
    return service_account.Credentials.from_service_account_info(key_data, scopes=_SHEETS_SCOPE)
