import asyncio
import html
import os
import sys
import requests
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
from typing import Any
import pytz

from smart_order.matcher import ProductMatch, fetch_catalog_sources, load_catalog, match_items
from smart_order.ocr import extract_items_from_image

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
except ImportError:
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None
    Update = Any
    Application = None
    CallbackQueryHandler = None
    CommandHandler = None
    ContextTypes = None
    MessageHandler = None
    filters = None

BOLIVIA_TZ = pytz.timezone("America/La_Paz")
SMART_ORDER_SESSIONS: dict[int, "SmartOrderSession"] = {}
CATALOG_CACHE: list[Any] | None = None

CATEGORIA_EMOJI = {
    "Frutas y Verduras":        "🥬",
    "Granos y Hortalizas":      "🌾",
    "Carnes":                   "🥩",
    "Fiambres":                 "🥓",
    "Lácteos y Derivados":      "🥛",
    "Congelados":               "🧊",
    "Bebidas":                  "🥤",
    "Abarrotes":                "🛒",
    "Panadería":                "🍞",
    "Pastelería y Masas Típicas": "🍰",
    "Aseo Personal":            "🧼",
    "Aseo Del Hogar":           "🧹",
    "Aseo Del Bebé":            "🍼",
    "Cuidado Personal":         "💆",
    "Cuidado del Hogar":        "🏠",
    "Cuidado del Bebé":         "👶",
    "Farmacia Otc":             "💊",
    "Farmacia Éticos":          "💊",
    "Bazar":                    "🛍️",
    "Bazar Importación":        "📦",
    "Juguetería":               "🧸",
    "Juguetería Importación":   "🧸",
}


@dataclass
class SmartOrderSession:
    items: list[str]
    matches: dict[str, list[ProductMatch]]
    selections: dict[int, ProductMatch | None] = field(default_factory=dict)
    build_prompt_sent: bool = False


def send_telegram(token: str, chat_id: str, message: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()


def get_telegram_token() -> str | None:
    return os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")


def authorized_chat(chat_id: int | None) -> bool:
    expected_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not expected_chat_id or chat_id is None:
        return True
    return str(chat_id) == str(expected_chat_id)


async def start_smart_order(update: Update, context: Any) -> None:
    if not update.effective_chat or not authorized_chat(update.effective_chat.id):
        return
    if update.message:
        await update.message.reply_text("Mandame una foto de tu lista de compras y la convierto en un Smart Order.")


async def cancel_smart_order(update: Update, context: Any) -> None:
    if not update.effective_chat or not authorized_chat(update.effective_chat.id):
        return
    SMART_ORDER_SESSIONS.pop(update.effective_chat.id, None)
    if update.message:
        await update.message.reply_text("Smart Order cancelado.")


async def handle_photo(update: Update, context: Any) -> None:
    if not update.effective_chat or not update.message:
        return
    chat_id = update.effective_chat.id
    if not authorized_chat(chat_id):
        await update.message.reply_text("Este chat no esta autorizado para usar este bot.")
        return
    if not update.message.photo:
        return

    status = await update.message.reply_text("Escaneando la lista...")
    try:
        photo = update.message.photo[-1]
        telegram_file = await context.bot.get_file(photo.file_id)
        image_buffer = await telegram_file.download_as_bytearray()
        items = await asyncio.to_thread(extract_items_from_image, bytes(image_buffer), "image/jpeg")
        if not items:
            await status.edit_text("No pude encontrar items claros en la imagen.")
            return

        await status.edit_text(f"Encontre {len(items)} item(s). Buscando matches en Hipermaxi...")
        catalog = await get_catalog_cached()
        matches = await asyncio.to_thread(match_items, items, catalog)
    except Exception as exc:
        await status.edit_text(f"No pude procesar la foto: {str(exc)}")
        return

    session = SmartOrderSession(items=items, matches=matches)
    SMART_ORDER_SESSIONS[chat_id] = session
    await status.edit_text("Listo. Confirmemos producto por producto.")
    for index, item in enumerate(items):
        await send_match_message(context.bot, chat_id, index, item, matches.get(item, []))


async def handle_smart_order_callback(update: Update, context: Any) -> None:
    query = update.callback_query
    if not query or not update.effective_chat:
        return
    chat_id = update.effective_chat.id
    if not authorized_chat(chat_id):
        await query.answer("Chat no autorizado", show_alert=True)
        return

    await query.answer()
    data = (query.data or "").split("|")
    if len(data) < 2 or data[0] != "so":
        return

    action = data[1]
    session = SMART_ORDER_SESSIONS.get(chat_id)
    if action == "cancel":
        SMART_ORDER_SESSIONS.pop(chat_id, None)
        await query.edit_message_text("Smart Order cancelado.")
        return
    if not session:
        await query.edit_message_text("Esta orden ya no esta activa. Mandame otra foto para empezar de nuevo.")
        return

    if action == "build":
        await build_cart_from_session(query, session)
        SMART_ORDER_SESSIONS.pop(chat_id, None)
        return

    if len(data) < 3:
        return
    try:
        index = int(data[2])
        item = session.items[index]
    except (ValueError, IndexError):
        await query.edit_message_text("La seleccion ya no es valida. Mandame otra foto para empezar de nuevo.")
        return
    item_matches = session.matches.get(item, [])

    if action == "confirm":
        await select_match(query, session, index, 0)
    elif action == "select" and len(data) >= 4:
        try:
            match_index = int(data[3])
        except ValueError:
            await query.edit_message_text("La alternativa elegida no es valida.")
            return
        await select_match(query, session, index, match_index)
    elif action == "alternatives":
        await show_alternatives(query, index, item, item_matches)
    elif action == "skip":
        session.selections[index] = None
        await query.edit_message_text(f"Saltado: {html.escape(item)}", parse_mode="HTML")

    await maybe_prompt_build_cart(context.bot, chat_id, session)


async def select_match(query: Any, session: SmartOrderSession, index: int, match_index: int) -> None:
    item = session.items[index]
    item_matches = session.matches.get(item, [])
    if match_index >= len(item_matches):
        await query.edit_message_text("Ese match ya no esta disponible.")
        return

    selected = item_matches[match_index]
    session.selections[index] = selected
    await query.edit_message_text(
        f"Confirmado: {html.escape(item)}\n{format_match(selected)}",
        parse_mode="HTML",
    )


async def show_alternatives(query: Any, index: int, item: str, item_matches: list[ProductMatch]) -> None:
    if not item_matches:
        await query.edit_message_text(f"No encontre alternativas para {html.escape(item)}.", parse_mode="HTML")
        return

    lines = [f"<b>{html.escape(item)}</b>", ""]
    for option_index, match in enumerate(item_matches):
        lines.append(f"{option_index + 1}. {format_match(match)}")

    keyboard = [
        [
            InlineKeyboardButton(
                f"Elegir {option_index + 1}",
                callback_data=f"so|select|{index}|{option_index}",
            )
        ]
        for option_index in range(len(item_matches))
    ]
    keyboard.append([InlineKeyboardButton("Saltar", callback_data=f"so|skip|{index}")])
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def maybe_prompt_build_cart(bot: Any, chat_id: int, session: SmartOrderSession) -> None:
    if session.build_prompt_sent:
        return
    if len(session.selections) < len(session.items):
        return

    session.build_prompt_sent = True
    selected_count = sum(1 for selected in session.selections.values() if selected is not None)
    keyboard = [
        [InlineKeyboardButton("Armar carrito", callback_data="so|build")],
        [InlineKeyboardButton("Cancelar", callback_data="so|cancel")],
    ]
    await bot.send_message(
        chat_id=chat_id,
        text=f"Hay {selected_count} producto(s) confirmados. ¿Armar el carrito?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def build_cart_from_session(query: Any, session: SmartOrderSession) -> None:
    selected_products = [
        selected
        for index, selected in sorted(session.selections.items())
        if selected is not None
    ]
    if not selected_products:
        await query.edit_message_text("No hay productos confirmados para armar el carrito.")
        return

    await query.edit_message_text("Abriendo Hipermaxi y armando el carrito...")
    try:
        from smart_order.cart import build_cart

        is_server = bool(os.environ.get("RENDER_EXTERNAL_URL"))
        default_dir = "/tmp/playwright-profile" if is_server else "playwright-profile"
        user_data_dir = os.environ.get("PLAYWRIGHT_USER_DATA_DIR", default_dir)
        headless = os.environ.get("CART_HEADLESS", "true" if is_server else "false").lower() == "true"
        result = await asyncio.to_thread(build_cart, selected_products, user_data_dir=user_data_dir, headless=headless)
    except Exception as exc:
        await query.message.reply_text(f"No pude armar el carrito: {str(exc)}")
        return

    lines = ["Carrito listo:"]
    if result.cart_url:
        lines.append(result.cart_url)
    if result.total:
        lines.append(f"Total: {result.total}")
    if result.failures:
        lines.append("No agregados: " + ", ".join(result.failures))
    await query.message.reply_text("\n".join(lines))


async def send_match_message(
    bot: Any,
    chat_id: int,
    index: int,
    item: str,
    item_matches: list[ProductMatch],
) -> None:
    if not item_matches:
        keyboard = [[InlineKeyboardButton("Saltar", callback_data=f"so|skip|{index}")]]
        await bot.send_message(
            chat_id=chat_id,
            text=f"No encontre matches para: {html.escape(item)}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    best = item_matches[0]
    keyboard = [
        [InlineKeyboardButton("✅ Confirmar", callback_data=f"so|confirm|{index}")],
        [InlineKeyboardButton("🔄 Ver alternativas", callback_data=f"so|alternatives|{index}")],
        [InlineKeyboardButton("❌ Saltar", callback_data=f"so|skip|{index}")],
    ]
    await bot.send_message(
        chat_id=chat_id,
        text=f"<b>{html.escape(item)}</b>\n{format_match(best)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


def format_match(match: ProductMatch) -> str:
    details = []
    if match.marca:
        details.append(match.marca)
    if match.tamano:
        details.append(match.tamano)
    if match.precio is not None:
        details.append(f"Bs {match.precio:.2f}")
    if match.score:
        details.append(f"{match.score:.0f}%")

    suffix = f" ({html.escape(' · '.join(details))})" if details else ""
    return f"<b>{html.escape(match.nombre)}</b>{suffix}"


async def get_catalog_cached() -> list[Any]:
    global CATALOG_CACHE
    if CATALOG_CACHE is None:
        CATALOG_CACHE = await asyncio.to_thread(load_catalog, "cochabamba")
    return CATALOG_CACHE


def run_smart_order_bot() -> None:
    if Application is None:
        print("ERROR: Falta instalar python-telegram-bot")
        sys.exit(1)

    token = get_telegram_token()
    if not token:
        print("ERROR: Faltan variables TELEGRAM_BOT_TOKEN o TELEGRAM_TOKEN")
        sys.exit(1)

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start_smart_order))
    application.add_handler(CommandHandler("cancel", cancel_smart_order))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(handle_smart_order_callback, pattern=r"^so\|"))

    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    port = int(os.environ.get("PORT", 8443))

    if render_url:
        webhook_url = f"{render_url}/webhook"
        print(f"Smart Order iniciando con webhook: {webhook_url}")
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="webhook",
            webhook_url=webhook_url,
        )
    else:
        print("Smart Order escuchando fotos por polling...")
        application.run_polling()


def main():
    token = get_telegram_token()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("ERROR: Faltan variables TELEGRAM_BOT_TOKEN/TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
        sys.exit(1)

    print("Descargando datos...")
    df, prod_info = fetch_catalog_sources(city="cochabamba")

    # 3. Filtrar bajas de precio
    if "1_cambio" not in df.columns:
        raise ValueError(f"Columna '1_cambio' no encontrada. Columnas: {list(df.columns)}")

    bajas = df[df["1_cambio"] < 0].copy()
    bajas = bajas.sort_values("1_cambio")

    if bajas.empty:
        print("No hubo bajas de precio hoy. No se envía mensaje.")
        return

    # 4. Agrupar por categoría
    por_categoria = defaultdict(list)
    for _, row in bajas.iterrows():
        pid = str(int(row["id_producto"]))
        info = prod_info.get(pid, {"nombre": f"Producto #{pid}", "categoria": "Otros"})
        por_categoria[info["categoria"]].append({
            "nombre": info["nombre"],
            "hoy": row["hoy"],
            "ayer": row["1"],
            "cambio_pct": row["1_cambio"] * 100,
        })

    # 5. Formatear mensaje
    today = datetime.now(BOLIVIA_TZ).strftime("%d/%m/%Y")
    bloques = []
    for cat in sorted(por_categoria.keys()):
        emoji = CATEGORIA_EMOJI.get(cat, "📦")
        header = f"{emoji} <b>{cat}</b>"
        items = []
        for p in por_categoria[cat]:
            items.append(f"  {p['nombre']}  <b>Bs {p['hoy']:.2f}</b>  <i>(antes Bs {p['ayer']:.2f}, {p['cambio_pct']:+.0f}%)</i>")
        bloques.append(header + "\n" + "\n".join(items))

    encabezado = f"📉 <b>Bajas de precio · Cochabamba · {today}</b>"
    pie = f"\n<i>{len(bajas)} producto{'s' if len(bajas) != 1 else ''} bajaron · HiperMaxi</i>"
    mensaje = encabezado + "\n\n" + "\n\n".join(bloques) + pie

    # Límite de Telegram: 4096 caracteres
    if len(mensaje) > 4000:
        bloques_recortados = []
        total = len(encabezado) + len(pie) + 10
        for bloque in bloques:
            if total + len(bloque) > 4000:
                break
            bloques_recortados.append(bloque)
            total += len(bloque) + 2
        mensaje = encabezado + "\n\n" + "\n\n".join(bloques_recortados) + pie

    # 6. Enviar
    print(f"Enviando mensaje con {len(bajas)} productos en {len(por_categoria)} categorías...")
    send_telegram(token, chat_id, mensaje)
    print("Mensaje enviado correctamente.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "smart-order":
        run_smart_order_bot()
    else:
        main()
