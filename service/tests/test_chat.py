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
    monkeypatch.setattr(chat, "call_model", lambda system, messages, trace, *rest: script.pop(0))
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
    spans = reply["trace"]["spans"]  # what it did, for "how I got this"
    assert [s["label"] for s in spans] == ["load_conversation", "read_request", "check_figures"]
    assert spans[0]["start"] == 0  # timed from the moment the message reached the server
    assert spans[1]["kind"] == "io" and spans[1]["end"] >= spans[1]["start"] >= spans[0]["end"] and spans[1]["detail"]
    assert reply["trace"]["duration"] >= spans[-1]["end"]


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
    checks = [s for s in reply["trace"]["spans"] if s["label"] == "check_figures"]
    assert len(checks) == 2 and all(s["status"] == "error" for s in checks)  # both tries were stopped


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


# ---------------------------------------------------------------- history: owners, request links, privacy

def colleague(org, role="estimator"):
    """A second person in the same workspace, signed in on their own client."""
    from fastapi.testclient import TestClient
    from bidwright.api import app
    from bidwright.tenancy import hash_password
    email = f"colleague-{uuid.uuid4().hex[:8]}@example.com"
    with engine.begin() as conn:
        uid = conn.execute(text("""INSERT INTO tenancy.app_user (email, name, password_hash) VALUES (:e, 'Sam Colleague', :p)
                                   RETURNING user_id"""), dict(e=email, p=hash_password("correct horse 42"))).scalar_one()
        conn.execute(text("INSERT INTO tenancy.membership (org_id, user_id, role) VALUES (:o, :u, :r)"), dict(o=org, u=uid, r=role))
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": email, "password": "correct horse 42"}).status_code == 200
    if c.get("/api/auth/me").json().get("workspace", {}).get("org_id") != str(org):
        c.post("/api/auth/switch", json={"org_id": str(org)})
    return c, email


def test_a_conversation_has_an_owner_a_title_and_the_requests_it_touched(stranger, scripted):
    client, org = stranger
    e, d = new_request(org, "Can you quote a hotel in Doha?")
    say(org, e, d, "city", "Doha", norm="Doha, QA")
    with workspace(org):
        run_engine(e)
    scripted += [tool_use("whats_missing", request_id=e), answer("It needs the floor area.")]
    t = client.post("/api/chat", json={"message": "What is   missing on the Doha hotel?"}).json()
    listed = client.get("/api/chats").json()
    assert [c["thread_id"] for c in listed] == [t["thread_id"]]
    c = listed[0]
    assert c["title"] == "What is missing on the Doha hotel?" and c["mine"] and c["messages"] == 2
    assert c["preview"] == "It needs the floor area."
    assert c["requests"] == [{"envelope_id": e, "relation": "discussed", "title": c["requests"][0]["title"]}]
    assert [x["thread_id"] for x in client.get(f"/api/chats?envelope_id={e}").json()] == [t["thread_id"]]
    assert client.get("/api/chats?q=floor").json() and not client.get("/api/chats?q=nothing-like-this").json()


def test_conversations_are_private_to_their_owner_except_for_owners_and_admins(stranger, scripted):
    client, org = stranger
    scripted += [answer("Nothing is waiting.")]
    t = client.post("/api/chat", json={"message": "What needs a decision today?"}).json()
    sam, sam_email = colleague(org)
    try:
        assert sam.get("/api/chats").json() == []
        assert sam.get(f"/api/chat/{t['thread_id']}").status_code == 404
        assert sam.post("/api/chat", json={"message": "hi", "thread_id": t["thread_id"]}).status_code == 404
        assert sam.get("/api/chats?scope=everyone").status_code == 403
        everyone = client.get("/api/chats?scope=everyone").json()  # the workspace owner sees everyone's
        assert [c["thread_id"] for c in everyone] == [t["thread_id"]] and everyone[0]["owner"] == "Test Owner"
        assert sam.patch(f"/api/chats/{t['thread_id']}", json={"archived": True}).status_code == 404
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tenancy.app_user WHERE email = :e"), dict(e=sam_email))


def test_a_conversation_can_be_renamed_and_archived_without_losing_it(stranger, scripted):
    client, org = stranger
    scripted += [answer("Nothing is waiting.")]
    t = client.post("/api/chat", json={"message": "What needs a decision today?"}).json()
    assert client.patch(f"/api/chats/{t['thread_id']}", json={"title": "Monday check"}).json()["title"] == "Monday check"
    client.patch(f"/api/chats/{t['thread_id']}", json={"archived": True})
    assert client.get("/api/chats").json() == []
    assert [c["title"] for c in client.get("/api/chats?archived=true").json()] == ["Monday check"]
    assert client.get(f"/api/chat/{t['thread_id']}").status_code == 200  # archived, still readable
    client.patch(f"/api/chats/{t['thread_id']}", json={"archived": False})
    assert len(client.get("/api/chats").json()) == 1


def test_files_added_in_the_chat_make_the_request_it_is_about_and_name_the_chat(stranger, scripted, monkeypatch):
    client, org = stranger
    e, _ = new_request(org, "Hotel in Doha, 120 keys")
    with workspace(org), engine.begin() as conn:
        conn.execute(text("UPDATE intake.envelope SET title = 'Hotel in Doha' WHERE envelope_id = :e"), dict(e=e))
    asked = {}
    def model(system, messages, trace, turn=None, label="model"):
        asked.update(effort=turn.effort, system=system)
        return answer("It is a hotel in Doha; the floor area is missing.")
    monkeypatch.setattr(chat, "call_model", model)
    t = client.post("/api/chat", json={"message": "I've added a file: brief.pdf. What's missing?", "envelope_id": e,
                                       "attached": True, "effort": "high"}).json()
    assert asked["effort"] == "high" and f"request {e}" in asked["system"]
    c = client.get("/api/chats").json()[0]
    assert c["title"] == "Hotel in Doha" and c["requests"][0]["relation"] == "created" and t["envelope_id"] == e
    assert client.post("/api/chat", json={"message": "hi", "effort": "extreme"}).status_code == 422


def test_with_files_the_trace_covers_the_upload_and_the_reading_wait(stranger, scripted):
    client, org = stranger
    e, _ = new_request(org, "Hotel in Doha, 120 keys")
    scripted += [answer("It is a hotel in Doha.")]
    t = client.post("/api/chat", json={"message": "What's in it?", "envelope_id": e, "attached": True,
                                       "upload_ms": 900, "waited_ms": 9400}).json()
    tr = t["messages"][-1]["trace"]
    labels = [s["label"] for s in tr["spans"]]
    assert labels[:2] == ["upload_files", "read_files"] and "load_conversation" in labels
    up, read = tr["spans"][0], tr["spans"][1]
    assert (up["start"], up["end"], read["start"], read["end"]) == (0, 900, 900, 9400)
    after = [s for s in tr["spans"] if s["label"] == "load_conversation"][0]
    assert after["start"] >= 9400  # the answer's own steps come after the wait the person saw
    assert tr["duration"] >= 9400 and tr["duration"] >= max(s["end"] for s in tr["spans"])


def test_a_request_continues_the_conversation_that_discussed_it(stranger, scripted):
    client, org = stranger
    e, d = new_request(org, "Can you quote a hotel in Doha?")
    say(org, e, d, "city", "Doha", norm="Doha, QA")
    with workspace(org):
        run_engine(e)
    scripted += [tool_use("whats_missing", request_id=e), answer("It needs the floor area.")]
    t = client.post("/api/chat", json={"message": "What is missing on the Doha hotel?"}).json()  # asked on Ask
    assert client.get(f"/api/envelopes/{e}/chat").json()["thread_id"] == t["thread_id"]
