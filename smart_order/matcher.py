from __future__ import annotations

import re
from dataclasses import dataclass
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

        catalog.append(
            CatalogProduct(
                id_producto=pid,
                nombre=info["nombre"],
                categoria=info["categoria"],
                subcategoria=info["subcategoria"],
                precio=price,
            )
        )
    return catalog


def clean_product_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
