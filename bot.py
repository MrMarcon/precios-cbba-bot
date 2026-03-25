import os
import re
import sys
import requests
import pandas as pd
from datetime import datetime
import pytz

BASE_URL = "https://mauforonda.github.io/precios"
BOLIVIA_TZ = pytz.timezone("America/La_Paz")


def get_hashed_urls(html: str) -> tuple[str, str]:
    cbba_match = re.search(r'registerFile\("./data/cochabamba\.csv",\s*"(\._file/data/cochabamba\.[a-f0-9]+\.csv)"', html)
    prod_match = re.search(r'registerFile\("./data/productos\.json",\s*"(\._file/data/productos\.[a-f0-9]+\.json)"', html)

    if not cbba_match:
        # Try alternate pattern without leading dot
        cbba_match = re.search(r'_file/data/cochabamba\.([a-f0-9]+)\.csv', html)
        prod_match = re.search(r'_file/data/productos\.([a-f0-9]+)\.json', html)
        if not cbba_match or not prod_match:
            raise ValueError("No se encontraron las URLs hasheadas en el HTML del sitio")
        cbba_url = f"{BASE_URL}/_file/data/cochabamba.{cbba_match.group(1)}.csv"
        prod_url = f"{BASE_URL}/_file/data/productos.{prod_match.group(1)}.json"
    else:
        cbba_url = BASE_URL + cbba_match.group(1).lstrip(".")
        prod_url = BASE_URL + prod_match.group(1).lstrip(".")

    return cbba_url, prod_url


def send_telegram(token: str, chat_id: str, message: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("ERROR: Faltan variables TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
        sys.exit(1)

    # 1. Obtener HTML y extraer URLs hasheadas
    print("Obteniendo URLs del sitio...")
    html_resp = requests.get(BASE_URL + "/", timeout=15)
    html_resp.raise_for_status()
    cbba_url, prod_url = get_hashed_urls(html_resp.text)
    print(f"CSV Cochabamba: {cbba_url}")
    print(f"JSON productos: {prod_url}")

    # 2. Descargar datos
    print("Descargando datos...")
    df = pd.read_csv(cbba_url)
    productos = requests.get(prod_url, timeout=15).json()

    # productos es un dict {id: {"producto": "...", "categoria": "...", ...}}
    if isinstance(productos, list):
        prod_map = {str(p["id_producto"]): p["producto"] for p in productos if "id_producto" in p}
    else:
        prod_map = {str(k): v["producto"] if isinstance(v, dict) else v for k, v in productos.items()}

    # 3. Filtrar productos con baja de precio (1_cambio < 0)
    if "1_cambio" not in df.columns:
        print(f"Columnas disponibles: {list(df.columns)}")
        raise ValueError("Columna '1_cambio' no encontrada en el CSV")

    bajas = df[df["1_cambio"] < 0].copy()
    bajas = bajas.sort_values("1_cambio")  # mayor baja primero

    if bajas.empty:
        print("No hubo bajas de precio hoy. No se envía mensaje.")
        return

    # 4. Formatear mensaje
    today = datetime.now(BOLIVIA_TZ).strftime("%d/%m/%Y")
    lineas = []
    for _, row in bajas.iterrows():
        nombre = prod_map.get(str(int(row["id_producto"])), f"Producto #{int(row['id_producto'])}")
        precio_hoy = row["hoy"]
        precio_ayer = row["1"]
        cambio_pct = row["1_cambio"] * 100  # viene como fracción (ej. -0.12 = -12%)

        lineas.append(
            f"🔻 <b>{nombre}</b>  Bs {precio_hoy:.2f} "
            f"<i>(ayer Bs {precio_ayer:.2f}, {cambio_pct:+.1f}%)</i>"
        )

    encabezado = f"📉 <b>Bajas de precio · Cochabamba · {today}</b>\n"
    pie = f"\n<i>{len(bajas)} producto{'s' if len(bajas) != 1 else ''} bajaron · HiperMaxi</i>"
    mensaje = encabezado + "\n" + "\n".join(lineas) + pie

    # Telegram tiene límite de 4096 caracteres
    if len(mensaje) > 4000:
        lineas = lineas[:40]
        pie = f"\n<i>{len(bajas)} productos bajaron (mostrando 40) · HiperMaxi</i>"
        mensaje = encabezado + "\n" + "\n".join(lineas) + pie

    # 5. Enviar
    print(f"Enviando mensaje con {len(bajas)} productos...")
    send_telegram(token, chat_id, mensaje)
    print("Mensaje enviado correctamente.")


if __name__ == "__main__":
    main()
