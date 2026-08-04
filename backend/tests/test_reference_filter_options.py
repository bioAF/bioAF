"""The reference filter vocabularies must come from the backend.

The Reference Data page hard-coded its own scope and category lists, and they
never matched the model: it offered scopes "global"/"organization" while
REFERENCE_SCOPES is ["public", "internal"], so selecting either always returned
zero rows and the UI reported it as "no data". It also offered "transcriptome"
(not a real category) and omitted "atlas" and "markers", which the upload form
can create, so those references were unreachable by any category filter.

Serving the vocabularies makes that class of drift impossible.
"""

import pytest
from httpx import AsyncClient

from app.models.reference_dataset import REFERENCE_CATEGORIES, REFERENCE_SCOPES


@pytest.mark.asyncio
async def test_filter_options_returns_the_model_vocabularies(client: AsyncClient, admin_token: str):
    resp = await client.get(
        "/api/references/filter-options",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["categories"] == REFERENCE_CATEGORIES
    assert data["scopes"] == REFERENCE_SCOPES


@pytest.mark.asyncio
async def test_filter_options_covers_every_category_the_upload_form_can_create(
    client: AsyncClient, admin_token: str
):
    resp = await client.get(
        "/api/references/filter-options",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    categories = resp.json()["categories"]
    for required in ("genome", "annotation", "index", "atlas", "markers", "other"):
        assert required in categories, f"{required} references would be unfilterable"
    assert "transcriptome" not in categories, "offering a category the model rejects returns zero rows"
