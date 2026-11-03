"""Runtime settings, read from environment variables (and a local ``.env`` file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .clock import Clock
from .data import default_data_dir

DEFAULT_MODEL = "gemini-3.5-flash-lite"  # stable and light: the free tier's usage limits are what constrains this project


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    model: str
    data_dir: Path
    sessions_dir: Path
    handoff_log: Path
    clock: Clock

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()  # a .env in the working directory; real environment variables win
        return cls(
            api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"),
            model=os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL,
            data_dir=default_data_dir(),
            sessions_dir=Path(os.environ.get("EMPORIO_SESSIONS_DIR", ".sessions")),
            handoff_log=Path(os.environ.get("EMPORIO_HANDOFF_LOG", ".handoffs/handoffs.jsonl")),
            clock=Clock.from_env(),
        )
