"""Shared transport behaviour for the provider clients (plan_7 step 14b).

Two things every client needs and none of them had.

**Refusal is a class of its own.** ``ProviderError.error_class`` was
``auth | rate_limit | transport | server | parse | other``, so a model that declined to answer had
nowhere to be reported. It arrived as prose with no fence (Anthropic), a ``None`` content (OpenAI),
or a ``KeyError`` reported as ``parse`` (Google), and in every case the study degraded with nothing
on screen. ``llm_decision`` cannot classify what the transport threw away, which is why this lives
below it.

**A 429 or a 5xx is worth one more try.** Putting the retry here means every caller gets rate-limit
resilience without writing a loop, and the decision helper above stays free of any retry concept.
The semantic re-ask (asking the model again with its own last answer in front of it) is a different
thing entirely and stays with the caller that knows a second look is worth paying for.

Nothing else is retried. A bad API key will be just as bad in two seconds, and asking a model the
same question again after it declined is not resilience.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx

from app.services.llm_provider_clients import ProviderError

logger = logging.getLogger("bioaf.llm.transport")

# One retry is the useful one: it clears a transient 502 and a burst rate limit. A longer ladder
# turns a provider outage into a study that hangs for minutes per call.
MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (1.0, 3.0)

RETRYABLE_STATUSES = (429,)


def is_retryable(status: int) -> bool:
    return status in RETRYABLE_STATUSES or status >= 500


async def request_with_retry(send: Callable[[], Awaitable[httpx.Response]], *, what: str) -> httpx.Response:
    """Send, retrying a 429, a 5xx and a dropped connection. Returns the last response.

    The caller still classifies the status: a retry that never succeeded must not change what the
    caller is told, so an exhausted 429 is still ``rate_limit``.
    """
    last_exc: httpx.HTTPError | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = await send()
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt == MAX_ATTEMPTS - 1:
                break
            logger.warning("%s transport failure (attempt %d), retrying: %s", what, attempt + 1, exc)
            await asyncio.sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
            continue
        if not is_retryable(resp.status_code) or attempt == MAX_ATTEMPTS - 1:
            return resp
        logger.warning("%s returned %d (attempt %d), retrying", what, resp.status_code, attempt + 1)
        await asyncio.sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])

    detail = str(last_exc).strip() or repr(last_exc)
    logger.warning("%s transport failure: %s", what, detail)
    raise ProviderError(detail, error_class="transport") from last_exc


def refusal(message: str) -> ProviderError:
    """The model declined. Named so an administrator can request an account exception for it."""
    return ProviderError(message, error_class="refusal")
