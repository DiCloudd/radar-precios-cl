"""Configuración central: .env + sources.toml."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


# ---------------------------------------------------------------- rutas
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "prices.db"
LOG_PATH = DATA_DIR / "monitor.log"
SITE_DIR = DATA_DIR / "site"                     # dashboard generado
SOURCES_FILE = Path(os.getenv("SOURCES_FILE", BASE_DIR / "sources.toml"))

# ---------------------------------------------------------------- Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
NOTIFY_MODE = os.getenv("NOTIFY_MODE", "changes").strip().lower()   # changes | always
NOTIFY_ON_RISE = _bool("NOTIFY_ON_RISE", False)
MIN_CHANGE_PCT = _float("MIN_CHANGE_PCT", 3)          # ignora variaciones menores (ruido)
# 🔕 Telegram sin ruido: una bajada normal solo se avisa si es >= 10 %, ahorra >= $10.000 y el producto vale $50.000+
# (los productos que sigues o con objetivo usan MIN_CHANGE_PCT; errores, Godzilla y gangas no tienen umbral)
DROP_ALERT_PCT = _float("DROP_ALERT_PCT", 10)
DROP_ALERT_MIN_PRICE = _float("DROP_ALERT_MIN_PRICE", 50_000)
DROP_ALERT_MIN_SAVING = _float("DROP_ALERT_MIN_SAVING", 10_000)
# 🔥 Ganga: precio <= X veces lo que cuesta el modelo NUEVO en el catálogo (mediana)
GANGA_NEW_RATIO = _float("GANGA_NEW_RATIO", 0.50)
GANGA_REFURB_RATIO = _float("GANGA_REFURB_RATIO", 0.45)    # seminuevo a menos de la mitad del nuevo
GANGA_MIN_REF = _float("GANGA_MIN_REF", 150_000)
DAILY_SUMMARY_HOUR = int(os.getenv("DAILY_SUMMARY_HOUR", "9"))      # hora Chile
TIMEZONE = os.getenv("TZ_NAME", "America/Santiago")

# ---------------------------------------------------------------- análisis
MIN_HISTORY_HOURS = _float("MIN_HISTORY_HOURS", 72)   # antes de esto no hay "mínimo histórico"
GODZILLA_DROP_PCT = _float("GODZILLA_DROP_PCT", 40)   # caída vs máximo 24 h
GODZILLA_MIN_PEAK = _float("GODZILLA_MIN_PEAK", 50000) # 🦖 solo productos que valían ≥ $50.000 (no accesorios)
ERROR_DISCOUNT_PCT = _float("ERROR_DISCOUNT_PCT", 70)  # 💥 descuento sobre el precio normal que huele a error
ERROR_MIN_LIST = _float("ERROR_MIN_LIST", 150000)      # ...solo si el precio normal era alto (evita accesorios)
ERROR_EXTREME_PCT = _float("ERROR_EXTREME_PCT", 80)     # 💥 producto NUEVO con -80 % o más…
ERROR_EXTREME_LIST = _float("ERROR_EXTREME_LIST", 400000)  # …sobre un precio normal ≥ $400.000
PRICE_ERROR_RATIO = _float("PRICE_ERROR_RATIO", 0.45) # precio < 45% de la mediana entre tiendas
FAMILY_ERROR_MIN_REF = _float("FAMILY_ERROR_MIN_REF", 100_000)   # bajo esto la regla "vs su familia" no aplica
FAKE_LIST_RAISE_PCT = _float("FAKE_LIST_RAISE_PCT", 10)
MISSING_HOURS = _float("MISSING_HOURS", 6)
ALERT_COOLDOWN_HOURS = _float("ALERT_COOLDOWN_HOURS", 12)

# ---------------------------------------------------------------- ritmo y anti-bloqueo
FAST_INTERVAL = _float("FAST_INTERVAL", 120)          # s entre ciclos rápidos (página 1 de cada tienda)
DEEP_INTERVAL = _float("DEEP_INTERVAL", 1200)         # s entre escaneos profundos (todas las páginas)
EVENT_SPEEDUP = _float("EVENT_SPEEDUP", 2)            # durante CyberDay/Black Friday todo va 2x
PUBLISH_INTERVAL = _float("PUBLISH_INTERVAL", 600)    # s entre publicaciones del dashboard
DOMAIN_MIN_GAP = _float("DOMAIN_MIN_GAP", 4)          # s mínimos entre 2 peticiones al mismo dominio
JITTER = _float("JITTER", 0.35)                       # ±35% aleatorio en todas las esperas
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "4"))
HTTP_TIMEOUT = _float("HTTP_TIMEOUT", 30)
PAGE_TIMEOUT_MS = int(os.getenv("PAGE_TIMEOUT_MS", "60000"))
RESPECT_ROBOTS = _bool("RESPECT_ROBOTS", True)
CRAWL = _bool("CRAWL", True)                          # 🔄 rastreador de catálogo completo (modo 24/7)
BROWSER_FALLBACK = _bool("BROWSER_FALLBACK", True)    # usar Playwright si falla la vía HTTP
HEADLESS = _bool("HEADLESS", True)
PROXY_URL = os.getenv("PROXY_URL", "").strip() or None
CONCURRENCY = int(os.getenv("CONCURRENCY", "6"))      # tiendas en paralelo
BLOCK_BASE_PAUSE = _float("BLOCK_BASE_PAUSE", 900)    # 15 min de pausa al primer bloqueo (x2 cada vez)
BLOCK_MAX_PAUSE = _float("BLOCK_MAX_PAUSE", 6 * 3600)

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

# ---------------------------------------------------------------- publicación (GitHub Pages)
GH_TOKEN = os.getenv("GH_TOKEN", "").strip()
GH_REPO = os.getenv("GH_REPO", "").strip()            # usuario/repositorio
PUBLIC_SITE_URL = os.getenv("PUBLIC_SITE_URL", "").strip().rstrip("/")
RUNNER_NAME = os.getenv("MONITOR_RUNNER", "local")   # (RUNNER_NAME a secas lo pisa GitHub Actions)


# ---------------------------------------------------------------- fuentes
@dataclass(frozen=True)
class Source:
    name: str
    url: str
    site: str
    max_pages: int = 1
    enabled: bool = True
    fast: bool = True        # False = solo en escaneos profundos (tiendas que piden varias peticiones)
    category: str = "celulares"   # ver CATEGORY_LABELS
    fast_every: int = 1      # en el ciclo rápido, revisar 1 de cada N ciclos (reparte la carga entre tiendas)
    fast_offset: int = 0     # desfase dentro de esos N ciclos (para no juntar todas las fuentes rotativas)


@dataclass(frozen=True)
class Watch:
    name: str
    url: str
    target_price: int | None = None


@dataclass(frozen=True)
class Target:
    label: str
    target_price: int
    keywords: tuple[str, ...] = field(default_factory=tuple)
    product_key: str | None = None
    site: str | None = None

    def matches(self, key: str, name: str, site: str) -> bool:
        if self.site and self.site != site:
            return False
        if self.product_key:
            return self.product_key == key
        low = name.lower()
        return bool(self.keywords) and all(k.lower() in low for k in self.keywords)


def _load() -> tuple[list[Source], list[Watch], list[Target]]:
    data = tomllib.loads(SOURCES_FILE.read_text(encoding="utf-8")) if SOURCES_FILE.exists() else {}
    override = int(os.getenv("MAX_PAGES", "0") or 0)
    sources = [
        Source(s["name"], s["url"], s["site"], override or int(s.get("max_pages", 1)), bool(s.get("enabled", True)),
               bool(s.get("fast", True)), s.get("category", "celulares"), max(1, int(s.get("fast_every", 1))),
               int(s.get("fast_offset", 0)))
        for s in data.get("source", [])
    ]
    watches = [Watch(w["name"], w["url"], w.get("target_price")) for w in data.get("watch", [])]
    targets = [
        Target(t["label"], int(t["target_price"]), tuple(t.get("keywords", [])), t.get("product_key"), t.get("site"))
        for t in data.get("target", [])
    ]
    for w in watches:
        if w.target_price:
            targets.append(Target(f"{w.name} bajo objetivo", int(w.target_price), product_key=f"url:{w.url}"))
    return [s for s in sources if s.enabled], watches, targets


SOURCES, WATCHES, TARGETS = _load()

CATEGORY_LABELS = {"celulares": "📱 Celulares", "notebooks": "💻 Notebooks", "televisores": "📺 Televisores",
                   "consolas": "🎮 Consolas", "tablets": "📲 Tablets", "audifonos": "🎧 Audífonos",
                   "smartwatches": "⌚ Smartwatches", "electro": "🧊 Electro", "monitores": "🖥️ Monitores",
                   "camaras": "📷 Cámaras y drones", "impresion3d": "🖨️ Impresoras 3D", "movilidad": "🛴 Movilidad"}
