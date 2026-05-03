from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

DEFAULT_MODEL = "claude-sonnet-4-5"
SUPPORTED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

OCR_PROMPT = """Extrae los items de esta lista de compras manuscrita.

Reglas:
- Devuelve solamente JSON valido con esta forma exacta: {"items": ["item 1", "item 2"]}.
- Cada item debe ser un string corto de producto buscable en supermercado.
- Conserva el orden de la lista.
- Ignora productos claramente tachados.
- Expande abreviaturas obvias si mejora la busqueda, por ejemplo "leche desl." -> "leche descremada".
- No inventes marcas, cantidades ni sabores si no se ven.
- Si no hay items claros, devuelve {"items": []}.
"""


class OCRParseError(ValueError):
    pass


def extract_items_from_image(
    image_bytes: bytes,
    media_type: str,
    *,
    client: Any | None = None,
    model: str | None = None,
) -> list[str]:
    if not image_bytes:
        raise ValueError("La imagen esta vacia")
    if media_type not in SUPPORTED_MEDIA_TYPES:
        raise ValueError(f"Tipo de imagen no soportado: {media_type}")

    client = client or _default_anthropic_client()
    model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    encoded_image = base64.b64encode(image_bytes).decode("ascii")

    message = client.messages.create(
        model=model,
        max_tokens=1000,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": encoded_image,
                        },
                    },
                    {"type": "text", "text": OCR_PROMPT},
                ],
            }
        ],
    )
    return parse_items_json(extract_text_from_message(message))


def parse_items_json(raw_text: str) -> list[str]:
    payload = _load_json_object(raw_text)
    items = payload.get("items")
    if not isinstance(items, list):
        raise OCRParseError("La respuesta OCR no contiene un array 'items'")

    cleaned: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        normalized = re.sub(r"\s+", " ", item).strip()
        if normalized:
            cleaned.append(normalized)
    return cleaned


def extract_text_from_message(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content

    parts: list[str] = []
    for block in content or []:
        if isinstance(block, dict):
            text = block.get("text")
        else:
            text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def _load_json_object(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise OCRParseError("No se encontro JSON en la respuesta OCR") from None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise OCRParseError(f"JSON OCR invalido: {exc}") from exc

    if not isinstance(payload, dict):
        raise OCRParseError("La respuesta OCR debe ser un objeto JSON")
    return payload


def _default_anthropic_client() -> Any:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Falta ANTHROPIC_API_KEY para usar Claude Vision")

    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("Falta instalar la dependencia 'anthropic'") from exc

    return anthropic.Anthropic(api_key=api_key)
