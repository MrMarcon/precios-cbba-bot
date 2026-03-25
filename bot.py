import os
import re
import sys
import requests
import pandas as pd
from datetime import datetime
from collections import defaultdict
import pytz

BASE_URL = "https://mauforonda.github.io/precios"
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


def get_hashed_urls(html: str) -> tuple[str, str]:
    cbba_match = re.search(r'_file/data/cochabamba\.([a-f0-9]+)\.csv', html)
    prod_match = re.search(r'_file/data/productos\.([a-f0-9]+)\.json', html)
    if not cbba_match or not prod_match:
        raise ValueError("No se encontraron las URLs hasheadas en el HTML del sitio")
    cbba_url = f"{BASE_URL}/_file/data/cochabamba.{cbba_match.group(1)}.csv"
    prod_url = f"{BASE_URL}/_file/data/productos.{prod_match.group(1)}.json"
    return cbba_url, prod_url


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

    # 1. Obtener URLs hasheadas del HTML
    print("Obteniendo URLs del sitio...")
    html_resp = requests.get(BASE_URL + "/", timeout=15)
    html_resp.raise_for_status()
    cbba_url, prod_url = get_hashed_urls(html_resp.text)

    # 2. Descargar datos
    print("Descargando datos...")
    df = pd.read_csv(cbba_url)
    productos = requests.get(prod_url, timeout=15).json()

    # Mapeo id → {nombre, categoria}
    if isinstance(productos, list):
        prod_info = {
            str(p["id_producto"]): {"nombre": p.get("producto", "?"), "categoria": p.get("categoria", "Otros")}
            for p in productos if "id_producto" in p
        }
    else:
        prod_info = {
            str(k): {"nombre": v.get("producto", "?") if isinstance(v, dict) else v,
                     "categoria": v.get("categoria", "Otros") if isinstance(v, dict) else "Otros"}
            for k, v in productos.items()
        }

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
            items.append(f"  {p['nombre']}  <b>Bs {p['hoy']:.2f}</b>  <i>({p['cambio_pct']:+.0f}%)</i>")
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
