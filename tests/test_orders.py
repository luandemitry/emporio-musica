from datetime import datetime

import pytest

from emporio.clock import Clock
from emporio.data import load_store
from emporio.tools.orders import (
    MAX_FAILED_ATTEMPTS,
    VerificationState,
    check_return_eligibility,
    get_order_status,
)

ANA = {"email": "anacarol.ferreira@coldmail.com"}  # customer 2, owner of order 8 (shipped)
LETICIA = {"email": "leticia.rocha@jmail.com"}  # customer 14, owner of order 7 (delivered)


@pytest.fixture(scope="module")
def store():
    return load_store()


def clock(year, month, day):
    return Clock(datetime(year, month, day, 10))


def test_wrong_contact_reveals_nothing_and_locks_after_three_attempts(store):
    state, today = VerificationState(), clock(2026, 3, 1)

    unknown = get_order_status(store, state, today, order_id=999, email="x@y.com")
    wrong = get_order_status(store, state, today, order_id=8, email="someone@else.com")
    assert unknown["error"] == wrong["error"]  # can't probe which order numbers exist
    assert "BRJL5544332BR" not in str(wrong) and "Ana" not in str(wrong)

    get_order_status(store, state, today, order_id=8, email="third@try.com")
    assert state.failed_attempts == MAX_FAILED_ATTEMPTS
    locked = get_order_status(store, state, today, order_id=8, **ANA)  # even the right data is refused now
    assert locked["locked"] is True and "tracking_code" not in locked


def test_verified_customer_sees_own_orders_only_and_late_shipments_are_flagged(store):
    state = VerificationState()
    assert get_order_status(store, state, clock(2026, 3, 1), order_id=8)["needs"]  # no contact yet

    shipped = get_order_status(store, state, clock(2026, 3, 1), order_id=8, **ANA)
    assert shipped["status"] == "shipped" and shipped["tracking_code"] == "BRJL5544332BR"
    assert shipped["delivery_estimate_passed"] and shipped["days_past_estimate"] == 1  # ETA was 28/02

    # phone in another format works, and afterwards the same customer needs no contact again
    state = VerificationState()
    get_order_status(store, state, clock(2026, 3, 1), order_id=2, phone="+55 67 98321-6543")  # customer 7
    cancelled = get_order_status(store, state, clock(2026, 3, 1), order_id=20)
    assert cancelled["cancellation_reason"].startswith("Endereço de entrega inválido")
    assert get_order_status(store, state, clock(2026, 3, 1), order_id=8)["needs"]  # someone else's order


def test_regret_window_is_7_days_from_receipt(store):
    state = VerificationState()
    inside = check_return_eligibility(store, state, clock(2026, 2, 20), 7, "regret", **LETICIA)
    assert inside["eligible"] and inside["deadline"] == "24/02/2026" and inside["days_left"] == 4
    assert inside["conditions_to_confirm_with_customer"]

    outside = check_return_eligibility(store, state, clock(2026, 2, 25), 7, "regret")
    assert not outside["eligible"] and outside["days_past_deadline"] == 1


def test_defect_after_30_days_points_to_warranty_and_undelivered_has_no_window(store):
    state = VerificationState()
    defect = check_return_eligibility(store, state, clock(2026, 3, 10), 7, "defect", **LETICIA)
    assert not defect["eligible"] and defect["deadline"] == "05/03/2026"  # 30 days from the purchase (03/02)
    assert defect["legal_warranty_active"] and defect["legal_warranty_until"] == "18/05/2026"

    get_order_status(store, state, clock(2026, 3, 10), order_id=8, **ANA)
    not_delivered = check_return_eligibility(store, state, clock(2026, 3, 10), 8, "regret")
    assert not_delivered["code"] == "not_delivered_yet"
