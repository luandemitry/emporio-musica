"""LLM access. The agent talks to the small ``LLM`` interface; ``GeminiLLM`` implements it.

History is kept as plain JSON dicts in the Gemini ``Content`` shape. The model's own turns are
stored exactly as returned (Gemini requires its thought signatures to be sent back with
function calls), and the tool results are added as ``function_response`` parts.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from google import genai
from google.genai import errors, types

log = logging.getLogger(__name__)

RETRYABLE_CODES = {429, 500, 503, 504}  # rate limit (free tier) and transient server errors
RETRY_DELAYS = (4, 10, 20)  # seconds


class LLMUnavailable(Exception):
    """The model could not produce a usable answer (after retries)."""


@dataclass(frozen=True)
class ToolCall:
    id: str | None
    name: str
    args: dict


@dataclass
class ModelTurn:
    content: dict  # the model's turn, ready to be appended to the history as-is
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLM(Protocol):
    def generate(self, system: str, history: list[dict], declarations: list[dict]) -> ModelTurn: ...


class GeminiLLM:
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if client is None and not api_key:
            raise LLMUnavailable(
                "No API key. Set GEMINI_API_KEY (see .env.example); a free key comes from Google AI Studio."
            )
        self.model = model
        self.client = client or genai.Client(api_key=api_key)
        self._sleep = sleep

    def generate(self, system: str, history: list[dict], declarations: list[dict]) -> ModelTurn:
        contents = [types.Content.model_validate(m) for m in history]
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[types.FunctionDeclaration(**d) for d in declarations])],
            # we run the tool loop ourselves, so each call can be traced and bounded
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        return self._to_turn(self._call_with_retry(contents, config))

    def _call_with_retry(self, contents: list, config: types.GenerateContentConfig):
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                return self.client.models.generate_content(model=self.model, contents=contents, config=config)
            except errors.APIError as exc:
                if exc.code in RETRYABLE_CODES and attempt < len(RETRY_DELAYS):
                    delay = RETRY_DELAYS[attempt]
                    log.warning("Gemini returned %s; retrying in %ss", exc.code, delay)
                    self._sleep(delay)
                    continue
                raise LLMUnavailable(f"Gemini API error {exc.code}: {exc.message}") from exc
        raise AssertionError("unreachable")

    @staticmethod
    def _to_turn(response: Any) -> ModelTurn:
        candidates = response.candidates or []
        content = candidates[0].content if candidates else None
        if content is None or not content.parts:
            reason = candidates[0].finish_reason if candidates else "no candidates"
            raise LLMUnavailable(f"Empty response from the model (finish reason: {reason}).")

        calls = [
            ToolCall(id=c.id, name=c.name, args=dict(c.args or {}))
            for c in (response.function_calls or [])
        ]
        text = "".join(p.text for p in content.parts if p.text and not p.thought).strip()
        return ModelTurn(content.model_dump(mode="json", exclude_none=True), text, calls)
