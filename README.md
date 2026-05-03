# Precios CBBA Bot

Bot de Telegram para alertas diarias de bajas de precio de Hipermaxi y flujo interactivo Smart Order.

## Ejecución

- Alertas diarias existentes: `python bot.py`
- Smart Order por polling: `python bot.py smart-order`
- Tests: `python -m pytest test_smart_order.py`
- Smoke real de carrito: `RUN_CART_SMOKE=1 python -m pytest test_smart_order.py -k cart_smoke -q`

## Variables

- `TELEGRAM_BOT_TOKEN` o `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `ANTHROPIC_API_KEY`
- `ANTHROPIC_MODEL`, opcional; default `claude-sonnet-4-6`
- `HIPERMAXI_URL`, opcional; default `https://www.hipermaxi.com/`
- `PLAYWRIGHT_USER_DATA_DIR`, opcional; default `playwright-profile`

Antes de usar el carrito en una máquina nueva, instalar el navegador de Playwright:

```bash
python -m playwright install chromium
```

Si Hipermaxi muestra CAPTCHA o login, resolverlo manualmente en la ventana que abre Playwright. La sesión queda guardada en el perfil persistente.
