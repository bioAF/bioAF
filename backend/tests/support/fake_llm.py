"""One model client at its own boundary, for tests that accept model-assisted behaviour.

It answers what it is told to answer, in order, and records how many times it was asked. The
transport and the provider are the stubbed boundary; nothing above them is stubbed, because the
behaviour being accepted is what bioAF does with an answer.
"""

from __future__ import annotations

import json

from app.services.llm_provider_clients import ProviderError


class FakeClient:
    """``answers`` are returned in order; a `ProviderError` entry raises instead."""

    def __init__(self, answers, *, model="fake-model"):
        self.answers = list(answers)
        self.model = model
        self.asked: list[dict] = []

    @property
    def calls(self) -> int:
        return len(self.asked)

    async def submit(self, *, prompt, payload, model=None, api_key=None, max_tokens=None, **kw):
        self.asked.append({"system": prompt, "payload": payload, "max_tokens": max_tokens})
        if not self.answers:
            raise ProviderError("the fake client was asked more times than it was given answers")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, str):
            return answer
        return f"```json\n{json.dumps(answer)}\n```"
