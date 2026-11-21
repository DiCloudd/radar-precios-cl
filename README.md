# 📡 Radar de Precios Chile

Monitor 24/7 de smartphones en **9 tiendas chilenas**, con historial, comparador entre tiendas, detector de ofertas falsas, alertas 🦖 Godzilla y dashboard web público. **Costo: $0.**

| | |
|---|---|
| 🏬 Tiendas | Falabella · Paris · PCFactory · Lider · Abc (La Polar) · Hites · Entel · Samsung Store · Apple Store |
| ⚡ Frecuencia | Página 1 de cada tienda cada ~2 min · todas las páginas cada ~20 min · x2 en CyberDay/Black Friday |
| 🧠 Alertas | bajadas, mínimo histórico *real*, precio objetivo, 🦖 caídas ≥40 % en 24 h, 💥 posibles errores de precio, stock, desaparecidos |
| 🤡 Anti-chanta | detecta precios "normales" inflados y descuentos permanentes |
| 📊 Dashboard | GitHub Pages: ofertas, comparador, gráficos, predicción, salud de tiendas, exportación Excel/CSV |
| ☁️ Hosting | GitHub Actions (ventanas de 12 min cada 15 min, ciclos de ~2 min) + GitHub Pages. Sin tarjeta, sin PC |

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

## Despliegue 24/7 gratis (solo GitHub, sin tarjeta)

- **`.github/workflows/monitor.yml`**: GitHub lanza una *ventana* cada 15 min. Cada ventana dura ~12 min:
  1 ciclo profundo + ciclos rápidos cada ~2 min, publicando el dashboard cada 5 min.
- **Persistencia**: la BD (`data/prices.db.gz`) viaja entre ventanas dentro de la rama `gh-pages`.
- **Secrets** (Settings → Secrets → Actions): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- **Pausar todo**: variable de repo `MONITOR_ON_ACTIONS = false`.
- Límite conocido: desde las IPs de GitHub (EE.UU.) **Paris responde 403**; el monitor lo pausa y reintenta cada vez más espaciado.
  Desde una IP chilena funciona (opciones: correr `run_monitor.bat` en un PC/celular en Chile, o una VM — `deploy/oracle/setup.sh` queda listo por si algún día hay tarjeta de crédito).

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

## 🤖 Bot de Telegram

1. En Telegram, habla con **@BotFather** → `/newbot` → copia el token.
2. GitHub → *Settings → Secrets and variables → Actions → New repository secret*: `TELEGRAM_BOT_TOKEN`.
3. Envía `/start` a tu bot: ese chat queda como dueño (no hace falta `TELEGRAM_CHAT_ID`; el bot ignora a cualquier otro chat).

| Comando | Qué hace |
|---|---|
| `/precio galaxy a57` | precios del más barato al más caro, con botones de gráfico |
| `/top` | mayores bajadas 24 h |
| `/comparar iphone 17 256` | mismo modelo en todas las tiendas |
| `/grafico iphone 17` | gráfico PNG del historial |
| `/objetivo 350000 galaxy a57` · `/objetivos` · `/borrar 1` | precios objetivo |
| `/seguir <link>` · `/siguiendo` · `/dejar 1` | vigilar un producto puntual (stock, desaparece, baja) |
| `/estado` · `/resumen` | salud de tiendas · resumen del día |

Bajo cada alerta: **📊 Historial** · **🎯 Avisar -5 %** · **🔕 24 h**. El bot responde al instante durante cada ventana de GitHub Actions; lo que escribas entre ventanas se contesta al iniciar la siguiente.
