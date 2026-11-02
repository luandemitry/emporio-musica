"""Order lookup (behind identity verification) and return/exchange eligibility.

Privacy (manual section 9, LGPD): nothing about an order is revealed before the customer
proves they own it, with the order number plus the e-mail or phone used on the purchase.
Wrong guesses are counted per session and lock the lookup after ``MAX_FAILED_ATTEMPTS``;
"order doesn't exist" and "contact doesn't match" are indistinguishable on purpose, so the
tool can't be used to discover which order numbers exist.

In production the WhatsApp number, authenticated by the channel, would be the second factor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from ..clock import Clock
from ..data import ORDER_STATUS_LABEL_PT, DataStore, Order, OrderStatus, normalize_email, normalize_phone
from ..pricing import money

MAX_FAILED_ATTEMPTS = 3
REGRET_DAYS = 7  # manual 4.1, counted from receipt
PREFERENCE_DAYS = 7  # manual 4.3 ("até 7 dias"; counted from receipt, an assumption)
DEFECT_EXCHANGE_DAYS = 30  # manual 4.2, counted from the purchase date
LEGAL_WARRANTY_DAYS = 90  # manual 8.1, counted from receipt
RETURN_REASONS = ("regret", "defect", "preference")


@dataclass
class VerificationState:
    """Who has proven their identity in this conversation. Persisted with the session."""

    verified_customer_ids: set[int] = field(default_factory=set)
    failed_attempts: int = 0

    @property
    def locked(self) -> bool:
        return self.failed_attempts >= MAX_FAILED_ATTEMPTS

    def to_dict(self) -> dict:
        return {"verified_customer_ids": sorted(self.verified_customer_ids), "failed_attempts": self.failed_attempts}

    @classmethod
    def from_dict(cls, data: dict) -> "VerificationState":
        return cls(set(data.get("verified_customer_ids", [])), int(data.get("failed_attempts", 0)))


def _fmt(day: date) -> str:
    return f"{day:%d/%m/%Y}"


def _authorize(
    store: DataStore, state: VerificationState, order_id: int, email: str | None, phone: str | None
) -> tuple[Order | None, dict | None]:
    """Return ``(order, None)`` if the caller may see it, else ``(None, response_to_return)``."""
    if state.locked:
        return None, {
            "verified": False,
            "locked": True,
            "note": "Too many failed verifications in this conversation. Do not try again; "
            "offer to forward the customer to the team (request_type=contact_request).",
        }
    try:
        order = store.orders.get(int(order_id))
    except (TypeError, ValueError):
        order = None
    if order is not None and order.customer_id in state.verified_customer_ids:
        return order, None
    if not (email or phone):
        return None, {
            "verified": False,
            "needs": "the e-mail or phone number used on the purchase",
            "note": "Ask the customer for it. Reveal nothing about the order yet.",
        }

    customer = store.customers.get(order.customer_id) if order else None
    matches = customer is not None and (
        (email and normalize_email(email) == normalize_email(customer.email))
        or (phone and normalize_phone(phone) == normalize_phone(customer.phone))
    )
    if matches:
        state.verified_customer_ids.add(customer.id)
        return order, None

    state.failed_attempts += 1
    left = max(0, MAX_FAILED_ATTEMPTS - state.failed_attempts)
    return None, {
        "verified": False,
        "error": "Could not verify: the order number and the contact don't match our records.",
        "attempts_left": left,
        "note": "Say only that you couldn't confirm the data; never say which part was wrong. "
        + ("Ask them to check and try again." if left else "Stop here and offer to forward to the team."),
    }


def _order_view(store: DataStore, order: Order, clock: Clock) -> dict:
    customer = store.customers[order.customer_id]
    items = [
        {
            "product": store.products[i.product_id].name if i.product_id in store.products else f"produto {i.product_id}",
            "quantity": i.quantity,
        }
        for i in order.items
    ]
    view = {
        "verified": True,
        "order_id": order.id,
        "customer_first_name": customer.name.split()[0],
        "order_date": _fmt(order.order_date),
        "status": order.status.value,
        "status_pt": ORDER_STATUS_LABEL_PT[order.status],
        "items": items,
        "total": money(order.total),
        "payment": order.payment.label_pt,
    }
    if order.tracking_code:
        view["tracking_code"] = order.tracking_code
    if order.estimated_delivery:
        view["estimated_delivery"] = _fmt(order.estimated_delivery)
    if order.status is OrderStatus.CANCELLED and order.notes:
        view["cancellation_reason"] = order.notes

    today = clock.today()
    if order.status is OrderStatus.SHIPPED and order.estimated_delivery and order.estimated_delivery < today:
        view["delivery_estimate_passed"] = True
        view["days_past_estimate"] = (today - order.estimated_delivery).days
        view["note"] = (
            "The estimated delivery date has passed. Acknowledge it and offer to forward the case "
            "to the team (request_type=order_issue); do not promise a new date."
        )
    elif order.status is OrderStatus.CONFIRMED:
        view["note"] = "Being prepared. The tracking code is sent by e-mail and WhatsApp when it is dispatched."
    elif order.status is OrderStatus.PENDING:
        view["note"] = "Awaiting payment confirmation; no tracking code yet."
    elif order.status is OrderStatus.DELIVERED:
        view["note"] = "The system only has the ESTIMATED delivery date, not the actual one."
    return view


def get_order_status(
    store: DataStore,
    state: VerificationState,
    clock: Clock,
    order_id: int,
    email: str | None = None,
    phone: str | None = None,
) -> dict:
    """Status, items, payment and tracking of an order, after verifying the customer."""
    order, error = _authorize(store, state, order_id, email, phone)
    return error if error else _order_view(store, order, clock)


def check_return_eligibility(
    store: DataStore,
    state: VerificationState,
    clock: Clock,
    order_id: int,
    reason: str,
    email: str | None = None,
    phone: str | None = None,
) -> dict:
    """Can this order be returned/exchanged? ``reason``: regret, defect or preference."""
    if reason not in RETURN_REASONS:
        return {"error": f"reason must be one of {', '.join(RETURN_REASONS)}."}
    order, error = _authorize(store, state, order_id, email, phone)
    if error:
        return error

    base = {"verified": True, "order_id": order.id, "reason": reason, "order_status": order.status.value}
    if order.status is OrderStatus.CANCELLED:
        return {**base, "eligible": False, "code": "order_cancelled", "note": "The order was cancelled; there is nothing to return."}
    if order.status is not OrderStatus.DELIVERED:
        return {
            **base,
            "eligible": False,
            "code": "not_delivered_yet",
            "note": "The order hasn't been delivered, so no return window has started. Do not invent a "
            "cancellation procedure; offer to forward to the team (request_type=order_issue).",
        }

    today = clock.today()
    receipt = order.estimated_delivery or order.order_date
    result = {
        **base,
        "today": _fmt(today),
        "receipt_date": _fmt(receipt),
        "receipt_date_note": "Estimated delivery date used as the receipt date: the system has no actual delivery date. "
        "All orders are treated as online purchases.",
    }

    if reason in ("regret", "preference"):
        deadline = receipt + timedelta(days=REGRET_DAYS if reason == "regret" else PREFERENCE_DAYS)
        eligible = today <= deadline
        result.update(
            policy_section="4.1" if reason == "regret" else "4.3",
            eligible=eligible,
            deadline=_fmt(deadline),
            **({"days_left": (deadline - today).days} if eligible else {"days_past_deadline": (today - deadline).days}),
        )
        if eligible:
            result["conditions_to_confirm_with_customer"] = [
                "Product in its original packaging, with no signs of use, with all accessories and manuals.",
                "Not a customised or special-order item, not a 'venda final' liquidation purchase, "
                "and not a wind-instrument mouthpiece (manual 4.4).",
            ]
            if reason == "regret":
                result["refund"] = "Same payment method as the purchase, within 10 business days. Return shipping is paid by the store."
            else:
                result["refund"] = "Depends on the desired product being available; any price difference is charged or refunded."
    else:  # defect
        exchange_deadline = order.order_date + timedelta(days=DEFECT_EXCHANGE_DAYS)
        warranty_until = receipt + timedelta(days=LEGAL_WARRANTY_DAYS)
        eligible = today <= exchange_deadline
        result.update(
            policy_section="4.2",
            eligible=eligible,
            deadline=_fmt(exchange_deadline),
            deadline_note="30 calendar days from the PURCHASE date.",
            conditions_to_confirm_with_customer=[
                "Manufacturing defect, not misuse, drops, excessive humidity or unauthorised modifications."
            ],
        )
        if not eligible:
            result.update(
                days_past_deadline=(today - exchange_deadline).days,
                legal_warranty_until=_fmt(warranty_until),
                legal_warranty_active=today <= warranty_until,
                alternative=(
                    "After 30 days the customer must use the warranty (legal: 90 days from receipt; "
                    "manufacturer: 6 months to 2 years, see the certificate). The store can mediate on request."
                ),
            )

    result["next_step"] = (
        "If eligible and the customer wants to proceed, confirm the conditions with them and register "
        "the request with the team (request_type=return_request or exchange_request). You cannot "
        "process it yourself, so do not say it has been done."
    )
    return result
