"""RPC handler tests for chat.clarify_submit (PR5 Task 2).

Verifies:
* The pure helper ``_clarify_fields_to_text`` serializes the form dict
  into the deterministic ``key: value\\n`` form that
  ``opensquilla.skills.meta.clarify_text.parse_clarify_reply`` accepts.
* The RPC handler rejects malformed params with a clear error.
* The RPC handler forwards a normal ``chat.send`` call when given a
  valid submission (handler internals are unit-tested elsewhere).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from opensquilla.gateway.rpc import RpcContext, RpcHandlerError
from opensquilla.gateway.rpc_chat import (
    _clarify_fields_to_text,
    _handle_chat_clarify_submit,
)
from opensquilla.persistence.meta_run_writer import AwaitingPeek
from opensquilla.skills.meta.clarify_text import parse_clarify_reply
from opensquilla.skills.meta.types import ClarifyField, ClarifyStepConfig

# ── pure helper ──

def test_clarify_fields_to_text_basic():
    text = _clarify_fields_to_text({
        "destination": "Tokyo",
        "days": 5,
        "party_size": 2,
        "budget": "mid",
    })
    # Order is dict iteration order (preserved in py3.7+).
    assert "destination: Tokyo" in text
    assert "days: 5" in text
    assert "party_size: 2" in text
    assert "budget: mid" in text


def test_clarify_fields_to_text_bool_renders_lowercase():
    text = _clarify_fields_to_text({"enabled": True, "disabled": False})
    assert "enabled: true" in text
    assert "disabled: false" in text


def test_clarify_fields_to_text_skips_empty_and_none():
    text = _clarify_fields_to_text({
        "destination": "Tokyo",
        "notes": "",
        "extra": None,
        "days": 5,
    })
    # Empty/None fields not included (signal "user left it blank").
    assert "notes" not in text
    assert "extra" not in text
    assert "destination: Tokyo" in text
    assert "days: 5" in text


def test_clarify_fields_to_text_roundtrips_through_real_parser():
    """The whole point: the synthetic text must be parseable by
    clarify_text.parse_clarify_reply so meta_resolution's awaiting
    branch picks it up exactly like a hand-typed reply."""
    schema = ClarifyStepConfig(
        mode="form",
        fields=(
            ClarifyField(name="destination", type="string", required=True),
            ClarifyField(name="days", type="int", required=True, min=1, max=14),
            ClarifyField(name="party_size", type="int", required=True),
            ClarifyField(name="budget", type="enum",
                         choices=("budget", "mid", "premium")),
        ),
    )
    text = _clarify_fields_to_text({
        "destination": "Tokyo",
        "days": 5,
        "party_size": 2,
        "budget": "mid",
    })
    parsed, errors = parse_clarify_reply(text, schema, surface="web")
    assert errors == []
    assert parsed == {
        "destination": "Tokyo",
        "days": 5,
        "party_size": 2,
        "budget": "mid",
    }


# ── RPC handler ──


def _awaiting(
    *,
    run_id: str = "r-await",
    session_id: str = "agent:main:webchat:abc",
    filled_json: str = "{}",
) -> AwaitingPeek:
    return AwaitingPeek(
        run_id=run_id,
        step_id="collect",
        awaiting_since=1700000000.0,
        awaiting_session_id=session_id,
        awaiting_schema_json=(
            '{"mode":"form","fields":['
            '{"name":"destination","type":"string","required":true},'
            '{"name":"days","type":"int","required":true,"min":1,"max":30}'
            ']}'
        ),
        awaiting_filled_json=filled_json,
        step_outputs_json="{}",
        inputs_json="{}",
        parse_failure_count=0,
    )


class _FakeMetaRunWriter:
    def __init__(self, awaiting: AwaitingPeek | None) -> None:
        self.awaiting = awaiting
        self.peeked_sessions: list[str] = []

    def peek_awaiting(self, *, session_id: str) -> AwaitingPeek | None:
        self.peeked_sessions.append(session_id)
        if self.awaiting is None:
            return None
        if self.awaiting.awaiting_session_id != session_id:
            return None
        return self.awaiting


def _ctx_with_writer(writer: _FakeMetaRunWriter) -> RpcContext:
    return RpcContext(
        conn_id="c",
        principal=SimpleNamespace(role="operator"),
        turn_runner=SimpleNamespace(_meta_run_writer=writer),
    )


@pytest.mark.asyncio
async def test_clarify_submit_rejects_non_dict_params():
    ctx = RpcContext(conn_id="c", principal=SimpleNamespace(role="operator"))
    with pytest.raises(ValueError, match="sessionKey, fields"):
        await _handle_chat_clarify_submit(None, ctx)
    with pytest.raises(ValueError, match="sessionKey, fields"):
        await _handle_chat_clarify_submit("not-a-dict", ctx)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_clarify_submit_rejects_empty_fields():
    ctx = RpcContext(conn_id="c", principal=SimpleNamespace(role="operator"))
    with pytest.raises(ValueError, match="non-empty mapping"):
        await _handle_chat_clarify_submit(
            {"sessionKey": "S1", "fields": {}}, ctx,
        )
    with pytest.raises(ValueError, match="non-empty mapping"):
        await _handle_chat_clarify_submit(
            {"sessionKey": "S1", "fields": "not a dict"}, ctx,
        )


@pytest.mark.asyncio
async def test_clarify_submit_rejects_all_empty_values():
    """If every value is None / '' the resulting text would be empty
    and meta_resolution couldn't tell what the user meant."""
    ctx = RpcContext(conn_id="c", principal=SimpleNamespace(role="operator"))
    with pytest.raises(ValueError, match="only empty values"):
        await _handle_chat_clarify_submit(
            {"sessionKey": "S1", "fields": {"a": "", "b": None}}, ctx,
        )


@pytest.mark.asyncio
async def test_clarify_submit_forwards_to_chat_send(monkeypatch):
    """Valid submission: forwards a chat.send call with the serialised
    text + clarify_submit intent + run_id tagged on _source."""
    captured: dict = {}

    async def _fake_send(send_params, ctx):
        captured["send_params"] = send_params
        captured["ctx"] = ctx
        return {"ok": True, "sessionKey": send_params["sessionKey"]}

    monkeypatch.setattr(
        "opensquilla.gateway.rpc_chat._handle_chat_send",
        _fake_send,
    )

    ctx = RpcContext(conn_id="c", principal=SimpleNamespace(role="operator"))
    result = await _handle_chat_clarify_submit(
        {
            "sessionKey": "agent:main:webchat:abc",
            "fields": {"destination": "Tokyo", "days": 5},
            "run_id": "r-xyz",
        },
        ctx,
    )

    assert result["ok"] is True
    sp = captured["send_params"]
    assert "destination: Tokyo" in sp["message"]
    assert "days: 5" in sp["message"]
    # PR7 E2E fix: intent is no longer forwarded — SessionIntent enum
    # rejects unknown values, and meta_resolution's awaiting branch keys
    # off session_key + provenance tag, not intent.
    assert "intent" not in sp
    assert sp["inputProvenance"] == "clarify_form"
    src = sp["_source"]
    assert src["channel_kind"] == "webchat"
    assert src["clarify_run_id"] == "r-xyz"


@pytest.mark.asyncio
async def test_clarify_submit_works_without_run_id(monkeypatch):
    """run_id is optional; absent → no _source tag, but submission still
    flows through normally."""
    captured: dict = {}

    async def _fake_send(send_params, ctx):
        captured["send_params"] = send_params
        return {"ok": True, "sessionKey": send_params["sessionKey"]}

    monkeypatch.setattr(
        "opensquilla.gateway.rpc_chat._handle_chat_send",
        _fake_send,
    )

    ctx = RpcContext(conn_id="c", principal=SimpleNamespace(role="operator"))
    await _handle_chat_clarify_submit(
        {"sessionKey": "S1", "fields": {"x": "y"}},
        ctx,
    )
    assert "_source" not in captured["send_params"]


@pytest.mark.asyncio
async def test_clarify_submit_validates_schema_before_forwarding(monkeypatch):
    captured: dict = {}

    async def _fake_send(send_params, ctx):
        captured["send_params"] = send_params
        return {"ok": True, "sessionKey": send_params["sessionKey"]}

    monkeypatch.setattr(
        "opensquilla.gateway.rpc_chat._handle_chat_send",
        _fake_send,
    )

    writer = _FakeMetaRunWriter(
        _awaiting(filled_json='{"destination":"Tokyo"}'),
    )
    ctx = _ctx_with_writer(writer)
    result = await _handle_chat_clarify_submit(
        {
            "sessionKey": "agent:main:webchat:abc",
            "run_id": "r-await",
            "fields": {"days": "5"},
        },
        ctx,
    )

    assert result["ok"] is True
    assert writer.peeked_sessions == ["agent:main:webchat:abc"]
    message = captured["send_params"]["message"]
    assert "destination: Tokyo" in message
    assert "days: 5" in message


@pytest.mark.asyncio
async def test_clarify_submit_validation_error_does_not_forward(monkeypatch):
    async def _fake_send(send_params, ctx):  # pragma: no cover - must not run
        raise AssertionError("chat.send should not be called for invalid fields")

    monkeypatch.setattr(
        "opensquilla.gateway.rpc_chat._handle_chat_send",
        _fake_send,
    )

    writer = _FakeMetaRunWriter(_awaiting())
    ctx = _ctx_with_writer(writer)
    with pytest.raises(RpcHandlerError) as excinfo:
        await _handle_chat_clarify_submit(
            {
                "sessionKey": "agent:main:webchat:abc",
                "run_id": "r-await",
                "fields": {"destination": "Tokyo"},
            },
            ctx,
        )

    exc = excinfo.value
    assert exc.code == "meta_clarify.validation_error"
    assert exc.details["missing_fields"] == ["days"]
    assert exc.details["filled_fields"] == {"destination": "Tokyo"}


@pytest.mark.asyncio
async def test_clarify_submit_rejects_run_id_mismatch(monkeypatch):
    async def _fake_send(send_params, ctx):  # pragma: no cover - must not run
        raise AssertionError("chat.send should not be called for mismatched run")

    monkeypatch.setattr(
        "opensquilla.gateway.rpc_chat._handle_chat_send",
        _fake_send,
    )

    writer = _FakeMetaRunWriter(_awaiting(run_id="r-real"))
    ctx = _ctx_with_writer(writer)
    with pytest.raises(RpcHandlerError) as excinfo:
        await _handle_chat_clarify_submit(
            {
                "sessionKey": "agent:main:webchat:abc",
                "runId": "r-submitted",
                "fields": {"destination": "Tokyo", "days": 5},
            },
            ctx,
        )

    exc = excinfo.value
    assert exc.code == "meta_clarify.run_mismatch"
    assert exc.details["awaiting_run_id"] == "r-real"
