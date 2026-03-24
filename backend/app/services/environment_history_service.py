"""Legacy environment history service (pre-ADR-033).

Environment change history is now tracked through environment_versions
(ADR-033). This module is retained as a stub to prevent import errors.
"""

import logging

logger = logging.getLogger("bioaf.environment_history")


class EnvironmentHistoryService:
    pass
