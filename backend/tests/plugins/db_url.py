"""The test database URL, shared by conftest and the test plugins."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://bioaf_app:devpassword@localhost:5432/bioaf_test"


def database_url() -> str:
    return os.environ.get("BIOAF_TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


def describe(url: str) -> dict:
    """Where a URL points, without its password."""
    parts = urlsplit(url)
    return {
        "host": parts.hostname,
        "port": parts.port,
        "database": parts.path.lstrip("/"),
        "user": parts.username,
    }
