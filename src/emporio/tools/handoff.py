"""Hand a case over to the human team (manual 7.3: register the complaint, reply within
24 business hours). The "ticket" is a line appended to a local JSONL file, which stands in
for whatever helpdesk the store would use in production."""

from __future__ import annotations

import json
from pathlib import Path

from ..clock import Clock

REQUEST_TYPES = ("complaint", "return_request", "exchange_request", "order_issue", "contact_request", "other")
DEFAULT_LOG = Path(".handoffs") / "handoffs.jsonl"


class HandoffLog:
    def __init__(self, path: Path | str = DEFAULT_LOG, clock: Clock | None = None):
        self.path = Path(path)
        self.clock = clock or Clock()

    def register(
        self,
        request_type: str,
        summary: str,
        customer_name: str | None = None,
        contact: str | None = None,
        order_id: int | None = None,
        session_id: str = "cli",
    ) -> dict:
        if request_type not in REQUEST_TYPES:
            return {"registered": False, "error": f"request_type must be one of {', '.join(REQUEST_TYPES)}."}
        if not summary.strip():
            return {"registered": False, "error": "summary is required: what does the customer need?"}

        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.path.read_text(encoding="utf-8").count("\n") if self.path.exists() else 0
        now = self.clock.now()
        ticket = f"EM-{now:%Y%m%d}-{existing + 1:03d}"
        record = {
            "ticket_id": ticket,
            "created_at": now.isoformat(timespec="seconds"),
            "request_type": request_type,
            "summary": summary.strip(),
            "customer_name": customer_name,
            "contact": contact,
            "order_id": order_id,
            "session_id": session_id,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        return {
            "registered": True,
            "ticket_id": ticket,
            "expected_response": "up to 24 business hours (horas úteis)",
            "note": "Tell the customer it was registered, give the ticket id and the response time.",
        }
