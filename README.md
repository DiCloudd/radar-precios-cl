# 📡 Radar de Precios Chile

Monitor 24/7 de smartphones en **9 tiendas chilenas**, con historial, comparador entre tiendas, detector de ofertas falsas, alertas 🦖 Godzilla y dashboard web público. **Costo: $0.**

| | |
|---|---|
| 🏬 Tiendas | Falabella · Paris · PCFactory · Lider · Abc (La Polar) · Hites · Entel · Samsung Store · Apple Store |
| ⚡ Frecuencia | Página 1 de cada tienda cada ~2 min · todas las páginas cada ~20 min · x2 en CyberDay/Black Friday |
| 🧠 Alertas | bajadas, mínimo histórico *real*, precio objetivo, 🦖 caídas ≥40 % en 24 h, 💥 posibles errores de precio, stock, desaparecidos |
| 🤡 Anti-chanta | detecta precios "normales" inflados y descuentos permanentes |
| 📊 Dashboard | GitHub Pages: ofertas, comparador, gráficos, predicción, salud de tiendas, exportación Excel/CSV |
| ☁️ Hosting | VM Oracle Cloud *Always Free* en Santiago (24/7) + GitHub Actions de respaldo |

## Arquitectura

```
sources.toml ─► monitor.py ──► scrapers/<tienda>.py ──► fetchers.py (HTTP liviano · navegador de respaldo)
                    │                                         └─ robots.txt · espaciado por dominio · detección de bloqueo
                    ├─► storage.py   SQLite (historial solo-cambios, ejecuciones, alertas, pausas)
                    ├─► analysis.py  reglas de alerta · 🤡 ofertas falsas · 🔮 predicción
                    ├─► matcher.py   mismo modelo entre tiendas ("samsung galaxy a57 128gb")
                    ├─► notifier.py  Telegram (🦖 con GIF animado)
                    └─► dashboard.py + publisher.py ─► GitHub Pages (data.json, Excel, CSV, respaldo BD)
```

## Uso local

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe main.py --deep --dry-run --build-site   # prueba completa sin enviar nada
.\.venv\Scripts\python.exe -m pytest -q                            # tests
```

Ver todos los comandos: `python main.py --help`.

## Despliegue 24/7 gratis

1. **GitHub** (ya configurado): el repo público guarda el código, corre los tests y publica el dashboard en `https://<usuario>.github.io/<repo>`. El workflow `monitor` hace un escaneo cada ~15 min mientras no exista la VM.
2. **Oracle Cloud Always Free, región Santiago** (`sa-santiago-1`):
   - Crea la cuenta en oracle.com/cloud/free (pide tarjeta solo para verificar identidad; el plan *Always Free* no cobra).
   - Crea una instancia **VM.Standard.A1.Flex** (ARM, 1–4 OCPU, 6–24 GB) con Ubuntu 24.04.
   - Entra por SSH y ejecuta:
     ```bash
     curl -fsSL https://raw.githubusercontent.com/<usuario>/<repo>/main/deploy/oracle/setup.sh | bash -s -- <usuario>/<repo>
     nano ~/price-monitor/.env        # GH_TOKEN, GH_REPO, PUBLIC_SITE_URL (y Telegram después)
     bash ~/price-monitor/deploy/oracle/setup.sh <usuario>/<repo>
     ```
   - En GitHub: *Settings → Secrets and variables → Actions → Variables* → `MONITOR_ON_ACTIONS = false` (la VM toma el control).

## Anti-bloqueo: cómo se evita que te baneen

- **HTTP liviano primero**: lee el JSON que la propia tienda incrusta (Next.js, JSON-LD, Endeca, APIs públicas de su web). 1 petición por página, sin descargar imágenes ni ejecutar JavaScript.
- **Espaciado por dominio** (`DOMAIN_MIN_GAP`) con jitter aleatorio: nunca ráfagas; cada tienda recibe ~1 petición/2 min en modo rápido.
- **robots.txt respetado**: por eso Movistar está desactivada.
- **Si una tienda bloquea, se pausa sola** (15 min → 30 → 60 … máx 6 h) en vez de insistir, y avisa.
- **No se evaden protecciones** (captchas, verificación de cuenta, huellas de navegador). Por eso Ripley y MercadoLibre no están: bloquean explícitamente a clientes automatizados.
- IP chilena (Oracle Santiago) = tráfico que la tienda espera ver.

## Tiendas verificadas (nov-2026)

| Tienda | Fuente de datos | Notas |
|---|---|---|
| Falabella | `__NEXT_DATA__` → `pageProps.results[].prices[]` | respaldos: JSON-LD, DOM `data-*-price` |
| Paris | JSON-LD `ItemList` + tarjetas `data-cnstrc-*` | resetea conexiones al azar → 8 reintentos; solo página 1 |
| PCFactory | API `catalogo/productos/query` (48/página) | categoría completa |
| Lider | `__NEXT_DATA__` → `searchResult.itemStacks` | PerimeterX: a veces desafía → pausa automática |
| Abc / La Polar | SFCC `.price-value[data-value]` (tarjeta/internet/normal) | La Polar redirige a Abc |
| Hites | SFCC `data-gtmselectitem` (precio + tarjeta Hites) | |
| Entel | JSON Endeca `attributes` | incluye cuotas y stock |
| Samsung Store | JSON-LD `ItemList` | |
| Apple Store | tarjetas `rf-hcard` ("Desde $X") | precio base por modelo |
| ~~Movistar~~ | — | robots.txt prohíbe bots |
| ~~Ripley~~, ~~MercadoLibre~~ | — | bloqueo explícito |
