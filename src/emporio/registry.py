"""Tool registry: what the LLM is told about each tool, and how a call is executed.

The declarations (JSON schema + guidance on *when* to use each tool) are what makes the
model "know when to consult data and when to consult policies". Execution is defensive:
a bad call from the model becomes an ``{"error": ...}`` result it can recover from, never
an exception that would break the conversation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from .clock import Clock
from .data import DataStore
from .policies import PolicyIndex, consult_policies
from .tools.handoff import REQUEST_TYPES, HandoffLog
from .tools.orders import RETURN_REASONS, VerificationState, check_return_eligibility, get_order_status
from .tools.payments import METHODS, simulate_payment
from .tools.products import get_product, list_categories, search_products
from .tools.store_info import store_status

log = logging.getLogger(__name__)


@dataclass
class ToolContext:
    """Everything the tools need; one per conversation."""

    store: DataStore
    policies: PolicyIndex
    clock: Clock
    verification: VerificationState
    handoffs: HandoffLog
    session_id: str = "cli"


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict[str, dict]
    required: tuple[str, ...]
    run: Callable[..., dict]

    def declaration(self) -> dict:
        decl: dict[str, Any] = {"name": self.name, "description": self.description}
        if self.properties:
            decl["parameters"] = {
                "type": "object",
                "properties": self.properties,
                **({"required": list(self.required)} if self.required else {}),
            }
        return decl


def _p(type_: str, description: str, **extra: Any) -> dict:
    return {"type": type_, "description": description, **extra}


def build_tools(ctx: ToolContext) -> list[Tool]:
    s = ctx.store
    contact = _p("string", "E-mail or phone used on the purchase, as the customer typed it.")
    return [
        Tool(
            "search_products",
            "Search the store catalogue. Use for ANY question about what the store sells, models, "
            "prices, price ranges or availability ('violões até R$ 1.000', 'ukulele para criança'). "
            "Every word in `query` must match. Categories: Guitarras, Baixos, Baterias e Percussão, "
            "Teclados e Pianos, Violões, Ukuleles. Price filters use the price the customer pays "
            "(promotion applied). Returns only sellable products unless include_unavailable is true. "
            "Zero results means the store does not have it: say so.",
            {
                "query": _p("string", "Short keywords, e.g. 'violão nylon', 'ukulele tenor', 'yamaha'."),
                "category": _p("string", "Category name, e.g. 'Violões'."),
                "min_price": _p("number", "Minimum price in BRL."),
                "max_price": _p("number", "Maximum price in BRL."),
                "include_unavailable": _p("boolean", "Also list out-of-stock, discontinued and coming-soon items."),
                "sort": _p("string", "Ordering by price.", enum=["price_asc", "price_desc"]),
                "limit": _p("integer", "Max results (default 8)."),
            },
            (),
            lambda **a: search_products(s, **a),
        ),
        Tool(
            "get_product",
            "Details of ONE product by name or id: price, promotion, stock, specs, and alternatives if "
            "it is unavailable. Use when the customer names a model ('Takamine GD20') or asks about its "
            "price/availability. Also reports promotions that already ended, so you can answer "
            "'wasn't it on sale?' honestly. Provide product_id or name.",
            {
                "product_id": _p("integer", "Product id from a previous result."),
                "name": _p("string", "Product name as the customer wrote it."),
            },
            (),
            lambda **a: get_product(s, **a),
        ),
        Tool(
            "list_categories",
            "Overview of what the store sells: categories, how many products are available and the "
            "price range. Use for broad questions like 'o que vocês vendem?'.",
            {},
            (),
            lambda **a: list_categories(s),
        ),
        Tool(
            "consult_policies",
            "Look up the store's policy manual: opening hours text, address, phone, payment methods, "
            "installment rules, returns and exchanges, shipping and delivery, tracking, promotions "
            "rules, warranty, privacy, how to treat accessories. Use for any 'how does it work' or "
            "rules question. Answer ONLY from what it returns. Never use it for prices or stock.",
            {
                "query": _p("string", "The question in plain Portuguese, e.g. 'prazo para devolver compra online'."),
                "max_results": _p("integer", "How many sections to return (default 3)."),
            },
            ("query",),
            lambda **a: consult_policies(ctx.policies, **a),
        ),
        Tool(
            "simulate_payment",
            "Payment options for a product or an amount: PIX (with its discount when it applies), "
            "debit, boleto and credit-card installments, following the store's rules. ALWAYS use it "
            "instead of calculating installments or PIX discounts yourself. Provide product_id "
            "(preferred) or amount, not both.",
            {
                "product_id": _p("integer", "Product id."),
                "quantity": _p("integer", "Units (default 1)."),
                "amount": _p("number", "Plain amount in BRL, when there is no product."),
                "promotional": _p("boolean", "True if the given amount is already a promotional price."),
                "method": _p("string", "Return only this method.", enum=list(METHODS)),
            },
            (),
            lambda **a: simulate_payment(s, **a),
        ),
        Tool(
            "get_store_status",
            "Is the store open right now? Today's hours and the next opening, considering Sundays "
            "and holidays. Use for 'está aberto?', 'até que horas?', 'abre sábado?'.",
            {},
            (),
            lambda **a: store_status(ctx.clock),
        ),
        Tool(
            "get_order_status",
            "Status, items, payment and tracking of an order. Needs order_id AND the e-mail or phone "
            "used on the purchase; without them nothing is revealed, so ask the customer first. "
            "Once a customer is verified in this conversation, their other orders need only the number.",
            {
                "order_id": _p("integer", "Order number."),
                "email": contact,
                "phone": contact,
            },
            ("order_id",),
            lambda **a: get_order_status(s, ctx.verification, ctx.clock, **a),
        ),
        Tool(
            "check_return_eligibility",
            "Check whether an order can be returned/exchanged under the store's rules, applying the "
            "deadlines to the order's dates. Same identity requirements as get_order_status. Reasons: "
            "regret (changed mind), defect, preference (swap for another model/color). It does NOT "
            "start the return: afterwards, if the customer wants to proceed, use request_human_followup.",
            {
                "order_id": _p("integer", "Order number."),
                "reason": _p("string", "Why the customer wants to return it.", enum=list(RETURN_REASONS)),
                "email": contact,
                "phone": contact,
            },
            ("order_id", "reason"),
            lambda **a: check_return_eligibility(s, ctx.verification, ctx.clock, **a),
        ),
        Tool(
            "request_human_followup",
            "Hand the case to the human team and get a ticket. Use to start a return or exchange the "
            "customer confirmed, to register a complaint, for order problems you can't solve, or when "
            "you don't have the information. Tell the customer the ticket id and that the team replies "
            "within 24 business hours. Include a self-contained summary.",
            {
                "request_type": _p("string", "Kind of request.", enum=list(REQUEST_TYPES)),
                "summary": _p("string", "What the customer needs, with the key facts (order, reason, dates)."),
                "customer_name": _p("string", "Customer name, if known."),
                "contact": _p("string", "How the team can reach the customer, if the customer gave it."),
                "order_id": _p("integer", "Related order number, if any."),
            },
            ("request_type", "summary"),
            lambda **a: ctx.handoffs.register(session_id=ctx.session_id, **a),
        ),
    ]


def _coerce(value: Any, type_: str) -> Any:
    """LLMs send 8.0 or "8" for integers; accept both."""
    if type_ == "integer":
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value)
    return value


class ToolRegistry:
    def __init__(self, ctx: ToolContext):
        self.tools = {t.name: t for t in build_tools(ctx)}

    def declarations(self) -> list[dict]:
        return [t.declaration() for t in self.tools.values()]

    def call(self, name: str, args: dict | None) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            return {"error": f"Unknown tool {name!r}. Available: {', '.join(self.tools)}."}

        clean = {
            k: _coerce(v, tool.properties[k]["type"])
            for k, v in (args or {}).items()
            if k in tool.properties and v is not None
        }
        missing = [r for r in tool.required if r not in clean]
        if missing:
            return {"error": f"Missing required argument(s): {', '.join(missing)}."}
        try:
            return tool.run(**clean)
        except Exception:  # a tool bug must not end the conversation
            log.exception("tool %s failed with %r", name, clean)
            return {"error": "Internal error while running this tool. Apologise and offer to forward to the team."}
