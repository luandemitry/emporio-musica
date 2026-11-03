"""Small web front end for the agent (Flask), a thin layer over ``Agent.respond``.

    python main.py web                    # http://127.0.0.1:5000
    python main.py web --now 2026-02-20T10:00 --port 8000

The browser keeps a conversation id in localStorage; the server keeps one agent per id (and the
usual JSON session file), so a page reload resumes the conversation. Requests are serialised
with a lock: this is a demo server for one or a few people, not a production deployment.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from ..agent import Agent, create_agent
from ..clock import Clock
from ..cli import OUTAGE
from ..config import Settings
from ..llm import LLM, LLMUnavailable
from ..persona import PERSONA_NAME

STATIC_DIR = Path(__file__).parent / "static"
MAX_MESSAGE_CHARS = 2000
MAX_CACHED_AGENTS = 50


def _tool_dicts(events) -> list[dict]:
    return [{"name": e.name, "args": e.args, "result": e.result} for e in events]


def create_app(settings: Settings, llm: LLM | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    agents: OrderedDict[str, Agent] = OrderedDict()
    lock = threading.Lock()

    def agent_for(session_id: str) -> Agent:
        if session_id in agents:
            agents.move_to_end(session_id)
            return agents[session_id]
        agent = create_agent(settings, session_id, llm=llm)  # raises ValueError on an unsafe id
        agents[session_id] = agent
        while len(agents) > MAX_CACHED_AGENTS:  # evicted conversations are still saved on disk
            agents.popitem(last=False)
        return agent

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/api/info")
    def info():
        return jsonify(
            persona=PERSONA_NAME,
            clock=f"{settings.clock.now():%Y-%m-%d %H:%M}",
            clock_frozen=settings.clock.is_frozen,
            model=settings.model,
        )

    @app.get("/api/sessions/<session_id>")
    def history(session_id: str):
        try:
            with lock:
                transcript = list(agent_for(session_id).session.transcript)
        except ValueError:
            return jsonify(error="invalid session id"), 400
        return jsonify(transcript=transcript)

    @app.post("/api/chat")
    def chat():
        payload = request.get_json(silent=True) or {}
        session_id, message = payload.get("session_id"), payload.get("message")
        if not isinstance(session_id, str) or not isinstance(message, str) or not message.strip():
            return jsonify(error="session_id and a non-empty message are required"), 400
        if len(message) > MAX_MESSAGE_CHARS:
            return jsonify(error=f"message too long (max {MAX_MESSAGE_CHARS} characters)"), 400
        try:
            with lock:
                reply = agent_for(session_id).respond(message.strip())
        except ValueError:
            return jsonify(error="invalid session id"), 400
        except LLMUnavailable:
            return jsonify(error=OUTAGE), 503
        return jsonify(text=reply.text, tools=_tool_dicts(reply.tool_events))

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="main.py web", description="Web chat for the Empório da Música agent.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--now", help="freeze the clock (store local time), e.g. 2026-02-20T10:00")
    parser.add_argument("--model", help="Gemini model (default: GEMINI_MODEL or the project default)")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if not settings.api_key:
        print("Error: set GEMINI_API_KEY (or put it in .env) before starting the server.", file=sys.stderr)
        return 2
    if args.now:
        settings = dataclasses.replace(settings, clock=Clock(datetime.fromisoformat(args.now)))
    if args.model:
        settings = dataclasses.replace(settings, model=args.model)

    create_app(settings).run(host=args.host, port=args.port, threaded=True)
    return 0
