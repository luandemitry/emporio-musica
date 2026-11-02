"""Catalogue tools: search, product lookup and category overview.

All prices returned are what the customer would actually pay (active promotion
applied). Tool results are plain JSON-serialisable dicts; the free-text ``note``
fields are instructions for the LLM, not text to be read out to the customer.
"""

from __future__ import annotations

import difflib
from decimal import Decimal

from ..data import DataStore, Product, ProductStatus
from ..pricing import PriceInfo, format_brl, price_info
from ..text import compact, tokenize

DEFAULT_LIMIT = 8
MAX_LIMIT = 15
LOW_STOCK_THRESHOLD = 2
MAX_ALTERNATIVES = 3


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def availability(product: Product) -> str:
    if product.status is ProductStatus.DISCONTINUED:
        return "discontinued"
    if product.status is ProductStatus.COMING_SOON:
        return "coming_soon"
    return "in_stock" if product.stock > 0 else "out_of_stock"


def _full_index(store: DataStore, product: Product) -> set[str]:
    """Tokens searchable for a product: name, description, category and spec values."""
    parts = [product.name, product.description, store.category_name(product.category_id)]
    parts += [str(v) for v in product.specs.values()]
    return set(tokenize(" ".join(parts)))


def _money_fields(info: PriceInfo) -> dict:
    out = {"price": float(info.price), "price_display": format_brl(info.price)}
    if info.discount_percent:
        out.update(
            original_price=float(info.list_price),
            original_price_display=format_brl(info.list_price),
            discount_percent=info.discount_percent,
            promotion=info.promotion,
        )
    if info.pix_price is not None:
        out.update(pix_price=float(info.pix_price), pix_price_display=format_brl(info.pix_price))
    return out


def _summary(store: DataStore, product: Product) -> dict:
    out = {
        "product_id": product.id,
        "name": product.name,
        "category": store.category_name(product.category_id),
        "description": product.description,
        "availability": availability(product),
        **_money_fields(price_info(store, product)),
    }
    if product.is_available:  # stock of non-sellable products would only mislead
        out["stock"] = product.stock
        if product.stock <= LOW_STOCK_THRESHOLD:
            out["low_stock"] = True
    return out


def _alternatives(store: DataStore, product: Product) -> list[dict]:
    """Available products of the same category, closest in price first."""
    pool = [
        p
        for p in store.products.values()
        if p.id != product.id and p.category_id == product.category_id and p.is_available
    ]
    pool.sort(key=lambda p: abs(p.price - product.price))
    return [_summary(store, p) for p in pool[:MAX_ALTERNATIVES]]


def _detail(store: DataStore, product: Product) -> dict:
    out = _summary(store, product)
    out["specs"] = product.specs
    expired = [
        {"promotion": p.description, "discount_percent": p.discount_percent}
        for p in store.promotions
        if p.product_id == product.id and not p.is_active
    ]
    if expired:
        out["expired_promotions"] = expired
        out["expired_promotions_note"] = (
            "These promotions are OVER. Never offer them; if the customer mentions one, "
            "say it ended and give the current price."
        )
    if not product.is_available:
        out["alternatives"] = _alternatives(store, product)
        out["note"] = {
            "discontinued": "Discontinued: no longer in the catalogue. Offer the alternatives.",
            "coming_soon": "Not for sale yet (coming soon). Do not confirm availability or take orders.",
            "out_of_stock": "Out of stock right now. Offer the alternatives.",
        }[availability(product)]
    return out


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #


def search_products(
    store: DataStore,
    query: str = "",
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    include_unavailable: bool = False,
    sort: str = "price_asc",
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """Search the catalogue. Every word of ``query`` must match (name, description, ...).

    Price filters compare against the price the customer pays (promotion applied).
    By default only products that can be sold now (active and in stock) are returned.
    """
    limit = max(1, min(int(limit), MAX_LIMIT))
    q_tokens = set(tokenize(query or ""))

    category_ids: set[int] | None = None
    if category:
        wanted = set(tokenize(category))
        category_ids = {c.id for c in store.categories.values() if wanted and wanted <= set(tokenize(c.name))}

    low = Decimal(str(min_price)) if min_price is not None else None
    high = Decimal(str(max_price)) if max_price is not None else None

    rows: list[tuple[Product, PriceInfo]] = []
    for p in store.products.values():
        if category_ids is not None and p.category_id not in category_ids:
            continue
        if not include_unavailable and not p.is_available:
            continue
        if q_tokens and not q_tokens <= _full_index(store, p):
            continue
        info = price_info(store, p)
        if (low is not None and info.price < low) or (high is not None and info.price > high):
            continue
        rows.append((p, info))

    sign = -1 if sort == "price_desc" else 1
    rows.sort(key=lambda r: (not r[0].is_available, sign * r[1].price, r[0].name))

    result = {
        "total_matches": len(rows),
        "showing": min(len(rows), limit),
        "products": [_summary(store, p) for p, _ in rows[:limit]],
        "filters": {
            "query": query or None,
            "category": category,
            "min_price": min_price,
            "max_price": max_price,
            "include_unavailable": include_unavailable,
            "sort": sort,
        },
    }
    if not rows:
        result["note"] = (
            "No product matches. Say so honestly; do not invent products. Try fewer words "
            "or offer what the catalogue does have (see catalog_overview)."
        )
        result["catalog_overview"] = list_categories(store)["categories"]
        result["categories_without_products"] = [
            c["name"] for c in result["catalog_overview"] if c["products_in_catalog"] == 0
        ]
    return result


def get_product(store: DataStore, product_id: int | None = None, name: str | None = None) -> dict:
    """Look up one product by id or (approximate) name, with specs and alternatives."""
    if product_id is not None:
        product = store.products.get(int(product_id))
        if product:
            return {"match": "exact", "product": _detail(store, product)}
        return {"match": "not_found", "note": f"There is no product with id {product_id}."}

    query = (name or "").strip()
    if not query:
        return {"match": "not_found", "note": "Provide product_id or name."}

    products = list(store.products.values())
    q_tokens = set(tokenize(query))
    q_compact = compact(query)

    # 1) every query word is a word of the name; 2) the query is a fragment of the name
    # ("GD 20" vs "GD20"); 3) every query word appears anywhere in the product data.
    passes = (
        lambda p: bool(q_tokens) and q_tokens <= set(tokenize(p.name)),
        lambda p: len(q_compact) >= 3 and q_compact in compact(p.name),
        lambda p: bool(q_tokens) and q_tokens <= _full_index(store, p),
    )
    for matches_query in passes:
        found = [p for p in products if matches_query(p)]
        if not found:
            continue
        exact = [p for p in found if compact(p.name) == q_compact]
        if len(found) == 1 or len(exact) == 1:
            return {"match": "exact", "product": _detail(store, (exact or found)[0])}
        found.sort(key=lambda p: (not p.is_available, p.price))
        return {
            "match": "ambiguous",
            "note": "Several products match. Ask which one the customer means.",
            "candidates": [_summary(store, p) for p in found[:6]],
            "total_candidates": len(found),
        }

    # Typo tolerance: compare against the start of each name, ignoring spaces.
    scored = sorted(
        (
            (difflib.SequenceMatcher(None, q_compact, compact(p.name)[: len(q_compact) + 3]).ratio(), p)
            for p in products
        ),
        key=lambda s: -s[0],
    )
    close = [p for score, p in scored[:3] if score >= 0.75]
    return {
        "match": "not_found",
        "note": "No product with that name. Do not guess; ask the customer to confirm.",
        "did_you_mean": [_summary(store, p) for p in close],
    }


def list_categories(store: DataStore) -> dict:
    """Overview of the catalogue: what the store sells, how much, and from what price."""
    out = []
    for cat in store.categories.values():
        in_cat = [p for p in store.products.values() if p.category_id == cat.id]
        sellable = [price_info(store, p).price for p in in_cat if p.is_available]
        entry = {
            "category": cat.name,
            "description": cat.description,
            "products_in_catalog": len(in_cat),
            "available_now": len(sellable),
        }
        if sellable:
            entry["cheapest"] = format_brl(min(sellable))
            entry["most_expensive"] = format_brl(max(sellable))
        out.append(entry)
    return {"categories": [{"name": e.pop("category"), **e} for e in out]}
