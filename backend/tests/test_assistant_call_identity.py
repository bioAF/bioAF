"""Tool-call identity through persistence (plan 12.5).

The provider's call id must survive storage so each tool result is replayed against the call that
produced it, by link, never by adjacency, tool name, or argument equality. Pending (unconfirmed)
calls are reported truthfully, legacy histories without links never get guessed associations, and
none of the internal link data reaches public transcript or plan fields.
"""

import json

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models.assistant import AssistantConversation, AssistantMessage, AssistantToolInvocation
from app.models.experiment import Experiment
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.sample import Sample
from app.services import llm_provider_config_service
from app.services.assistant_loop_service import AssistantLoopService
from app.services.llm_provider_clients.tool_use import ToolCall, ToolUseResult

pytestmark = pytest.mark.asyncio


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _scripted_capturing(store, *results):
    state = {"i": 0}

    async def _submit(messages, tools):
        store.append(messages)
        i = state["i"]
        state["i"] += 1
        return results[min(i, len(results) - 1)]

    return _submit


async def _conversation(session, user):
    conv = AssistantConversation(organization_id=user.organization_id, user_id=user.id, title="t")
    session.add(conv)
    await session.flush()
    await session.commit()
    return conv


async def _experiment(session, user):
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
    await session.flush()
    await session.commit()
    return exp


def _tool_entries_after_first_tool_call(history):
    """The assistant tool-call entry and the tool entries that immediately follow it."""
    idx = next(i for i, m in enumerate(history) if m["role"] == "assistant" and m.get("tool_calls"))
    tools = []
    for m in history[idx + 1 :]:
        if m["role"] != "tool":
            break
        tools.append(m)
    return history[idx], tools


async def _mixed_turn(session, admin_user):
    """One model turn proposing a consequential call FIRST and a read call SECOND."""
    exp = await _experiment(session, admin_user)
    conv = await _conversation(session, admin_user)
    launch_args = {"experiment_id": exp.id, "pipeline_key": "nf-core/rnaseq"}
    first = await AssistantLoopService.run_turn(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        user_text="launch rnaseq and tell me what you recommend",
        submit_override=_scripted_capturing(
            [],
            ToolUseResult(
                tool_calls=[
                    ToolCall(tool="launch_run", args=launch_args, id="call_launch"),
                    ToolCall(tool="recommend_pipeline", args={"experiment_id": exp.id}, id="call_read"),
                ]
            ),
        ),
    )
    assert first.status == "awaiting_confirmation"
    return exp, conv, launch_args


async def test_results_follow_call_ids_when_a_pending_call_precedes_a_read(session, admin_user):
    _, conv, _ = await _mixed_turn(session, admin_user)

    seen: list = []
    await AssistantLoopService.run_turn(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        user_text="and?",
        submit_override=_scripted_capturing(seen, ToolUseResult(text="done")),
    )

    assistant, tools = _tool_entries_after_first_tool_call(seen[0])
    assert [c["call_id"] for c in assistant["tool_calls"]] == ["call_launch", "call_read"]
    by_id = {t["tool_call_id"]: t["content"] for t in tools}
    assert set(by_id) == {"call_launch", "call_read"}
    # The read result belongs to the read call...
    assert "nf-core/rnaseq" in by_id["call_read"]
    assert "awaiting_confirmation" not in by_id["call_read"]
    # ...and the launch is reported as proposed, never as executed.
    assert "awaiting_confirmation" in by_id["call_launch"]
    assert "succeeded" not in by_id["call_launch"]

    statuses = {
        inv.tool_name: inv.status
        for inv in (
            await session.execute(
                select(AssistantToolInvocation).where(AssistantToolInvocation.conversation_id == conv.id)
            )
        ).scalars()
    }
    assert statuses == {"launch_run": "awaiting_confirmation", "recommend_pipeline": "succeeded"}


async def test_repeated_identical_calls_keep_their_own_results(session, admin_user):
    exp = await _experiment(session, admin_user)
    conv = await _conversation(session, admin_user)
    args = {"experiment_id": exp.id}

    seen: list = []
    await AssistantLoopService.run_turn(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        user_text="list twice",
        submit_override=_scripted_capturing(
            seen,
            ToolUseResult(
                tool_calls=[
                    ToolCall(tool="list_samples", args=args, id="call_a"),
                    ToolCall(tool="list_samples", args=args, id="call_b"),
                ]
            ),
            ToolUseResult(text="two lists"),
        ),
    )

    _, tools = _tool_entries_after_first_tool_call(seen[1])
    assert [t["tool_call_id"] for t in tools] == ["call_a", "call_b"]
    assert all("B1" in t["content"] for t in tools)


async def test_public_transcript_and_plan_fields_carry_no_internal_links(client, session, admin_user, admin_token):
    _, conv, launch_args = await _mixed_turn(session, admin_user)

    resp = await client.get(f"/api/assistant/conversations/{conv.id}/messages", headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    assert "_bioaf" not in resp.text
    assert "call_launch" not in resp.text
    body = resp.json()
    tool_call_messages = [m for m in body["messages"] if m["tool_calls"]]
    assert tool_call_messages[0]["tool_calls"] == [
        {"tool": "launch_run", "args": launch_args},
        {"tool": "recommend_pipeline", "args": {"experiment_id": launch_args["experiment_id"]}},
    ]
    # Messages without tool calls still serialize tool_calls as null.
    assert all(m["tool_calls"] is None for m in body["messages"] if m["role"] != "assistant" or not m["tool_calls"])
    assert body["plans"][0]["steps"] == [
        {"tool": "launch_run", "args": launch_args, "consequence_class": "spend"},
    ]


async def test_message_response_plan_steps_carry_no_internal_links(client, session, admin_user, admin_token):
    await llm_provider_config_service.upsert(
        session,
        org_id=admin_user.organization_id,
        provider="anthropic",
        api_key="sk-ant-LAST5",
        model="claude-x",
        actor_user_id=admin_user.id,
    )
    await llm_provider_config_service.set_active(
        session, org_id=admin_user.organization_id, provider="anthropic", actor_user_id=admin_user.id
    )
    await session.commit()
    exp = await _experiment(session, admin_user)
    conv_id = (await client.post("/api/assistant/conversations", json={}, headers=_auth(admin_token))).json()["id"]

    tool_use = {
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "launch_run",
                "input": {"experiment_id": exp.id, "pipeline_key": "nf-core/rnaseq"},
            }
        ],
        "stop_reason": "tool_use",
    }
    with respx.mock(base_url="https://api.anthropic.com/v1") as r:
        r.post("/messages").mock(return_value=httpx.Response(200, json=tool_use))
        msg = await client.post(
            f"/api/assistant/conversations/{conv_id}/messages", json={"text": "run it"}, headers=_auth(admin_token)
        )

    assert msg.status_code == 200, msg.text
    assert "_bioaf" not in msg.text
    assert msg.json()["plan_steps"] == [
        {
            "tool": "launch_run",
            "args": {"experiment_id": exp.id, "pipeline_key": "nf-core/rnaseq"},
            "consequence_class": "spend",
        }
    ]


async def _legacy_conversation(session, admin_user, *, calls, results):
    conv = await _conversation(session, admin_user)
    session.add(AssistantMessage(conversation_id=conv.id, role="user", content="earlier question"))
    await session.flush()
    session.add(AssistantMessage(conversation_id=conv.id, role="assistant", content=None, tool_calls_json=calls))
    await session.flush()
    for content in results:
        session.add(AssistantMessage(conversation_id=conv.id, role="tool", content=content))
        await session.flush()
    session.add(AssistantMessage(conversation_id=conv.id, role="assistant", content="earlier answer"))
    await session.flush()
    await session.commit()
    return conv


async def test_legacy_history_with_a_result_for_every_call_is_replayed_in_call_order(session, admin_user):
    conv = await _legacy_conversation(
        session,
        admin_user,
        calls=[{"tool": "list_samples", "args": {"experiment_id": 1}}, {"tool": "list_pipelines", "args": {}}],
        results=[
            json.dumps({"status": "succeeded", "result": "LEGACY-ONE"}),
            json.dumps({"status": "succeeded", "result": "LEGACY-TWO"}),
        ],
    )
    seen: list = []
    await AssistantLoopService.run_turn(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        user_text="continue",
        submit_override=_scripted_capturing(seen, ToolUseResult(text="ok")),
    )

    assistant, tools = _tool_entries_after_first_tool_call(seen[0])
    ids = [c["call_id"] for c in assistant["tool_calls"]]
    assert len(set(ids)) == 2 and all(ids)
    assert [t["tool_call_id"] for t in tools] == ids
    assert "LEGACY-ONE" in tools[0]["content"] and "LEGACY-TWO" in tools[1]["content"]


async def test_ambiguous_legacy_history_never_guesses_or_claims_an_outcome(session, admin_user):
    """Before call tracking, a consequential call left no result row, so one result for two calls
    cannot be attributed. Neither call may receive it or a claimed status."""
    conv = await _legacy_conversation(
        session,
        admin_user,
        calls=[
            {"tool": "launch_run", "args": {"experiment_id": 1, "pipeline_key": "nf-core/rnaseq"}},
            {"tool": "list_samples", "args": {"experiment_id": 1}},
        ],
        results=[json.dumps({"status": "succeeded", "result": "LEGACY-ORPHAN"})],
    )
    seen: list = []
    await AssistantLoopService.run_turn(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        user_text="did it launch?",
        submit_override=_scripted_capturing(seen, ToolUseResult(text="ok")),
    )

    history = seen[0]
    _, tools = _tool_entries_after_first_tool_call(history)
    assert len(tools) == 2
    for t in tools:
        assert "LEGACY-ORPHAN" not in t["content"]
        assert "awaiting_confirmation" not in t["content"]
        assert "succeeded" not in t["content"]
        assert "not recorded" in t["content"]
    # The orphan result is kept as context, not dropped, but not attached to a call.
    assert any("LEGACY-ORPHAN" in (m.get("content") or "") for m in history if m["role"] == "assistant")
