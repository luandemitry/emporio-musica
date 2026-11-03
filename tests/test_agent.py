import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from google.genai import errors, types

from emporio.agent import Agent
from emporio.clock import Clock
from emporio.data import load_store
from emporio.llm import GeminiLLM, LLMUnavailable, ModelTurn, ToolCall
from emporio.policies import PolicyIndex
from emporio.registry import ToolContext, ToolRegistry
from emporio.session import Session, SessionStore
from emporio.tools.handoff import HandoffLog

CLOCK = Clock(datetime(2026, 2, 20, 10, 0))


@pytest.fixture(scope="module")
def shared():
    return load_store(), PolicyIndex.load()


@pytest.fixture
def registry_and_session(shared, tmp_path):
    session = Session("t1")
    ctx = ToolContext(shared[0], shared[1], CLOCK, session.verification, HandoffLog(tmp_path / "h.jsonl", CLOCK), "t1")
    return ToolRegistry(ctx), session


class ScriptedLLM:
    """Plays back prepared turns and records what it was sent."""

    def __init__(self, *turns):
        self.turns, self.seen = list(turns), []

    def generate(self, system, history, declarations):
        self.seen.append({"system": system, "history": json.loads(json.dumps(history))})
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


def asks(tool, call_id="c1", **args):
    content = {"role": "model", "parts": [{"function_call": {"id": call_id, "name": tool, "args": args}}]}
    return ModelTurn(content, "", [ToolCall(call_id, tool, args)])


def says(text):
    return ModelTurn({"role": "model", "parts": [{"text": text}]}, text, [])


def make_agent(llm, registry, session, tmp_path):
    return Agent(llm, registry, session, SessionStore(tmp_path / "sessions"), CLOCK)


def test_tool_loop_feeds_results_back_and_records_everything(registry_and_session, tmp_path):
    registry, session = registry_and_session
    llm = ScriptedLLM(asks("get_product", name="Takamine GD20"), says("O GD20 sai por R$ 2.199,00."))
    agent = make_agent(llm, registry, session, tmp_path)

    reply = agent.respond("Quanto custa o Takamine GD20?")

    assert reply.text == "O GD20 sai por R$ 2.199,00."
    assert [e.name for e in reply.tool_events] == ["get_product"]
    assert [m["role"] for m in session.history] == ["user", "model", "user", "model"]
    # the 2nd model call saw the real tool result, matched to the call by id
    response = llm.seen[1]["history"][-1]["parts"][0]["function_response"]
    assert response["id"] == "c1" and response["response"]["result"]["product"]["price_display"] == "R$ 2.199,00"
    assert "20/02/2026" in llm.seen[0]["system"]  # the model is told today's date
    saved = SessionStore(tmp_path / "sessions").load_or_create("t1")
    assert saved.transcript[0]["tools"][0]["name"] == "get_product" and saved.history == session.history


def test_failed_turn_is_rolled_back_but_failed_verifications_are_kept(registry_and_session, tmp_path):
    registry, session = registry_and_session
    llm = ScriptedLLM(
        asks("get_order_status", order_id=8, email="wrong@email.com"),  # a failed verification...
        LLMUnavailable("503"),  # ...then the API goes down mid-turn
    )
    agent = make_agent(llm, registry, session, tmp_path)

    with pytest.raises(LLMUnavailable):
        agent.respond("Cadê meu pedido 8? Meu e-mail é wrong@email.com")

    assert session.history == []  # no dangling function call left behind
    assert session.verification.failed_attempts == 1  # and the counter was not reset
    assert SessionStore(tmp_path / "sessions").load_or_create("t1").verification.failed_attempts == 1


def test_every_declared_parameter_is_accepted_by_its_tool(registry_and_session):
    registry, _ = registry_and_session
    full_calls = {
        "search_products": dict(query="violão", category="Violões", min_price=0, max_price=1000, include_unavailable=False, sort="price_asc", limit=3),
        "get_product": dict(product_id=95, name="Takamine GD20"),
        "list_categories": {},
        "consult_policies": dict(query="devolução", max_results=2),
        "simulate_payment": dict(product_id=95, quantity=1, amount=100, promotional=False, method="pix"),
        "get_store_status": {},
        "get_order_status": dict(order_id=8.0, email="anacarol.ferreira@coldmail.com", phone="67999999999"),  # 8.0: LLMs send floats
        "check_return_eligibility": dict(order_id=7, reason="regret", email="leticia.rocha@jmail.com", phone="x"),
        "request_human_followup": dict(request_type="complaint", summary="teste", customer_name="Ana", contact="x", order_id=8),
    }
    assert set(full_calls) == set(registry.tools)  # a new tool must be added here too
    for name, args in full_calls.items():
        assert set(args) == set(registry.tools[name].properties), name  # declaration and test agree
        assert "Internal error" not in str(registry.call(name, args)), name  # i.e. no TypeError

    assert "Unknown tool" in registry.call("drop_database", {})["error"]
    assert "Missing" in registry.call("consult_policies", {})["error"]


# --- Gemini adapter, against the real SDK types (no network) -------------------------------


def gemini_response(parts):
    return types.GenerateContentResponse.model_validate({"candidates": [{"content": {"role": "model", "parts": parts}}]})


class StubClient:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_gemini_adapter_parses_calls_keeps_signatures_and_retries_rate_limits(registry_and_session):
    registry, _ = registry_and_session
    rate_limited = errors.APIError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    answer = gemini_response(
        [{"function_call": {"id": "abc", "name": "get_product", "args": {"name": "GD20"}}, "thought_signature": "c2ln"}]
    )
    client, waits = StubClient(rate_limited, answer), []
    llm = GeminiLLM("gemini-test", client=client, sleep=waits.append)

    history = [{"role": "user", "parts": [{"text": "preço do GD20?"}]}]
    turn = llm.generate("SYSTEM", history, registry.declarations())

    assert waits == [4] and len(client.calls) == 2  # one retry after the 429
    assert turn.tool_calls == [ToolCall("abc", "get_product", {"name": "GD20"})]
    assert turn.content["parts"][0]["thought_signature"] == "c2ln"  # sent back untouched next time
    sent = client.calls[-1]
    assert isinstance(sent["contents"][0], types.Content) and sent["config"].system_instruction == "SYSTEM"
    assert len(sent["config"].tools[0].function_declarations) == len(registry.tools)
    assert sent["config"].automatic_function_calling.disable is True
    types.Content.model_validate(turn.content)  # and it can be replayed in the next request
