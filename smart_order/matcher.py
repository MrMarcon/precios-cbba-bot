from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Any

import pandas as pd
import requests

BASE_URL = "https://mauforonda.github.io/precios"

CITY_DATA_FILES = {
    "cochabamba": "cochabamba",
    "la_paz": "la_paz",
    "santa_cruz": "santa_cruz",
}


@dataclass(frozen=True)
class CatalogProduct:
    id_producto: str
    nombre: str
    categoria: str
    subcategoria: str
    precio: float | None
    marca: str | None = None
    tamano: str | None = None


@dataclass(frozen=True)
class ProductMatch:
    id_producto: str
    nombre: str
    marca: str | None
    tamano: str | None
    precio: float | None
    categoria: str
    subcategoria: str
    score: float


SIZE_PATTERN = re.compile(
    r"\b(?:x\s*)?\d+(?:[.,]\d+|/\d+)?\s*(?:kg|kilo|kilos|g|gr|gramos|l|lt|lts|litro|litros|ml|cc|oz|un|und|unidad|unidades|cm|m)\b",
    re.IGNORECASE,
)

GENERIC_PRODUCT_PREFIXES = {
    "aceite",
    "agua",
    "alimento",
    "arroz",
    "atun",
    "cafe",
    "caramelo",
    "cerveza",
    "chocolate",
    "crema",
    "fideo",
    "galleta",
    "gaseosa",
    "harina",
    "helado",
    "jabon",
    "jugo",
    "leche",
    "mantequilla",
    "mermelada",
    "pan",
    "papel",
    "queso",
    "sal",
    "salsa",
    "shampoo",
    "sopa",
    "te",
    "vino",
    "yogurt",
}

UNBRANDED_PRODUCE = {
    "acelga",
    "ajo",
    "albahaca",
    "apio",
    "arveja",
    "banana",
    "cebolla",
    "ciruela",
    "frutilla",
    "guineo",
    "kiwi",
    "lechuga",
    "lima",
    "limon",
    "mandarina",
    "manzana",
    "melon",
    "naranja",
    "papa",
    "papaya",
    "pina",
    "sandia",
    "tomate",
    "zanahoria",
}

VARIANT_WORDS = {
    "amarillo",
    "azul",
    "blanco",
    "chocolate",
    "clasica",
    "clasico",
    "con",
    "descremada",
    "diet",
    "dulce",
    "entera",
    "familiar",
    "frutilla",
    "grande",
    "integral",
    "light",
    "mediano",
    "naranja",
    "natural",
    "original",
    "pequeno",
    "rojo",
    "sin",
    "surtido",
    "tradicional",
    "vainilla",
    "verde",
    "zero",
}

QUERY_EXPANSIONS = {
    "desl": "descremada",
    "descrem": "descremada",
    "desc": "descremada",
    "coca": "coca cola",
    "atun": "atun",
    "azuc": "azucar",
    "deterg": "detergente",
    "fideos": "fideo",
    "galletas": "galleta",
    "verd": "verdura",
}


def get_hashed_urls(html: str, city: str = "cochabamba") -> tuple[str, str]:
    city_file = CITY_DATA_FILES.get(city)
    if not city_file:
        raise ValueError(f"Ciudad no soportada: {city}")

    city_match = re.search(rf'_file/data/{re.escape(city_file)}\.([a-f0-9]+)\.csv', html)
    prod_match = re.search(r'_file/data/productos\.([a-f0-9]+)\.json', html)
    if not city_match or not prod_match:
        raise ValueError("No se encontraron las URLs hasheadas en el HTML del sitio")

    city_url = f"{BASE_URL}/_file/data/{city_file}.{city_match.group(1)}.csv"
    prod_url = f"{BASE_URL}/_file/data/productos.{prod_match.group(1)}.json"
    return city_url, prod_url


def fetch_catalog_sources(city: str = "cochabamba") -> tuple[pd.DataFrame, dict[str, dict[str, str]]]:
    html_resp = requests.get(BASE_URL + "/", timeout=15)
    html_resp.raise_for_status()
    city_url, prod_url = get_hashed_urls(html_resp.text, city=city)

    prices = pd.read_csv(city_url)
    products_payload = requests.get(prod_url, timeout=15).json()
    return prices, normalize_product_map(products_payload)


def normalize_product_map(products_payload: Any) -> dict[str, dict[str, str]]:
    if isinstance(products_payload, list):
        return {
            str(p["id_producto"]): {
                "nombre": clean_product_name(p.get("producto", "?")),
                "categoria": clean_product_name(p.get("categoria", "Otros")),
                "subcategoria": clean_product_name(p.get("subcategoria", "")),
            }
            for p in products_payload
            if isinstance(p, dict) and "id_producto" in p
        }

    if isinstance(products_payload, dict):
        normalized = {}
        for key, value in products_payload.items():
            if isinstance(value, dict):
                normalized[str(key)] = {
                    "nombre": clean_product_name(value.get("producto", "?")),
                    "categoria": clean_product_name(value.get("categoria", "Otros")),
                    "subcategoria": clean_product_name(value.get("subcategoria", "")),
                }
            else:
                normalized[str(key)] = {
                    "nombre": clean_product_name(value),
                    "categoria": "Otros",
                    "subcategoria": "",
                }
        return normalized

    raise ValueError("Formato desconocido para productos.json")


def load_catalog(city: str = "cochabamba") -> list[CatalogProduct]:
    prices, products = fetch_catalog_sources(city=city)
    if "id_producto" not in prices.columns:
        raise ValueError(f"Columna 'id_producto' no encontrada. Columnas: {list(prices.columns)}")

    catalog: list[CatalogProduct] = []
    for _, row in prices.iterrows():
        pid = str(int(row["id_producto"]))
        info = products.get(pid)
        if not info:
            continue

        price = None
        if "hoy" in row and not pd.isna(row["hoy"]):
            price = float(row["hoy"])

        marca, tamano = parse_product_fields(info["nombre"])
        catalog.append(
            CatalogProduct(
                id_producto=pid,
                nombre=info["nombre"],
                categoria=info["categoria"],
                subcategoria=info["subcategoria"],
                precio=price,
                marca=marca,
                tamano=tamano,
            )
        )
    return catalog


def clean_product_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def match_items(
    items: list[str],
    catalog: list[CatalogProduct],
    *,
    limit: int = 3,
) -> dict[str, list[ProductMatch]]:
    prepared_catalog = [ensure_product_fields(product) for product in catalog if product.nombre]
    choices = {product.id_producto: searchable_product_text(product) for product in prepared_catalog}
    products_by_id = {product.id_producto: product for product in prepared_catalog}

    result: dict[str, list[ProductMatch]] = {}
    for item in items:
        query = normalize_query(item)
        if not query:
            result[item] = []
            continue

        matches = _extract_matches(query, choices, limit=limit)
        result[item] = [
            ProductMatch(
                id_producto=product.id_producto,
                nombre=product.nombre,
                marca=product.marca,
                tamano=product.tamano,
                precio=product.precio,
                categoria=product.categoria,
                subcategoria=product.subcategoria,
                score=round(float(score), 2),
            )
            for product_id, score in matches
            if (product := products_by_id.get(product_id))
        ]
    return result


def parse_product_fields(nombre: str) -> tuple[str | None, str | None]:
    clean_name = clean_product_name(nombre)
    size = parse_size(clean_name)
    brand = parse_brand(clean_name)
    return brand, size


def parse_size(nombre: str) -> str | None:
    matches = []
    for match in SIZE_PATTERN.finditer(nombre):
        value = re.sub(r"\s+", " ", match.group(0)).strip()
        if value.lower() not in {item.lower() for item in matches}:
            matches.append(value)
    return " / ".join(matches) if matches else None


def parse_brand(nombre: str) -> str | None:
    without_size = SIZE_PATTERN.sub(" ", nombre)
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", without_size)
    if not words:
        return None

    first = normalize_text(words[0])
    if first in UNBRANDED_PRODUCE:
        return None

    start = 1 if first in GENERIC_PRODUCT_PREFIXES else 0
    brand_words: list[str] = []
    for word in words[start:]:
        normalized = normalize_text(word)
        if brand_words and normalized in VARIANT_WORDS:
            break
        if normalized in {"de", "del", "la", "las", "los", "y"} and not brand_words:
            continue
        brand_words.append(word)
        if len(brand_words) >= 2:
            break

    if not brand_words:
        return None
    return " ".join(brand_words)


def ensure_product_fields(product: CatalogProduct) -> CatalogProduct:
    if product.marca and product.tamano:
        return product
    marca, tamano = parse_product_fields(product.nombre)
    return replace(
        product,
        marca=product.marca or marca,
        tamano=product.tamano or tamano,
    )


def searchable_product_text(product: CatalogProduct) -> str:
    parts = [
        product.nombre,
        product.marca or "",
        product.tamano or "",
        product.categoria,
        product.subcategoria,
    ]
    return normalize_query(" ".join(parts))


def normalize_query(value: str) -> str:
    text = normalize_text(value)
    text = re.sub(r"(\d)(kg|kilo|kilos|g|gr|l|lt|lts|ml|cc|oz|un|und)\b", r"\1 \2", text)
    tokens = [QUERY_EXPANSIONS.get(token, token) for token in text.split()]
    return " ".join(tokens)


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    text = without_accents.lower()
    text = text.replace("ñ", "n")
    text = re.sub(r"[^a-z0-9/.,]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_matches(query: str, choices: dict[str, str], *, limit: int) -> list[tuple[str, float]]:
    try:
        from rapidfuzz import fuzz
    except ImportError:
        scored = [
            (product_id, SequenceMatcher(None, query, choice).ratio() * 100)
            for product_id, choice in choices.items()
        ]
        return sorted(scored, key=lambda item: item[1], reverse=True)[:limit]

    query_tokens = set(query.split())
    scored = []
    for product_id, choice in choices.items():
        choice_tokens = set(choice.split())
        coverage = 0.0
        if query_tokens:
            coverage = len(query_tokens & choice_tokens) / len(query_tokens) * 100
        fuzzy_score = fuzz.WRatio(query, choice)
        scored.append((product_id, fuzzy_score * 0.7 + coverage * 0.3))
    return sorted(scored, key=lambda item: item[1], reverse=True)[:limit]
