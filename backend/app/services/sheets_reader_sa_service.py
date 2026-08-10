"""Manage a dedicated Google Sheets reader service account.

The reader SA is a single-purpose service account with only Sheets API
read access.  Users share their spreadsheets with this SA's email to
allow bioAF to read column headers during experiment creation.

Greenfield installs use keyless impersonation: bioaf-app holds
``roles/iam.serviceAccountTokenCreator`` resource-scoped to the reader
SA, and ``get_reader_credentials`` mints a short-lived impersonated
token at request time. Only the SA email and a "created" flag live in
``platform_config``. Legacy installs (``gcp_credential_source =
service_account_key``) continue to use the stored JSON key.
"""

import secrets

from googleapiclient import discovery as google_discovery
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.credentials import get_credentials_provider
from app.platform.platform_config_service import PlatformConfigService

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
    "gcp_bootstrap_sa_email",
]


async def _upsert(session: AsyncSession, key: str, value: str) -> None:
    await PlatformConfigService.set(session, key, value)


async def _delete_key(session: AsyncSession, key: str) -> None:
    await PlatformConfigService.set(session, key, None)


async def _read_keys(session: AsyncSession, keys: list[str]) -> dict[str, str]:
    return await PlatformConfigService.get_many(session, keys)


def _load_primary_credentials(config: dict[str, str]) -> tuple[object, str]:
    """Load the primary SA credentials from platform_config values.

    Returns ``(credentials, project_id)``.
    Raises ``RuntimeError`` if GCP credentials are not configured.
    """
    project_id = config.get("gcp_project_id", "")
    if not project_id:
        raise RuntimeError("GCP project ID is not configured")

    source = config.get("gcp_credential_source", "vm_default")
    if source == "service_account_key" and not config.get("gcp_service_account_key"):
        raise RuntimeError("GCP service account key is not configured")

    # The primary SA path is the seam's default resolution: cloud-platform scope,
    # config-derived impersonation (bootstrap SA, else the legacy email).
    creds = get_credentials_provider().load_credentials(config)
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
    """Create a keyless reader SA and grant bioaf-app token-creator on it.

    No JSON key is created: the runtime impersonates the reader SA via
    short-lived tokens. Stores the SA email + ``sheets_reader_sa_created=true``
    in ``platform_config``. Returns ``{email: str}``.

    For ``service_account_key`` (legacy) installs, the SA still has no key, so
    the legacy install can't impersonate and the in-app button is not the
    right path there. Greenfield ``vm_default`` installs are the supported
    target; the typical greenfield flow has the installer pre-provisioning
    the reader SA so this function is a fallback.
    """
    status = await get_reader_sa_status(session)
    if status["exists"]:
        return {"email": str(status["email"])}

    gcp_config = await _read_keys(session, _GCP_KEYS)
    creds, project_id = _load_primary_credentials(gcp_config)

    # Identify the runtime SA so we can grant tokenCreator on the new
    # reader SA. On GCE this is the VM's attached SA (bioaf-app).
    from app.services.bootstrap_metadata import get_attached_sa_email

    runtime_sa_email = await get_attached_sa_email()
    if not runtime_sa_email:
        raise RuntimeError(
            "Cannot determine the runtime service account email "
            "(VM metadata server unreachable). Run install-gcp.sh on a "
            "fresh project to provision the reader SA automatically."
        )

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

    # Grant the runtime SA roles/iam.serviceAccountTokenCreator on the
    # newly created reader SA so impersonation works at request time.
    sa_resource = f"projects/{project_id}/serviceAccounts/{sa_email}"
    member = f"serviceAccount:{runtime_sa_email}"
    try:
        policy = iam_service.projects().serviceAccounts().getIamPolicy(resource=sa_resource).execute()
        bindings = policy.get("bindings", [])
        token_creator = next((b for b in bindings if b.get("role") == "roles/iam.serviceAccountTokenCreator"), None)
        if token_creator is None:
            bindings.append({"role": "roles/iam.serviceAccountTokenCreator", "members": [member]})
        elif member not in token_creator.get("members", []):
            token_creator.setdefault("members", []).append(member)
        policy["bindings"] = bindings
        iam_service.projects().serviceAccounts().setIamPolicy(
            resource=sa_resource,
            body={"policy": policy},
        ).execute()
    except Exception as exc:
        # Roll back the SA we just created so a re-run can retry cleanly.
        try:
            iam_service.projects().serviceAccounts().delete(name=sa_resource).execute()
        except Exception:
            pass
        raise RuntimeError(
            f"Created reader SA {sa_email} but failed to grant tokenCreator to {runtime_sa_email}: {exc}"
        ) from exc

    # Enable the Sheets API in the project (best-effort).
    sheets_api_enabled = False
    try:
        service_usage = discovery_build("serviceusage", "v1", credentials=creds, cache_discovery=False)
        service_usage.services().enable(name=f"projects/{project_id}/services/sheets.googleapis.com").execute()
        sheets_api_enabled = True
    except Exception:
        try:
            svc = service_usage.services().get(name=f"projects/{project_id}/services/sheets.googleapis.com").execute()
            sheets_api_enabled = svc.get("state") == "ENABLED"
        except Exception:
            pass

    await _upsert(session, "sheets_reader_sa_email", sa_email)
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


async def get_reader_credentials(session: AsyncSession):
    """Load the reader SA's credentials scoped to Sheets readonly.

    On ``vm_default`` installs, mint an impersonated token for the
    reader SA using bioaf-app's metadata-server identity as the source
    principal. On legacy ``service_account_key`` installs, build
    credentials from the stored JSON key.

    Raises ``RuntimeError`` if the reader SA is not configured.
    """
    config = await _read_keys(session, _READER_SA_KEYS + _GCP_KEYS)
    if config.get("sheets_reader_sa_created") != "true":
        raise RuntimeError(
            "Google Sheets reader service account is not configured. Set it up in Settings > Integrations > GCP."
        )

    sa_email = config.get("sheets_reader_sa_email", "")
    if not sa_email:
        raise RuntimeError("Reader SA email is missing from configuration")

    provider = get_credentials_provider()

    # Legacy installs still use the stored JSON key (the reader SA's own key,
    # scoped to Sheets readonly).
    if config.get("gcp_credential_source") == "service_account_key":
        key_json = config.get("sheets_reader_sa_key", "")
        if not key_json:
            raise RuntimeError("Reader SA key is missing from configuration")
        return provider.load_credentials(config, scopes=_SHEETS_SCOPE, key_json=key_json)

    # Greenfield: impersonate the reader SA using bioaf-app's ADC, with a
    # short-lived Sheets-scoped token.
    return provider.load_credentials(
        config,
        scopes=_SHEETS_SCOPE,
        impersonate_target=sa_email,
        lifetime=3600,
    )
