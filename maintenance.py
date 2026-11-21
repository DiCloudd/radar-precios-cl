"""🛡️ Protecciones para que el radar siga funcionando GRATIS a medida que crece.

Límites del plan gratis (nov-2026) y cómo se respetan:
  * GitHub Pages: límite blando de 10 builds/hora -> máx. MAX_BUILDS_PER_HOUR publicaciones; la publicación final de
    cada ventana (la que guarda la BD) siempre tiene un cupo reservado.
  * GitHub: archivos de máx. 100 MB -> aviso si la BD comprimida o el catálogo pasan SIZE_WARN_MB.
  * Base de datos: limpieza diaria (ejecuciones viejas, alertas viejas, productos que ya no existen) + VACUUM semanal.
  * Tiendas: presupuesto de peticiones por minuto por dominio (el límite real para no ser bloqueado).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import config
from storage import PriceStore, ago

log = logging.getLogger("maintenance")

MAX_BUILDS_PER_HOUR = int(config._float("MAX_BUILDS_PER_HOUR", 9))   # GitHub Pages: límite blando 10/h
SIZE_WARN_MB = config._float("SIZE_WARN_MB", 50)                       # GitHub rechaza archivos > 100 MB
DATA_WARN_MB = config._float("DATA_WARN_MB", 30)
RUNS_KEEP_DAYS, ALERTS_KEEP_DAYS, PRODUCT_STALE_DAYS = 30, 365, 60
REQ_PER_MIN_WARN = config._float("REQ_PER_MIN_WARN", 15)              # por tienda (ritmo actual máx. ≈ 11/min)


# ---------------------------------------------------------------- 1. builds de GitHub Pages
def publish_allowed(store: PriceStore, essential: bool) -> bool:
    """La publicación periódica se salta si ya hubo demasiadas en la última hora. La esencial (fin de ventana,
    guarda la BD entre ventanas) siempre pasa: por eso las periódicas dejan 1 cupo libre."""
    recent = [t for t in store.get_meta("pub:times", []) if t > time.time() - 3600]
    return essential or len(recent) < MAX_BUILDS_PER_HOUR - 1


def record_publish(store: PriceStore) -> int:
    recent = [t for t in store.get_meta("pub:times", []) if t > time.time() - 3600] + [time.time()]
    store.set_meta("pub:times", recent)
    return len(recent)


# ---------------------------------------------------------------- 2. limpieza diaria
def daily_cleanup(store: PriceStore, force: bool = False) -> dict | None:
    today = datetime.now().strftime("%Y-%m-%d")
    if not force and store.get_meta("maint:last") == today:
        return None
    c = store.conn
    stats = {
        "runs": c.execute("DELETE FROM runs WHERE ts<?", (ago(days=RUNS_KEEP_DAYS),)).rowcount,
        "alerts": c.execute("DELETE FROM alerts WHERE ts<?", (ago(days=ALERTS_KEEP_DAYS),)).rowcount,
    }
    stale = [r[0] for r in c.execute("SELECT key FROM products WHERE last_seen<?", (ago(days=PRODUCT_STALE_DAYS),))]
    for i in range(0, len(stale), 500):                # productos que ya no existen en ninguna tienda
        chunk = stale[i:i + 500]
        marks = ",".join("?" * len(chunk))
        c.execute(f"DELETE FROM prices WHERE key IN ({marks})", chunk)
        c.execute(f"DELETE FROM products WHERE key IN ({marks})", chunk)
    stats["products"] = len(stale)
    store.commit()
    if store.get_meta("vacuum:last", 0) < time.time() - 7 * 86400:     # VACUUM semanal: devuelve el espacio
        c.execute("VACUUM")
        store.set_meta("vacuum:last", time.time())
        stats["vacuum"] = True
    store.set_meta("maint:last", today)
    log.info("🧹 Limpieza diaria: %s", stats)
    return stats


# ---------------------------------------------------------------- 3. tamaños
def sizes(site_dir: Path = config.SITE_DIR) -> dict[str, float]:
    def mb(p: Path) -> float:
        return round(p.stat().st_size / 1e6, 2) if p.exists() else 0.0
    backup = site_dir / "data" / ("prices.db.enc" if (site_dir / "data" / "prices.db.enc").exists() else "prices.db.gz")
    return {"db_gz": mb(backup), "data_json": mb(site_dir / "data.json"),
            "products_json": mb(site_dir / "products.json"), "db": mb(config.DB_PATH)}


def size_warnings(s: dict[str, float], builds_last_hour: int | None = None) -> list[str]:
    out = []
    if s["db_gz"] > SIZE_WARN_MB:
        out.append(f"La base comprimida pesa {s['db_gz']} MB (GitHub rechaza archivos > 100 MB)")
    if max(s["data_json"], s["products_json"]) > DATA_WARN_MB:
        out.append(f"El catálogo de la app pesa {max(s['data_json'], s['products_json'])} MB (lento en el iPhone)")
    if builds_last_hour is not None and builds_last_hour > 10:
        out.append(f"{builds_last_hour} publicaciones en la última hora (GitHub Pages recomienda ≤ 10)")
    return out


# ---------------------------------------------------------------- 4. presupuesto de peticiones por tienda
def request_budget(sources: list[config.Source] | None = None) -> dict[str, float]:
    """Peticiones/minuto estimadas por dominio = ciclos rápidos (rotación incluida) + rastreador."""
    from crawler import CRAWL_GAP, crawl_sources
    sources = sources or config.SOURCES
    per_min: dict[str, float] = {}
    cycles_per_min = 60 / config.FAST_INTERVAL
    for s in sources:
        if s.fast:
            dom = urlsplit(s.url).netloc
            per_min[dom] = per_min.get(dom, 0) + cycles_per_min / s.fast_every
    if config.CRAWL:
        for dom in crawl_sources(sources):
            per_min[dom] = per_min.get(dom, 0) + 60 / (CRAWL_GAP + config.DOMAIN_MIN_GAP)
    return {d: round(v, 1) for d, v in sorted(per_min.items(), key=lambda kv: -kv[1])}


def budget_warnings(budget: dict[str, float]) -> list[str]:
    return [f"{d}: ~{v} peticiones/min (límite sano {REQ_PER_MIN_WARN:.0f})" for d, v in budget.items()
            if v > REQ_PER_MIN_WARN]


# ---------------------------------------------------------------- avisos (1 vez al día por tipo)
def notify_once_a_day(store: PriceStore, kind: str, lines: list[str]) -> None:
    if not lines:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    if store.get_meta(f"guard:{kind}") == today:
        return
    from notifier import TelegramNotifier
    TelegramNotifier().send("🛡️ <b>Radar: límite del plan gratis</b>\n" + "\n".join(f"• {x}" for x in lines))
    store.set_meta(f"guard:{kind}", today)
