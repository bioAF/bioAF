"""Legacy package management service (pre-ADR-033).

Individual package install/remove/update operations have been superseded by
the versioned environment system (ADR-033). Package changes are now made by
editing the Dockerfile or conda YAML definition content and creating a new
environment version.

This module is retained so that existing imports (packages API) do not crash
on startup, but all methods raise NotImplementedError at runtime.
"""

import logging

logger = logging.getLogger("bioaf.package")


class PackageService:
    @staticmethod
    async def install_package(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError("Package management moved to environment versions (ADR-033)")

    @staticmethod
    async def remove_package(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError("Package management moved to environment versions (ADR-033)")

    @staticmethod
    async def update_package(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError("Package management moved to environment versions (ADR-033)")

    @staticmethod
    async def pin_package(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError("Package management moved to environment versions (ADR-033)")

    @staticmethod
    async def unpin_package(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError("Package management moved to environment versions (ADR-033)")
