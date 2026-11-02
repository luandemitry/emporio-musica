"""Payment simulation (manual, sections 3, 3.1 and 6.2).

All the arithmetic lives here so the LLM never has to do it. Interpretations:
- credit card: parcel minimum is R$ 50 up to 3x, R$ 80 from 4x to 6x and R$ 100 from 7x
  to 12x (the detailed rules of 3.1 win over the summary row of the table in section 3);
  1x on credit has no minimum;
- the PIX discount (5%) does not apply to promotional prices (6.2);
- combining payment methods is allowed above R$ 2,000.00.
"""

from __future__ import annotations

from decimal import Decimal

from ..data import DataStore
from ..pricing import PIX_DISCOUNT_PERCENT, format_brl, money, price_info, quantize
from .products import availability

MAX_INSTALLMENTS = 12
COMBINE_ABOVE = Decimal("2000")
METHODS = ("pix", "debit", "boleto", "credit")


def min_installment_value(n: int) -> Decimal:
    if n == 1:
        return Decimal(0)
    if n <= 3:
        return Decimal(50)
    if n <= 6:
        return Decimal(80)
    return Decimal(100)


def allowed_installments(total: Decimal) -> list[int]:
    return [n for n in range(1, MAX_INSTALLMENTS + 1) if total / n >= min_installment_value(n)]


def simulate_payment(
    store: DataStore,
    product_id: int | None = None,
    quantity: int = 1,
    amount: float | None = None,
    promotional: bool = False,
    method: str | None = None,
) -> dict:
    """Payment options for a product (or a plain amount): PIX, debit, boleto and installments."""
    if (product_id is None) == (amount is None):
        return {"error": "Provide exactly one of product_id or amount."}
    if method is not None and method not in METHODS:
        return {"error": f"method must be one of {', '.join(METHODS)}."}
    quantity = max(1, int(quantity))

    basis: dict
    if product_id is not None:
        product = store.products.get(int(product_id))
        if product is None:
            return {"error": f"There is no product with id {product_id}."}
        info = price_info(store, product)
        total = quantize(info.price * quantity)
        promotional = info.discount_percent > 0
        basis = {
            "product": product.name,
            "quantity": quantity,
            "unit_price": money(info.price),
            "availability": availability(product),
        }
        if promotional:
            basis["promotion"] = f"{info.promotion} ({info.discount_percent}% off, list price {format_brl(info.list_price)})"
        if not product.is_available:
            basis["warning"] = "This product cannot be sold right now; do not offer to close the sale."
    else:
        total = quantize(Decimal(str(amount)))
        basis = {"amount_given": money(total), "treated_as": "promotional price" if promotional else "list price"}

    pix_total = total if promotional else quantize(total * (100 - PIX_DISCOUNT_PERCENT) / 100)
    installments = allowed_installments(total)
    options = {
        "pix": {
            "total": money(pix_total),
            "discount_percent": 0 if promotional else int(PIX_DISCOUNT_PERCENT),
            **({"note": "No PIX discount on promotional prices."} if promotional else {}),
        },
        "debit": {"total": money(total), "note": "À vista."},
        "boleto": {"total": money(total), "note": "À vista; compensação em até 3 dias úteis."},
        "credit": {
            "total": money(total),
            "interest": "none (sem juros)",
            "max_installments": max(installments),
            "installments": [{"n": n, "each": money(quantize(total / n))} for n in installments],
            "note": "Parcelas podem diferir em centavos na última.",
        },
    }
    return {
        "basis": basis,
        "total": money(total),
        "options": {method: options[method]} if method else options,
        "can_combine_payment_methods": total > COMBINE_ABOVE,
    }
