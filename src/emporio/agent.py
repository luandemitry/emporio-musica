"""The agent: a bounded tool-calling loop on top of an LLM, with persisted history.

One user message -> the model either answers or asks for tools -> tools run -> results go back
to the model -> repeat until it answers (at most ``MAX_TOOL_ROUNDS`` rounds). If the LLM fails
midway, the turn is rolled back so the saved history never contains a dangling tool call.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .clock import Clock
from .config import Settings
from .data import load_store
from .llm import LLM, GeminiLLM, LLMUnavailable
from .persona import build_system_prompt
from .policies import PolicyIndex
from .registry import ToolContext, ToolRegistry
from .session import Session, SessionStore
from .tools.handoff import HandoffLog

MAX_TOOL_ROUNDS = 6
EMPTY_REPLY = "Desculpe, não consegui formular uma resposta agora. Pode repetir sua pergunta de outro jeito?"
TOO_MANY_ROUNDS = (
    "Desculpe, me enrolei por aqui. Pode me dizer de novo o que você precisa? "
    "Se preferir, encaminho para a nossa equipe."
)


@dataclass
class ToolEvent:
    name: str
    args: dict
    result: dict


@dataclass
class AgentReply:
    text: str
    tool_events: list[ToolEvent] = field(default_factory=list)


class Agent:
    def __init__(self, llm: LLM, registry: ToolRegistry, session: Session, store: SessionStore, clock: Clock):
        self.llm, self.registry, self.session, self.store, self.clock = llm, registry, session, store, clock

    def respond(self, user_text: str) -> AgentReply:
        history = self.session.history
        checkpoint = len(history)
        history.append({"role": "user", "parts": [{"text": user_text}]})
        events: list[ToolEvent] = []

        try:
            reply = self._run_tool_loop(history, events)
        except LLMUnavailable:
            del history[checkpoint:]  # drop the partial turn: no dangling function calls
            # Identity-check counters are deliberately NOT rolled back: provoking API failures
            # must not be a way to reset failed attempts.
            self.store.save(self.session)
            raise

        self.session.transcript.append(
            {
                "at": self.clock.now().isoformat(timespec="seconds"),
                "user": user_text,
                "tools": [{"name": e.name, "args": e.args, "result": e.result} for e in events],
                "assistant": reply,
            }
        )
        self.store.save(self.session)
        return AgentReply(reply, events)

    def _run_tool_loop(self, history: list[dict], events: list[ToolEvent]) -> str:
        for _ in range(MAX_TOOL_ROUNDS):
            turn = self.llm.generate(build_system_prompt(self.clock), history, self.registry.declarations())

            if not turn.tool_calls:
                if not turn.text:
                    history.append({"role": "model", "parts": [{"text": EMPTY_REPLY}]})
                    return EMPTY_REPLY
                history.append(turn.content)
                return turn.text

            history.append(turn.content)
            responses = []
            for call in turn.tool_calls:
                result = self.registry.call(call.name, call.args)
                events.append(ToolEvent(call.name, call.args, result))
                part = {"name": call.name, "response": {"result": result}}
                if call.id:
                    part["id"] = call.id
                responses.append({"function_response": part})
            history.append({"role": "user", "parts": responses})

        history.append({"role": "model", "parts": [{"text": TOO_MANY_ROUNDS}]})
        return TOO_MANY_ROUNDS


def create_agent(settings: Settings, session_id: str, llm: LLM | None = None) -> Agent:
    """Wire everything together for one conversation."""
    store = SessionStore(settings.sessions_dir)
    session = store.load_or_create(session_id)
    ctx = ToolContext(
        store=load_store(settings.data_dir),
        policies=PolicyIndex.load(settings.data_dir / "politicas_da_loja.md"),
        clock=settings.clock,
        verification=session.verification,
        handoffs=HandoffLog(settings.handoff_log, settings.clock),
        session_id=session_id,
    )
    llm = llm or GeminiLLM(settings.model, api_key=settings.api_key)
    agent = Agent(llm, ToolRegistry(ctx), session, store, settings.clock)
    return agent
