"""The assistant loop driving the real OpenAI adapter end to end (plan 12.5, finding L2).

No submit_override: the loop resolves the active provider, the actual openai_client builds the
Responses request, respx answers it, and tools run through the enforcement wrapper. This proves the
request -> tool call -> result -> final answer round trip, persistence and replay of the returned
items across a reload and a second user turn, and that opaque state never reaches another
provider, another model, or a public field.
"""

import json

import httpx
import pytest
import respx

from app.models.assistant import AssistantConversation
from app.models.experiment import Experiment
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.sample import Sample
from app.services import llm_provider_config_service
from app.services.assistant_loop_service import AssistantLoopService

pytestmark = pytest.mark.asyncio

MODEL = "gpt-6.1-sol"
REASONING_1 = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "ENC-OPAQUE-1"}
REASONING_2 = {"type": "reasoning", "id": "rs_2", "summary": [], "encrypted_content": "ENC-OPAQUE-2"}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _fc(call_id, name, args):
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(args),
        "status": "completed",
    }


def _msg(text, item_id="msg_1"):
    return {
        "type": "message",
        "id": item_id,
        "role": "assistant",
        "status": "completed",
        "phase": "final_answer",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _resp(*output):
    return httpx.Response(200, json={"id": "resp", "object": "response", "status": "completed", "output": list(output)})


async def _activate(session, user, provider, model, api_key="sk-SYNTH-KEY"):
    await llm_provider_config_service.upsert(
        session, org_id=user.organization_id, provider=provider, api_key=api_key, model=model, actor_user_id=user.id
    )
    await llm_provider_config_service.set_active(
        session, org_id=user.organization_id, provider=provider, actor_user_id=user.id
    )
    await session.commit()


async def _setup(session, user):
    exp = Experiment(
        organization_id=user.organization_id, name="Bulk mouse", owner_user_id=user.id, status="fastq_uploaded"
    )
    session.add(exp)
    await session.flush()
    session.add(
        Sample(
            experiment_id=exp.id,
            external_id="B1",
            organism="Mus musculus",
            molecule_type="total RNA",
            library_prep_method="TruSeq Stranded mRNA",
        )
    )
    session.add(
        PipelineCatalogEntry(
            organization_id=user.organization_id,
            pipeline_key="nf-core/rnaseq",
            name="nf-core/rnaseq",
            source_type="github",
            version="3.14.0",
            default_params_json={"aligner": "star_salmon"},
            enabled=True,
        )
    )
    conv = AssistantConversation(organization_id=user.organization_id, user_id=user.id, title="t")
    session.add(conv)
    await session.flush()
    await session.commit()
    await _activate(session, user, "openai", MODEL)
    return exp, conv


def _bodies(route):
    return [json.loads(call.request.content) for call in route.calls]


async def _first_turn(session, admin_user):
    exp, conv = await _setup(session, admin_user)
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        route = r.post("/responses").mock(
            side_effect=[
                _resp(REASONING_1, _fc("call_1", "recommend_pipeline", {"experiment_id": exp.id})),
                _resp(REASONING_2, _msg("Run nf-core/rnaseq.")),
            ]
        )
        result = await AssistantLoopService.run_turn(
            session, conversation=conv, role_id=admin_user.role_id, user_text="what should I run?"
        )
    return exp, conv, result, _bodies(route)


async def test_tool_round_trip_through_the_real_adapter(session, admin_user):
    _, _, result, bodies = await _first_turn(session, admin_user)

    assert result.status == "answered"
    assert result.text == "Run nf-core/rnaseq."
    assert len(bodies) == 2
    for body in bodies:
        assert body["model"] == MODEL and body["store"] is False and "reasoning" not in body
    second = bodies[1]["input"]
    assert second[0] == {"role": "user", "content": "what should I run?"}
    # The first response's items are replayed verbatim, then the result for that call id.
    assert second[1] == REASONING_1
    assert second[2]["type"] == "function_call" and second[2]["call_id"] == "call_1"
    assert second[3]["type"] == "function_call_output" and second[3]["call_id"] == "call_1"
    assert "nf-core/rnaseq" in second[3]["output"]
    assert len(second) == 4


async def test_reload_and_second_turn_replay_the_complete_history_in_order(session, admin_user):
    _, conv, _, _ = await _first_turn(session, admin_user)
    session.expunge_all()
    conv = await session.get(AssistantConversation, conv.id)

    with respx.mock(base_url="https://api.openai.com/v1") as r:
        route = r.post("/responses").mock(return_value=_resp(_msg("Yes.", item_id="msg_2")))
        result = await AssistantLoopService.run_turn(
            session, conversation=conv, role_id=admin_user.role_id, user_text="is that the best?"
        )

    assert result.status == "answered"
    items = _bodies(route)[0]["input"]
    kinds = [i.get("type") or i.get("role") for i in items]
    assert kinds == ["user", "reasoning", "function_call", "function_call_output", "reasoning", "message", "user"]
    assert items[4] == REASONING_2
    assert items[5] == _msg("Run nf-core/rnaseq.")  # phase preserved
    assert items[6] == {"role": "user", "content": "is that the best?"}


async def test_a_pending_and_a_read_call_in_one_response_both_get_outputs(session, admin_user):
    exp, conv = await _setup(session, admin_user)
    launch = {"experiment_id": exp.id, "pipeline_key": "nf-core/rnaseq"}
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        r.post("/responses").mock(
            return_value=_resp(
                REASONING_1,
                _fc("call_launch", "launch_run", launch),
                _fc("call_read", "recommend_pipeline", {"experiment_id": exp.id}),
            )
        )
        first = await AssistantLoopService.run_turn(
            session, conversation=conv, role_id=admin_user.role_id, user_text="launch and recommend"
        )
    assert first.status == "awaiting_confirmation"

    with respx.mock(base_url="https://api.openai.com/v1") as r:
        route = r.post("/responses").mock(return_value=_resp(_msg("Waiting for you.")))
        await AssistantLoopService.run_turn(session, conversation=conv, role_id=admin_user.role_id, user_text="status?")

    outputs = {i["call_id"]: i["output"] for i in _bodies(route)[0]["input"] if i.get("type") == "function_call_output"}
    assert set(outputs) == {"call_launch", "call_read"}
    assert "awaiting_confirmation" in outputs["call_launch"]
    assert "nf-core/rnaseq" in outputs["call_read"]


async def test_model_change_does_not_replay_opaque_items(session, admin_user):
    _, conv, _, _ = await _first_turn(session, admin_user)
    await _activate(session, admin_user, "openai", "gpt-6.1-other")

    with respx.mock(base_url="https://api.openai.com/v1") as r:
        route = r.post("/responses").mock(return_value=_resp(_msg("ok")))
        await AssistantLoopService.run_turn(session, conversation=conv, role_id=admin_user.role_id, user_text="again")

    raw = route.calls.last.request.content.decode()
    assert "ENC-OPAQUE" not in raw
    items = _bodies(route)[0]["input"]
    # The tool exchange is still present, rebuilt from the normalized calls.
    assert any(i.get("type") == "function_call" and i["call_id"] == "call_1" for i in items)
    assert any(i.get("type") == "function_call_output" and i["call_id"] == "call_1" for i in items)


async def test_other_providers_never_receive_openai_state(session, admin_user):
    _, conv, _, _ = await _first_turn(session, admin_user)
    await _activate(session, admin_user, "anthropic", "claude-x")

    with respx.mock(base_url="https://api.anthropic.com/v1") as r:
        route = r.post("/messages").mock(
            return_value=httpx.Response(
                200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
            )
        )
        result = await AssistantLoopService.run_turn(
            session, conversation=conv, role_id=admin_user.role_id, user_text="again"
        )

    assert result.status == "answered"
    raw = route.calls.last.request.content.decode()
    assert "ENC-OPAQUE" not in raw
    assert "encrypted_content" not in raw
    assert "_bioaf" not in raw and "provider_state" not in raw


async def test_transcript_exposes_no_opaque_state(client, session, admin_user, admin_token):
    _, conv, _, _ = await _first_turn(session, admin_user)
    resp = await client.get(f"/api/assistant/conversations/{conv.id}/messages", headers=_auth(admin_token))
    assert resp.status_code == 200
    assert "ENC-OPAQUE" not in resp.text
    assert "_bioaf" not in resp.text
    assert "call_1" not in resp.text
    messages = resp.json()["messages"]
    # The final answer message (which stored items internally) has no public tool_calls.
    assert messages[-1]["content"] == "Run nf-core/rnaseq." and messages[-1]["tool_calls"] is None
