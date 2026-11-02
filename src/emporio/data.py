"""Loading and normalisation of the store data (CSV files).

Everything here is deterministic and LLM-free: the agent only sees the data
through the tools, which are built on top of this module.
"""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path

DATA_DIR_ENV = "EMPORIO_DATA_DIR"


def default_data_dir() -> Path:
    """Data directory: ``$EMPORIO_DATA_DIR`` or ``<repo root>/data``."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data"


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class ProductStatus(str, Enum):
    ACTIVE = "active"
    DISCONTINUED = "discontinued"
    COMING_SOON = "coming_soon"


class OrderStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


# Customer-facing labels stay in Portuguese: the agent talks to Brazilian customers.
ORDER_STATUS_LABEL_PT = {
    OrderStatus.PENDING: "pendente (aguardando confirmação)",
    OrderStatus.CONFIRMED: "confirmado (em preparação)",
    OrderStatus.SHIPPED: "enviado",
    OrderStatus.DELIVERED: "entregue",
    OrderStatus.CANCELLED: "cancelado",
}


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    description: str


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    price: Decimal
    category_id: int
    description: str
    stock: int
    status: ProductStatus
    created_at: date
    specs: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def is_available(self) -> bool:
        """Sellable right now: active and in stock. Never claim availability without this."""
        return self.status is ProductStatus.ACTIVE and self.stock > 0


@dataclass(frozen=True)
class Promotion:
    id: int
    product_id: int
    discount_percent: int
    description: str
    is_active: bool


@dataclass(frozen=True)
class Customer:
    id: int
    name: str
    phone: str
    email: str
    city: str


@dataclass(frozen=True)
class PaymentMethod:
    kind: str  # pix | boleto | debit | credit
    installments: int = 1

    _LABELS = {"pix": "PIX", "boleto": "boleto bancário", "debit": "cartão de débito"}

    @classmethod
    def parse(cls, raw: str) -> "PaymentMethod":
        raw = raw.strip().lower()
        if raw in ("pix", "boleto", "debit"):
            return cls(raw)
        match = re.fullmatch(r"credit_(\d+)x", raw)
        if match:
            return cls("credit", int(match.group(1)))
        raise ValueError(f"Unknown payment method: {raw!r}")

    @property
    def label_pt(self) -> str:
        if self.kind == "credit":
            if self.installments == 1:
                return "cartão de crédito à vista"
            return f"cartão de crédito em {self.installments}x"
        return self._LABELS[self.kind]


@dataclass(frozen=True)
class OrderItem:
    product_id: int
    quantity: int


@dataclass(frozen=True)
class Order:
    id: int
    customer_id: int
    order_date: date
    status: OrderStatus
    total: Decimal
    payment: PaymentMethod
    tracking_code: str | None
    estimated_delivery: date | None
    notes: str | None
    items: tuple[OrderItem, ...]


# --------------------------------------------------------------------------- #
# Identifier normalisation (used by identity verification)
# --------------------------------------------------------------------------- #


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_phone(value: str) -> str:
    """Digits only, without the 55 country code: '(67) 99812-3456' == '+55 67 99812-3456'."""
    digits = re.sub(r"\D", "", value)
    if digits.startswith("55") and len(digits) > 11:
        digits = digits[2:]
    return digits


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def _opt(value: str) -> str | None:
    return value or None


def _opt_date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


@dataclass
class DataStore:
    categories: dict[int, Category]
    products: dict[int, Product]
    promotions: tuple[Promotion, ...]
    customers: dict[int, Customer]
    orders: dict[int, Order]

    def category_name(self, category_id: int) -> str:
        cat = self.categories.get(category_id)
        return cat.name if cat else "Sem categoria"

    def active_promotion(self, product_id: int) -> Promotion | None:
        """Current promotion of a product. Promotions don't stack: the highest one wins."""
        active = [p for p in self.promotions if p.product_id == product_id and p.is_active]
        return max(active, key=lambda p: p.discount_percent) if active else None

    def data_quality_issues(self) -> list[str]:
        """Anomalies found in the data, so they can be handled and documented."""
        issues: list[str] = []

        for p in self.products.values():
            if p.status is ProductStatus.ACTIVE and p.stock == 0:
                issues.append(f"Product {p.id} ({p.name}) is 'active' but out of stock.")
            if p.status is not ProductStatus.ACTIVE and p.stock > 0:
                issues.append(
                    f"Product {p.id} ({p.name}) is '{p.status.value}' but has stock {p.stock}."
                )
            if p.category_id not in self.categories:
                issues.append(f"Product {p.id} points to unknown category {p.category_id}.")

        used = {p.category_id for p in self.products.values()}
        for cat in self.categories.values():
            if cat.id not in used:
                issues.append(f"Category {cat.id} ({cat.name}) has no products.")

        for o in self.orders.values():
            if o.customer_id not in self.customers:
                issues.append(f"Order {o.id} points to unknown customer {o.customer_id}.")
            expected = Decimal(0)
            for item in o.items:
                product = self.products.get(item.product_id)
                if product is None:
                    issues.append(f"Order {o.id} contains unknown product {item.product_id}.")
                    continue
                expected += product.price * item.quantity
                if product.status is not ProductStatus.ACTIVE:
                    issues.append(
                        f"Order {o.id} contains product {product.id} with status '{product.status.value}'."
                    )
            if expected and expected != o.total:
                issues.append(
                    f"Order {o.id}: total {o.total} differs from the sum of list prices {expected}."
                )
            if o.status in (OrderStatus.SHIPPED, OrderStatus.DELIVERED) and not o.tracking_code:
                issues.append(f"Order {o.id} is '{o.status.value}' but has no tracking code.")

        for label, values in (
            ("e-mail", [normalize_email(c.email) for c in self.customers.values()]),
            ("phone", [normalize_phone(c.phone) for c in self.customers.values()]),
        ):
            for v in sorted({v for v in values if values.count(v) > 1}):
                issues.append(f"More than one customer shares the same {label}: {v}.")

        return issues


def load_store(data_dir: Path | str | None = None) -> DataStore:
    """Read the CSV files in ``data_dir`` and return typed, normalised data."""
    base = Path(data_dir) if data_dir else default_data_dir()

    categories = {
        int(r["category_id"]): Category(int(r["category_id"]), r["name"], r["description"])
        for r in _read_csv(base / "categories.csv")
    }

    products = {}
    for r in _read_csv(base / "products.csv"):
        pid = int(r["product_id"])
        products[pid] = Product(
            id=pid,
            name=r["name"],
            price=Decimal(r["price_brl"]),
            category_id=int(r["category_id"]),
            description=r["description"],
            stock=int(r["stock_quantity"]),
            status=ProductStatus(r["status"]),
            created_at=date.fromisoformat(r["created_at"]),
            specs=json.loads(r["specs"]) if r["specs"] else {},
        )

    promotions = tuple(
        Promotion(
            id=int(r["promotion_id"]),
            product_id=int(r["product_id"]),
            discount_percent=int(r["discount_percent"]),
            description=r["description"],
            is_active=r["is_active"] == "1",
        )
        for r in _read_csv(base / "promotions.csv")
    )

    customers = {
        int(r["customer_id"]): Customer(
            int(r["customer_id"]), r["name"], r["phone"], r["email"], r["city"]
        )
        for r in _read_csv(base / "customers.csv")
    }

    items_by_order: dict[int, list[OrderItem]] = {}
    for r in _read_csv(base / "order_items.csv"):
        items_by_order.setdefault(int(r["order_id"]), []).append(
            OrderItem(int(r["product_id"]), int(r["quantity"]))
        )

    orders = {}
    for r in _read_csv(base / "orders.csv"):
        oid = int(r["order_id"])
        orders[oid] = Order(
            id=oid,
            customer_id=int(r["customer_id"]),
            order_date=date.fromisoformat(r["order_date"]),
            status=OrderStatus(r["status"]),
            total=Decimal(r["total_brl"]),
            payment=PaymentMethod.parse(r["payment_method"]),
            tracking_code=_opt(r["tracking_code"]),
            estimated_delivery=_opt_date(r["estimated_delivery"]),
            notes=_opt(r["notes"]),
            items=tuple(items_by_order.get(oid, [])),
        )

    return DataStore(categories, products, promotions, customers, orders)
