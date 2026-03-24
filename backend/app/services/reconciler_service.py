"""Legacy environment reconciler (pre-ADR-033).

Environment reconciliation via GitOps + SLURM SSH has been superseded by
the versioned environment build system (ADR-033). Environments are now
built as container images via Cloud Build.

This module is retained as a no-op so the background loop in main.py
does not crash.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("bioaf.reconciler")


class ReconcilerService:
    @staticmethod
    async def process_pending(session: AsyncSession) -> None:
        """No-op. Legacy reconciliation is superseded by ADR-033."""
        pass
