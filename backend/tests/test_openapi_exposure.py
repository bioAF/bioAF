"""Tests for OpenAPI/docs endpoint exposure control."""

import pytest
import pytest_asyncio
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def production_client():
    """Client with BIOAF_ENVIRONMENT=production (docs disabled)."""
    with patch("app.config.settings.environment", "production"):
        from app.main import create_app

        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


@pytest_asyncio.fixture
async def development_client():
    """Client with BIOAF_ENVIRONMENT=development (docs enabled)."""
    with patch("app.config.settings.environment", "development"):
        from app.main import create_app

        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


class TestOpenApiExposure:
    """OpenAPI and Swagger UI should only be available in development."""

    @pytest.mark.asyncio
    async def test_docs_disabled_in_production(self, production_client):
        resp = await production_client.get("/docs")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_openapi_json_disabled_in_production(self, production_client):
        resp = await production_client.get("/openapi.json")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_docs_enabled_in_development(self, development_client):
        resp = await development_client.get("/docs")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_openapi_json_enabled_in_development(self, development_client):
        resp = await development_client.get("/openapi.json")
        assert resp.status_code == 200
