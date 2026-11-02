from datetime import datetime

import pytest

from emporio.clock import Clock
from emporio.data import load_store
from emporio.tools.payments import simulate_payment
from emporio.tools.store_info import easter, store_status


@pytest.fixture(scope="module")
def store():
    return load_store()


def at(year, month, day, hour, minute=0):
    return Clock(datetime(year, month, day, hour, minute))


@pytest.mark.parametrize(
    "when, is_open, expected, reason",
    [
        (at(2026, 11, 9, 10), True, "18:00", None),  # Monday morning: closes at 18:00
        (at(2026, 11, 7, 14), False, "segunda-feira, 09/11/2026 às 09:00", "fora do horário"),  # Saturday after 13:00
        (at(2026, 11, 2, 15), False, "terça-feira, 03/11/2026 às 09:00", "Finados"),  # a Monday, but a holiday
        (at(2026, 4, 3, 10), False, "sábado, 04/04/2026 às 09:00", "Sexta-feira Santa"),  # Easter moves every year
    ],
)
def test_store_status(when, is_open, expected, reason):
    status = store_status(when)
    assert status["is_open"] is is_open
    assert expected in (status.get("closes_at"), status.get("next_opening"))
    if reason:
        assert reason in status["closed_reason"]


def test_easter_moves_between_years():
    assert easter(2026).isoformat() == "2026-04-05"
    assert easter(2027).isoformat() == "2027-03-28"


def test_pix_discount_does_not_stack_on_promotions(store):
    promo = simulate_payment(store, product_id=127)  # 359 with an active 10% promotion
    assert promo["total"]["value"] == 323.10
    assert promo["options"]["pix"]["total"]["value"] == 323.10 and promo["options"]["pix"]["discount_percent"] == 0
    assert promo["options"]["credit"]["max_installments"] == 4  # 323.10/4 = 80.78 >= 80; 5x would be 64.62

    regular = simulate_payment(store, product_id=95)  # Takamine GD20, no active promotion
    assert regular["options"]["pix"]["total"]["value"] == 2089.05
    assert regular["can_combine_payment_methods"] is True  # above R$ 2.000


def test_installment_minimums_per_tier(store):
    result = simulate_payment(store, amount=1199)
    credit = result["options"]["credit"]
    assert credit["max_installments"] == 11  # 12x would be 99.92, under the R$ 100 minimum
    assert credit["installments"][-1]["each"]["value"] == 109.0
    assert result["can_combine_payment_methods"] is False

    assert simulate_payment(store, amount=140)["options"]["credit"]["max_installments"] == 2  # 3x = 46.67 < R$ 50
