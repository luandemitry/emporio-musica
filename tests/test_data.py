from datetime import date

import pytest

from emporio.data import (
    OrderStatus,
    PaymentMethod,
    load_store,
    normalize_email,
    normalize_phone,
)


@pytest.fixture(scope="module")
def store():
    return load_store()


def test_availability_requires_active_status_and_stock(store):
    assert store.products[95].is_available  # active, in stock
    assert not store.products[96].is_available  # active, but stock is 0
    assert not store.products[130].is_available  # "coming soon", even with stock
    assert not store.products[136].is_available  # discontinued, even with stock


def test_only_active_promotions_count(store):
    assert store.active_promotion(127).discount_percent == 10
    # Takamine GD20 (95) has a promotion in the CSV, but it is expired
    assert any(p.product_id == 95 for p in store.promotions)
    assert store.active_promotion(95) is None


def test_orders_are_typed_with_items(store):
    shipped = store.orders[8]
    assert shipped.status is OrderStatus.SHIPPED
    assert shipped.tracking_code == "BRJL5544332BR"
    assert shipped.estimated_delivery == date(2026, 2, 28)
    assert [(i.product_id, i.quantity) for i in store.orders[15].items] == [(82, 1), (106, 1)]

    pending = store.orders[13]  # no tracking/ETA: becomes None, not an empty string
    assert pending.tracking_code is None and pending.estimated_delivery is None


@pytest.mark.parametrize(
    "raw, kind, installments, label",
    [
        ("pix", "pix", 1, "PIX"),
        ("debit", "debit", 1, "cartão de débito"),
        ("credit_6x", "credit", 6, "cartão de crédito em 6x"),
        ("credit_12x", "credit", 12, "cartão de crédito em 12x"),
    ],
)
def test_payment_method_parse(raw, kind, installments, label):
    pm = PaymentMethod.parse(raw)
    assert (pm.kind, pm.installments, pm.label_pt) == (kind, installments, label)


def test_identity_normalization():
    assert normalize_phone("(67) 99812-3456") == normalize_phone("+55 67 99812-3456") == "67998123456"
    assert normalize_email("  Lucas.Mendes@JMail.com ") == "lucas.mendes@jmail.com"


def test_quality_report_flags_known_anomalies(store):
    report = "\n".join(store.data_quality_issues())
    assert "Product 96" in report  # active but out of stock
    assert "Product 130" in report  # "coming soon" with stock
    assert "Category 6" in report  # category without products
    assert "Order 20: total 1400" in report  # total differs from items
