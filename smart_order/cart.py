from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Protocol, Sequence

DEFAULT_HIPERMAXI_URL = "https://www.hipermaxi.com/"
DEFAULT_USER_DATA_DIR = "playwright-profile"

SEARCH_SELECTORS = [
    'input[type="search"]',
    'input[placeholder*="buscar" i]',
    'input[aria-label*="buscar" i]',
    'input[name*="search" i]',
    'input[name*="buscar" i]',
]

ADD_BUTTON_RE = re.compile(r"(agregar|anadir|añadir|carrito|comprar)", re.IGNORECASE)
CART_RE = re.compile(r"(carrito|cart|mi compra)", re.IGNORECASE)
BLOCK_RE = re.compile(r"(captcha|radware|perfdrive|bot|request unblock|blocked)", re.IGNORECASE)
TOTAL_RE = re.compile(r"(?:total|subtotal)[^\n]{0,80}?(Bs\.?\s*[0-9]+(?:[.,][0-9]{1,2})?)", re.IGNORECASE)


class CartProduct(Protocol):
    nombre: str


@dataclass(frozen=True)
class CartResult:
    cart_url: str
    total: str | None = None
    added: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    blocked: bool = False


def build_cart(
    products: Sequence[CartProduct],
    *,
    user_data_dir: str = DEFAULT_USER_DATA_DIR,
    headless: bool = False,
    base_url: str | None = None,
    manual_wait_seconds: int = 180,
) -> CartResult:
    if not products:
        return CartResult(cart_url="", failures=["No hay productos para agregar"])

    base_url = base_url or os.environ.get("HIPERMAXI_URL", DEFAULT_HIPERMAXI_URL)

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Falta instalar la dependencia 'playwright'") from exc

    added: list[str] = []
    failures: list[str] = []

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            viewport={"width": 1440, "height": 950},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
            if is_blocked(page):
                if headless:
                    return CartResult(cart_url=page.url, failures=["Hipermaxi pidio CAPTCHA"], blocked=True)
                wait_for_manual_unblock(page, timeout_seconds=manual_wait_seconds)

            for product in products:
                try:
                    add_product_to_cart(page, product.nombre)
                    added.append(product.nombre)
                except Exception as exc:
                    failures.append(f"{product.nombre}: {exc}")

            open_cart_if_possible(page)
            total = extract_total(page)
            return CartResult(cart_url=page.url, total=total, added=added, failures=failures, blocked=is_blocked(page))
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"Hipermaxi no respondio a tiempo: {exc}") from exc
        finally:
            context.close()


def verify_site_navigation(
    *,
    user_data_dir: str = DEFAULT_USER_DATA_DIR,
    headless: bool = True,
    base_url: str | None = None,
) -> CartResult:
    base_url = base_url or os.environ.get("HIPERMAXI_URL", DEFAULT_HIPERMAXI_URL)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Falta instalar la dependencia 'playwright'") from exc

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
            return CartResult(cart_url=page.url, blocked=is_blocked(page))
        finally:
            context.close()


def add_product_to_cart(page: object, product_name: str) -> None:
    search_input = find_visible_locator(page, SEARCH_SELECTORS)
    if search_input is None:
        raise RuntimeError("No encontre el buscador")

    search_input.click(timeout=10_000)
    search_input.fill(product_name)
    search_input.press("Enter")
    page.wait_for_load_state("domcontentloaded", timeout=20_000)
    page.wait_for_timeout(1500)

    click_first_relevant_result(page, product_name)
    page.wait_for_timeout(1000)

    add_button = page.get_by_role("button", name=ADD_BUTTON_RE).first
    if not add_button.is_visible(timeout=5000):
        raise RuntimeError("No encontre boton para agregar")
    add_button.click(timeout=10_000)
    page.wait_for_timeout(1500)


def click_first_relevant_result(page: object, product_name: str) -> None:
    keywords = meaningful_keywords(product_name)
    if not keywords:
        return

    body = page.locator("body")
    for keyword in keywords:
        candidate = body.get_by_text(re.compile(re.escape(keyword), re.IGNORECASE)).first
        try:
            if candidate.is_visible(timeout=2500):
                candidate.click(timeout=5000)
                return
        except Exception:
            continue


def open_cart_if_possible(page: object) -> None:
    for role in ("link", "button"):
        locator = page.get_by_role(role, name=CART_RE).first
        try:
            if locator.is_visible(timeout=2000):
                locator.click(timeout=5000)
                page.wait_for_timeout(1500)
                return
        except Exception:
            continue


def extract_total(page: object) -> str | None:
    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return None
    match = TOTAL_RE.search(text)
    return match.group(1) if match else None


def is_blocked(page: object) -> bool:
    try:
        title = page.title(timeout=5000)
    except Exception:
        title = ""
    try:
        body = page.locator("body").inner_text(timeout=5000)
    except Exception:
        body = ""
    return bool(BLOCK_RE.search(f"{title}\n{body}\n{page.url}"))


def wait_for_manual_unblock(page: object, *, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not is_blocked(page):
            return
        page.wait_for_timeout(2000)
    raise RuntimeError("Hipermaxi sigue mostrando CAPTCHA/bloqueo despues de la espera manual")


def find_visible_locator(page: object, selectors: Sequence[str]) -> object | None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=3000):
                return locator
        except Exception:
            continue
    return None


def meaningful_keywords(product_name: str) -> list[str]:
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", product_name)
    return [word for word in words if len(word) >= 4][:4]
