"""The chat assistant with a scripted stand-in for the model: tools, checks on answers, confirm cards."""

import json
import uuid
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import text

from bidwright import chat
from bidwright.db import engine, workspace
from bidwright.engine import run_engine
from test_gate1 import new_request, say


class Block(NS):
    def model_dump(self, **_):
        return {k: v for k, v in vars(self).items()}


def tool_use(name, **args):
    return NS(stop_reason="tool_use", content=[Block(type="tool_use", id=f"t{uuid.uuid4().hex[:6]}", name=name, input=args)])


def answer(text_):
    return NS(stop_reason="end_turn", content=[Block(type="text", text=text_)])


@pytest.fixture
def scripted(monkeypatch):
    """Replies the model would give, in order; the test checks what the server did with them."""
    script = []
    monkeypatch.setattr(chat, "call_model", lambda system, messages, trace: script.pop(0))
    return script


def test_a_pasted_message_becomes_a_request_with_a_card(stranger, scripted, monkeypatch):
    client, org = stranger
    monkeypatch.setattr(chat, "extract_envelope", lambda e: run_engine(e))  # reading without the model
    scripted += [tool_use("read_request", text="Hi, concept for a tower in Doha please", channel="whatsapp"),
                 answer("I have read it. It needs more before pricing.")]
    r = client.post("/api/chat", json={"message": "Hi, concept for a tower in Doha please"})
    assert r.status_code == 200, r.text
    t = r.json()
    reply = t["messages"][-1]
    assert t["envelope_id"] and reply["role"] == "assistant" and reply["cards"][0]["type"] == "request"
    assert reply["cards"][0]["envelope_id"] == t["envelope_id"] and reply["cards"][0]["headline"]
    assert client.get(f"/api/envelopes/{t['envelope_id']}/chat").json()["thread_id"] == t["thread_id"]


def test_an_answer_may_only_use_figures_and_sources_from_its_tools():
    turn = chat.Turn(None, 1, None, "what is the fee?")
    turn.keep({"opening_fee": 910400, "won": {"low": 883000.0, "high": 989000.0}}, [{"id": 5, "why": "x"}])
    assert chat.problems("The fee is AED 910,400 [ev:5]; won bids landed 883k–989k.", turn) == []
    assert "figures that are in no tool result: ['1200000']" in chat.problems("About AED 1,200,000.", turn)[0]
    assert "evidence ids not returned" in chat.problems("Stated [ev:99].", turn)[0]
    assert "108.5" in chat.problems("That is about 108.5% of the opening fee.", turn)[0]  # worked out, not given
    assert "2.4M" in chat.problems("Roughly 2.4M.", turn)[0]


def test_an_answer_with_an_invented_figure_is_never_shown(stranger, scripted):
    client, _ = stranger
    scripted += [answer("Probably around AED 2,400,000."), answer("Still AED 2,400,000.")]
    reply = client.post("/api/chat", json={"message": "Rough fee for a tower?"}).json()["messages"][-1]
    assert "2,400,000" not in reply["text"] and "won't state it" in reply["text"]


def test_a_proposed_decision_waits_for_a_person_and_runs_once(stranger, scripted):
    client, org = stranger
    e, d = new_request(org, "Can you quote a hotel in Doha?")
    say(org, e, d, "city", "Doha", norm="Doha, QA")
    with workspace(org):
        run_engine(e)
    scripted += [tool_use("propose_decision", request_id=e, decision="decline"),
                 tool_use("propose_decision", request_id=e, decision="decline", reason="Too small for us"),
                 answer("Ready to confirm: decline, too small.")]
    t = client.post("/api/chat", json={"message": "decline it", "envelope_id": e}).json()
    actions = t["messages"][-1]["actions"]
    assert len(actions) == 1 and actions[0]["status"] == "pending"  # the decline without a reason was refused
    with workspace(org), engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM intake.gate_decision WHERE envelope_id = :e"), dict(e=e)).scalar() == 0
    done = client.post(f"/api/chat/actions/{actions[0]['action_id']}/confirm").json()
    assert done["messages"][-1]["role"] == "system" and done["messages"][-1]["status"] == "confirmed"
    assert [m for m in done["messages"] if m["role"] == "assistant"][-1]["actions"][0]["status"] == "confirmed"
    assert client.post(f"/api/chat/actions/{actions[0]['action_id']}/confirm").status_code == 409
    v = client.get(f"/api/envelopes/{e}/gate1").json()
    assert v["decision"]["decision"] == "decline" and v["decision"]["reason"] == "Too small for us"
