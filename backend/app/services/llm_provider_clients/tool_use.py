"""Normalized tool-calling types for the assistant (L4/L1).

Provider clients translate their native tool-calling responses into this provider-agnostic
shape, so the agentic loop is identical regardless of which LLM is active. One model turn is
either a final text answer or one-or-more tool calls the loop should execute and feed back.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolCall:
    tool: str
    args: dict
    id: str | None = None  # provider-supplied call id, when matching results back is needed


@dataclass(frozen=True)
class ToolUseResult:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def is_final(self) -> bool:
        """A turn with no tool calls is the model's final answer."""
        return not self.tool_calls


def object_schema(args_schema: dict) -> dict:
    """Wrap a tool's minimal args schema ({"required": [...], "properties": {...}}) into the
    JSON-Schema object form every provider's tool/function declaration expects."""
    return {
        "type": "object",
        "properties": args_schema.get("properties", {}),
        "required": args_schema.get("required", []),
    }
