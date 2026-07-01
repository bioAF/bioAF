"""Assistant availability gate (L4).

The assistant runs on the org's active LLM provider (ADR-053), but it needs native
tool-calling, which the advisory agent-review job does not. So availability is stricter than
agent_reviews/availability: enabled only when an active provider exists, has a model, and is
tool-capable. When it is not, the reason tells the user exactly what to fix in Settings.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import llm_provider_config_service
from app.services.llm_provider_clients import supports_tools

_NO_PROVIDER = "No active LLM provider is configured. Set one in Settings > Integrations > LLMs."
_NO_MODEL = "The active LLM provider has no model selected. Choose a model in Settings > Integrations > LLMs."
_NOT_TOOL_CAPABLE = (
    "Your active LLM provider does not support the assistant. Pick a tool-capable provider "
    "(Anthropic, OpenAI, or Google) in Settings > Integrations > LLMs."
)


@dataclass(frozen=True)
class AssistantAvailability:
    enabled: bool
    reason: str | None = None


class AssistantAvailabilityService:
    @staticmethod
    async def get_availability(session: AsyncSession, org_id: int) -> AssistantAvailability:
        active = await llm_provider_config_service.get_active(session, org_id)
        if active is None:
            return AssistantAvailability(False, _NO_PROVIDER)
        if not active.model:
            return AssistantAvailability(False, _NO_MODEL)
        if not supports_tools(active.provider):
            return AssistantAvailability(False, _NOT_TOOL_CAPABLE)
        return AssistantAvailability(True, None)
