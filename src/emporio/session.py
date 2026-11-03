"""Conversation persistence: one JSON file per session, so a chat can be resumed.

Stored: the model-facing history, the identity-verification state (so a customer who was
verified doesn't have to repeat themselves, and failed attempts can't be reset by restarting)
and a human-readable transcript with the tools that were called, used for the examples.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .tools.orders import VerificationState

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass
class Session:
    id: str
    history: list[dict] = field(default_factory=list)
    verification: VerificationState = field(default_factory=VerificationState)
    transcript: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "history": self.history,
            "verification": self.verification.to_dict(),
            "transcript": self.transcript,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        return cls(
            id=data["id"],
            history=data.get("history", []),
            verification=VerificationState.from_dict(data.get("verification", {})),
            transcript=data.get("transcript", []),
        )


class SessionStore:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)

    def _path(self, session_id: str) -> Path:
        if not _SAFE_ID.match(session_id):  # the id becomes a file name
            raise ValueError("Session ids may only contain letters, digits, '-' and '_' (max 64).")
        return self.directory / f"{session_id}.json"

    def exists(self, session_id: str) -> bool:
        return self._path(session_id).exists()

    def load_or_create(self, session_id: str) -> Session:
        path = self._path(session_id)
        if path.exists():
            return Session.from_dict(json.loads(path.read_text(encoding="utf-8")))
        return Session(id=session_id)

    def save(self, session: Session) -> None:
        path = self._path(session.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a crash never leaves a half-written session
