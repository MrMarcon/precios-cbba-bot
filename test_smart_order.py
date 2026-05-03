from __future__ import annotations

from smart_order.matcher import CatalogProduct, match_items, parse_product_fields


def test_match_items_returns_best_three_matches():
    catalog = [
        CatalogProduct("1", "Leche PIL Entera 946 ml", "Lacteos", "Leches", 7.5),
        CatalogProduct("2", "Leche PIL Descremada 946 ml", "Lacteos", "Leches", 7.8),
        CatalogProduct("3", "Bebida Lactea Delizia Frutilla 1 L", "Lacteos", "Yogures", 12.0),
        CatalogProduct("4", "Gaseosa Coca Cola Original 2 L", "Bebidas", "Gaseosas", 14.5),
    ]

    matches = match_items(["leche desl"], catalog, limit=3)

    assert len(matches["leche desl"]) == 3
    assert matches["leche desl"][0].nombre == "Leche PIL Descremada 946 ml"
    assert matches["leche desl"][0].marca == "PIL"
    assert matches["leche desl"][0].tamano == "946 ml"


def test_match_items_handles_accents_and_compact_units():
    catalog = [
        CatalogProduct("1", "Gaseosa Coca Cola Original 2 L", "Bebidas", "Gaseosas", 14.5),
        CatalogProduct("2", "Gaseosa Fanta Naranja 2 L", "Bebidas", "Gaseosas", 13.5),
        CatalogProduct("3", "Cafe Bolivia Especial Tostado Grano 1 kg", "Abarrotes", "Cafe", 86.0),
    ]

    matches = match_items(["coca 2l", "café"], catalog, limit=2)

    assert matches["coca 2l"][0].nombre == "Gaseosa Coca Cola Original 2 L"
    assert matches["café"][0].nombre == "Cafe Bolivia Especial Tostado Grano 1 kg"


def test_parse_product_fields_uses_name_as_source_of_truth():
    marca, tamano = parse_product_fields("Gaseosa Coca Cola Original 2 L")

    assert marca == "Coca Cola"
    assert tamano == "2 L"


def test_parse_product_fields_leaves_unbranded_produce_without_brand():
    marca, tamano = parse_product_fields("Manzana Verde 1 kg")

    assert marca is None
    assert tamano == "1 kg"
