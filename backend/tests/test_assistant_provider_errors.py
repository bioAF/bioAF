"""Expected provider failures at the assistant message boundary (plan 12.5, finding L2).

Before this change a ProviderError escaped POST /messages as an unhandled 500 and the UI showed
"Unknown error". Each expected failure now returns controlled JSON with a status that never looks
like a bioAF login expiry, already committed messages and tool results stay truthful, nothing raw
from the provider reaches the response or the logs, and a later turn recovers without repeating
completed tools.
"""

import json
import logging

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models.assistant import AssistantMessage, AssistantToolInvocation
from app.models.experiment import Experiment
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.sample import Sample
from app.services import llm_provider_config_service

pytestmark = pytest.mark.asyncio

MODEL = "gpt-6.1-sol"
API_KEY = "sk-SYNTH-SECRET-LLM-KEY-123456"
MARKERS = ("SYNTH-SECRET", "SYNTH-PROVIDER-BODY", "SYNTH-PRIVATE-INPUT", "api.openai.com")
USER_TEXT = "SYNTH-PRIVATE-INPUT what should I run?"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _provider_error_body(code="unsupported_parameter"):
    return {
        "error": {
            "message": "SYNTH-PROVIDER-BODY Function tools with reasoning_effort are not supported in /v1/chat/completions",
            "type": "invalid_request_error",
            "param": "tools",
            "code": code,
        }
    }


def _completed(*output):
    return httpx.Response(200, json={"id": "resp", "status": "completed", "output": list(output)})


def _fc(call_id, name, args):
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(args),
    }


def _msg(text):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


async def _setup(client, session, user, token):
    await llm_provider_config_service.upsert(
        session, org_id=user.organization_id, provider="openai", api_key=API_KEY, model=MODEL, actor_user_id=user.id
    )
    await llm_provider_config_service.set_active(
        session, org_id=user.organization_id, provider="openai", actor_user_id=user.id
    )
    exp = Experiment(
        organization_id=user.organization_id, name="Bulk mouse", owner_user_id=user.id, status="fastq_uploaded"
    )
    session.add(exp)
    await session.flush()
    session.add(Sample(experiment_id=exp.id, external_id="B1", organism="Mus musculus", molecule_type="total RNA"))
    session.add(
        PipelineCatalogEntry(
            organization_id=user.organization_id,
            pipeline_key="nf-core/rnaseq",
            name="nf-core/rnaseq",
            source_type="github",
            version="3.14.0",
            default_params_json={},
            enabled=True,
        )
    )
    await session.commit()
    conv_id = (await client.post("/api/assistant/conversations", json={}, headers=_auth(token))).json()["id"]
    return exp, conv_id


async def _send(client, token, conv_id, text=USER_TEXT):
    return await client.post(
        f"/api/assistant/conversations/{conv_id}/messages", json={"text": text}, headers=_auth(token)
    )


def _assert_safe(response):
    for marker in MARKERS:
        assert marker not in response.text
    assert API_KEY not in response.text
    detail = response.json()["detail"]
    assert isinstance(detail, str) and detail


async def _messages(session, conv_id):
    rows = (
        await session.execute(
            select(AssistantMessage).where(AssistantMessage.conversation_id == conv_id).order_by(AssistantMessage.id)
        )
    ).scalars()
    return [(m.role, m.content) for m in rows]


CASES = [
    (
        "incompatible request (L2)",
        httpx.Response(400, json=_provider_error_body()),
        502,
        "provider and model configuration",
    ),
    ("provider auth", httpx.Response(401, json=_provider_error_body("invalid_api_key")), 502, "API key"),
    ("provider forbidden", httpx.Response(403, json=_provider_error_body("forbidden")), 502, "API key"),
    ("rate limited", httpx.Response(429, json=_provider_error_body("rate_limit_exceeded")), 429, "try again"),
    ("server failure", httpx.Response(503, text="SYNTH-PROVIDER-BODY upstream down"), 502, "temporarily unavailable"),
    ("timeout", httpx.ReadTimeout("SYNTH-SECRET read timed out"), 504, "did not respond in time"),
    ("connection failure", httpx.ConnectError("SYNTH-SECRET refused"), 502, "temporarily unavailable"),
    (
        "incomplete response",
        httpx.Response(
            200, json={"status": "incomplete", "output": [], "incomplete_details": {"reason": "max_output_tokens"}}
        ),
        502,
        "stopped before finishing",
    ),
    ("unusable response", httpx.Response(200, json={"status": "completed", "output": []}), 502, "could not use"),
]


@pytest.mark.parametrize("label,upstream,expected_status,fragment", CASES, ids=[c[0] for c in CASES])
async def test_provider_failure_before_any_tool_is_a_controlled_error(
    client, session, admin_user, admin_token, label, upstream, expected_status, fragment
):
    _, conv_id = await _setup(client, session, admin_user, admin_token)
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        route = r.post("/responses")
        if isinstance(upstream, Exception):
            route.mock(side_effect=upstream)
        else:
            route.mock(return_value=upstream)
        response = await _send(client, admin_token, conv_id)

    assert response.status_code == expected_status, response.text
    assert response.status_code != 401  # a provider credential failure is not a bioAF login expiry
    _assert_safe(response)
    assert fragment.lower() in response.json()["detail"].lower()
    # The user's message was already committed; no answer or plan was invented.
    assert await _messages(session, conv_id) == [("user", USER_TEXT)]


async def test_failure_after_a_completed_read_keeps_truthful_state_and_recovers(
    client, session, admin_user, admin_token
):
    exp, conv_id = await _setup(client, session, admin_user, admin_token)
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        r.post("/responses").mock(
            side_effect=[
                _completed(_fc("call_1", "recommend_pipeline", {"experiment_id": exp.id})),
                httpx.Response(503, text="SYNTH-PROVIDER-BODY"),
            ]
        )
        failed = await _send(client, admin_token, conv_id)

    assert failed.status_code == 502
    _assert_safe(failed)
    roles = [role for role, _ in await _messages(session, conv_id)]
    assert roles == ["user", "assistant", "tool"]  # the read ran and its result is kept; no answer
    invocations = list(
        (
            await session.execute(
                select(AssistantToolInvocation).where(AssistantToolInvocation.conversation_id == conv_id)
            )
        ).scalars()
    )
    assert [(i.tool_name, i.status) for i in invocations] == [("recommend_pipeline", "succeeded")]

    with respx.mock(base_url="https://api.openai.com/v1") as r:
        route = r.post("/responses").mock(return_value=_completed(_msg("Run nf-core/rnaseq.")))
        retried = await _send(client, admin_token, conv_id, text="try again")

    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "answered"
    items = json.loads(route.calls.last.request.content)["input"]
    assert any(i.get("type") == "function_call_output" and i["call_id"] == "call_1" for i in items)
    # The completed read was not executed again.
    count = len(
        list(
            (
                await session.execute(
                    select(AssistantToolInvocation).where(AssistantToolInvocation.conversation_id == conv_id)
                )
            ).scalars()
        )
    )
    assert count == 1


async def test_provider_failure_diagnostics_are_allowlisted(client, session, admin_user, admin_token, caplog):
    """caplog's handler has no RedactSecretsFilter, so this proves exclusion at the source."""
    _, conv_id = await _setup(client, session, admin_user, admin_token)
    caplog.set_level(logging.DEBUG)
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        r.post("/responses").mock(return_value=httpx.Response(400, json=_provider_error_body()))
        await _send(client, admin_token, conv_id)
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        r.post("/responses").mock(side_effect=httpx.ConnectError("SYNTH-SECRET refused"))
        await _send(client, admin_token, conv_id)

    formatter = logging.Formatter("%(name)s %(levelname)s %(message)s")
    # Records that reach output in production: the bioaf logger tree has handlers at INFO; any other
    # logger falls through to the root, which has no handler, so only WARNING and above surface (via
    # logging's last-resort handler). httpx's INFO request lines therefore never reach a sink.
    emitted = [r for r in caplog.records if r.name.startswith("bioaf") or r.levelno >= logging.WARNING]
    assert emitted
    for record in emitted:
        rendered = formatter.format(record) + repr(record.__dict__)
        for marker in MARKERS:
            assert marker not in rendered
        assert API_KEY not in rendered
        if record.name.startswith(("bioaf.assistant", "bioaf.llm")):
            assert record.exc_info is None
    diagnostics = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("bioaf.assistant"))
    assert "provider=openai" in diagnostics
    assert "error_class=other" in diagnostics and "upstream_status=400" in diagnostics
    assert "upstream_code=unsupported_parameter" in diagnostics
    assert "error_class=transport" in diagnostics


@pytest.mark.parametrize("status", [408, 504])
async def test_upstream_timeout_status_returns_504(client, session, admin_user, admin_token, status):
    _, conv_id = await _setup(client, session, admin_user, admin_token)
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        r.post("/responses").mock(return_value=httpx.Response(status, json={"error": {"code": "request_timeout"}}))
        response = await _send(client, admin_token, conv_id)
    assert response.status_code == 504, response.text
    assert "did not respond in time" in response.json()["detail"]


async def test_unrecognized_upstream_code_never_reaches_logs(client, session, admin_user, admin_token, caplog):
    marker = "sk-SYNTH-ORG-KEY-REVIEW-123456"
    _, conv_id = await _setup(client, session, admin_user, admin_token)
    caplog.set_level(logging.DEBUG)
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        r.post("/responses").mock(return_value=httpx.Response(400, json={"error": {"code": marker, "type": marker}}))
        response = await _send(client, admin_token, conv_id)
    assert response.status_code == 502
    assert marker not in caplog.text
    assert "upstream_code=none" in caplog.text
