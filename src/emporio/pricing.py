"""Price rules shared by the product and payment tools.

Store policy (manual, section 6.2):
- promotions don't stack; the PIX discount (5%) does not apply to promotional prices;
- promotional prices are always shown next to the original price and the percentage.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .data import DataStore, Product

PIX_DISCOUNT_PERCENT = Decimal(5)
_CENTS = Decimal("0.01")


def quantize(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def format_brl(value: Decimal) -> str:
    """1199 -> 'R$ 1.199,00'."""
    us = f"{quantize(Decimal(value)):,.2f}"
    return "R$ " + us.replace(",", "_").replace(".", ",").replace("_", ".")


def money(value: Decimal) -> dict:
    """A monetary value for tool results: the number plus the text to show the customer."""
    return {"value": float(quantize(Decimal(value))), "display": format_brl(value)}


@dataclass(frozen=True)
class PriceInfo:
    list_price: Decimal
    price: Decimal  # what the customer pays by default (promotion applied, if any)
    discount_percent: int  # 0 when there is no active promotion
    promotion: str | None
    pix_price: Decimal | None  # None when the price is promotional (no stacking)


def price_info(store: DataStore, product: Product) -> PriceInfo:
    promo = store.active_promotion(product.id)
    if promo:
        price = quantize(product.price * (100 - promo.discount_percent) / 100)
        return PriceInfo(product.price, price, promo.discount_percent, promo.description, None)
    pix = quantize(product.price * (100 - PIX_DISCOUNT_PERCENT) / 100)
    return PriceInfo(product.price, product.price, 0, None, pix)
