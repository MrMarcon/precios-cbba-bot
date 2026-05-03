import os
import sys
import requests
from datetime import datetime
from collections import defaultdict
import pytz

from smart_order.matcher import fetch_catalog_sources

BOLIVIA_TZ = pytz.timezone("America/La_Paz")

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


def send_telegram(token: str, chat_id: str, message: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("ERROR: Faltan variables TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
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
    main()
