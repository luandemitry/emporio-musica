"""Command-line chat with the agent.

    python -m emporio                       # new conversation
    python -m emporio --session maria       # resume (or start) the conversation "maria"
    python -m emporio --verbose             # also show which tools the agent called
    python -m emporio --now 2026-02-20T10:00  # freeze the clock (see README)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime

from .agent import Agent, ToolEvent, create_agent
from .clock import Clock
from .config import Settings
from .llm import LLM, LLMUnavailable
from .persona import PERSONA_NAME

EXIT_WORDS = {"sair", "exit", "quit", "/sair"}
OUTAGE = "Desculpe, estou com uma instabilidade aqui. Tenta de novo em instantes?"


def format_event(event: ToolEvent, width: int = 150) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in event.args.items())
    result = json.dumps(event.result, ensure_ascii=False)
    if len(result) > width:
        result = result[: width - 1] + "…"
    return f"  [tool] {event.name}({args}) -> {result}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="emporio", description=f"Chat with {PERSONA_NAME}, the Empório da Música support agent.")
    parser.add_argument("--session", help="conversation id; an existing one is resumed (default: a new conversation)")
    parser.add_argument("--verbose", "-v", action="store_true", help="show the tools the agent called")
    parser.add_argument("--now", help="freeze the clock (store local time), e.g. 2026-02-20T10:00")
    parser.add_argument("--model", help="Gemini model (default: GEMINI_MODEL or the project default)")
    return parser


def main(argv: list[str] | None = None, llm: LLM | None = None) -> int:
    for stream in (sys.stdout, sys.stdin):  # Windows consoles/pipes are not always UTF-8
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    if args.now:
        settings = dataclasses.replace(settings, clock=Clock(datetime.fromisoformat(args.now)))
    if args.model:
        settings = dataclasses.replace(settings, model=args.model)
    session_id = args.session or f"chat-{datetime.now():%Y%m%d-%H%M%S}"

    try:
        agent: Agent = create_agent(settings, session_id, llm=llm)
    except (LLMUnavailable, ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    previous = len(agent.session.transcript)
    print(f"{PERSONA_NAME} · Empório da Música customer support  (type 'quit' to exit)")
    print(f"conversation: {session_id}" + (f" · resuming ({previous} earlier exchanges)" if previous else ""))
    if settings.clock.is_frozen:
        print(f"clock frozen at {settings.clock.now():%Y-%m-%d %H:%M}")
    print()

    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text.lower() in EXIT_WORDS:
            return 0
        try:
            reply = agent.respond(text)
        except LLMUnavailable as exc:
            print(f"{PERSONA_NAME}: {OUTAGE}\n")
            if args.verbose:
                print(f"  [error] {exc}\n")
            continue
        if args.verbose:
            for event in reply.tool_events:
                print(format_event(event))
        print(f"{PERSONA_NAME}: {reply.text}\n")


if __name__ == "__main__":
    raise SystemExit(main())
