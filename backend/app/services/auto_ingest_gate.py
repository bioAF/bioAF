"""Single source of truth for the auto-ingest disable during the Naming Profile redesign.

The Naming Profile feature is being rebuilt (see
local/Naming Profiles/redesign-plan.md). Auto-ingest depends on the old
naming-profile contract and would silently misbehave against the new schema,
so it is gated off until the follow-up rework lands.

When the rework is ready to re-enable auto-ingest, flip AUTO_INGEST_DISABLED
to False. The gate fires at the top of process_ingest_event() and
process_manifest_ingest(), and the API layer translates the resulting
AutoIngestDisabledError into a 503 with the documented body.
"""

AUTO_INGEST_DISABLED = True


class AutoIngestDisabledError(Exception):
    """Raised when auto-ingest is invoked while the gate is active."""

    code = "auto_ingest_temporarily_disabled"
    message = (
        "Auto-ingest is temporarily disabled while the Naming Profile "
        "feature is being redesigned. See "
        "local/Naming Profiles/redesign-plan.md."
    )

    def __init__(self) -> None:
        super().__init__(self.message)


def check_gate() -> None:
    """Raise AutoIngestDisabledError if the gate is currently active.

    Reads AUTO_INGEST_DISABLED via the module global at call time, so
    monkeypatching the module attribute in tests (and flipping the constant
    for real in the follow-up rework) takes effect immediately.
    """
    if AUTO_INGEST_DISABLED:
        raise AutoIngestDisabledError()
