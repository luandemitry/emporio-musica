import pytest

from emporio.data import load_store
from emporio.tools.products import get_product, search_products


@pytest.fixture(scope="module")
def store():
    return load_store()


def ids(result):
    return [p["product_id"] for p in result["products"]]


def test_search_acoustic_guitars_under_1000_only_returns_sellable(store):
    result = search_products(store, query="violões", max_price=1000, limit=15)
    found = result["products"]
    assert found and all(p["price"] <= 1000 for p in found)
    assert [p["price"] for p in found] == sorted(p["price"] for p in found)
    assert 96 not in ids(result)  # active, but out of stock
    assert 113 not in ids(result)  # discontinued
    assert all(p["availability"] == "in_stock" for p in found)


def test_price_filter_uses_promotional_price(store):
    # Crafter HT-100: list price 2399, but 18% off right now => 1967.18
    result = search_products(store, query="violão", max_price=2000, sort="price_desc", limit=3)
    crafter = result["products"][0]  # list price is above 2000, but the price paid is not
    assert crafter["product_id"] == 90
    assert crafter["price"] == 1967.18 and crafter["original_price"] == 2399
    assert crafter["discount_percent"] == 18
    assert "pix_price" not in crafter  # PIX discount doesn't stack on promotions


def test_search_ignores_accents_and_plural(store):
    assert ids(search_products(store, query="violões")) == ids(search_products(store, query="violao"))


def test_search_without_results_lists_empty_categories(store):
    result = search_products(store, query="saxofone")
    assert result["total_matches"] == 0
    assert "Instrumentos de Sopro (Madeiras)" in result["categories_without_products"]


def test_get_product_price_pix_and_expired_promotion(store):
    result = get_product(store, name="Takamine GD20")
    product = result["product"]
    assert result["match"] == "exact" and product["product_id"] == 95
    assert product["price"] == 2199 and product["pix_price"] == 2089.05  # 5% off
    assert "discount_percent" not in product  # no active promotion...
    assert product["expired_promotions"] == [{"promotion": "Oferta Especial", "discount_percent": 12}]


def test_get_product_tolerates_spacing_and_never_invents(store):
    assert get_product(store, name="takamine gd 20")["product"]["product_id"] == 95

    ambiguous = get_product(store, name="takamine")
    assert ambiguous["match"] == "ambiguous" and ambiguous["total_candidates"] == 4

    assert get_product(store, name="Gibson SG Standard")["match"] == "not_found"


def test_unavailable_product_comes_with_available_alternatives(store):
    product = get_product(store, product_id=96)["product"]  # active, out of stock
    assert product["availability"] == "out_of_stock" and "stock" not in product
    assert product["alternatives"]
    assert all(a["availability"] == "in_stock" and a["category"] == product["category"] for a in product["alternatives"])

    discontinued = get_product(store, product_id=136)["product"]  # has stock in the CSV
    assert discontinued["availability"] == "discontinued" and "stock" not in discontinued
