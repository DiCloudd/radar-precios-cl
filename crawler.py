"""🔄 Rastreador de catálogo completo: recorre TODAS las páginas de cada categoría, sin parar y a ritmo tranquilo.

Por qué: las tiendas grandes tienen cientos de páginas (Falabella ~366). Los ciclos rápidos miran la página 1 y las
trampas ⚡/🆕 atrapan errores llamativos; el rastreador garantiza que TODO producto se revise cada cierto tiempo
(un descuento o error en la página 40 también se detecta).

Diseño anti-bloqueo:
  * Un trabajador por dominio (las tiendas avanzan en paralelo, cada una a su propio ritmo).
  * Dentro de un dominio, round-robin entre sus categorías: las chicas se completan seguido y las enormes
    (audífonos de Falabella, 182 páginas) no acaparan.
  * Pausa entre páginas (CRAWL_GAP, con jitter) además del espaciado mínimo por dominio del fetcher.
  * Respeta las pausas por bloqueo del monitor y deja de pedir a esa tienda mientras dure.
  * El avance (página actual) se guarda en la BD: continúa entre ventanas de GitHub Actions.
  * El 1er barrido de cada categoría es silencioso para productos nunca vistos (evita una avalancha).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from urllib.parse import urlsplit

import config
import userprefs
from fetchers import BlockedError, RobotsDisallowed, jittered
from models import ScrapeResult
from scrapers import SCRAPERS
from storage import PriceStore, now_iso

log = logging.getLogger("crawler")

CRAWLABLE = {"falabella", "pcfactory", "abc", "hites", "entel", "lider", "paris"}
TRAP_MARKS = ("⚡", "🆕", "🏷️")
CRAWL_GAP = config._float("CRAWL_GAP", 6)                 # s entre páginas del rastreador, por dominio
MAX_PAGES_PER_SOURCE = int(config._float("CRAWL_MAX_PAGES", 400))


def crawl_sources(sources: list[config.Source]) -> dict[str, list[config.Source]]:
    """Fuentes "normales" (no trampas) de tiendas paginadas, agrupadas por dominio."""
    out: dict[str, list[config.Source]] = {}
    for s in sources:
        if s.site in CRAWLABLE and not any(m in s.name for m in TRAP_MARKS):
            out.setdefault(urlsplit(s.url).netloc, []).append(s)
    return out


class Crawler:
    def __init__(self, monitor) -> None:
        self.mon = monitor
        self.pages_done = 0
        self.products_seen = 0

    async def run(self, stop: asyncio.Event) -> None:
        groups = crawl_sources(config.SOURCES)
        self.mon.crawling = True
        log.info("🔄 Rastreador: %d fuentes en %d tiendas", sum(map(len, groups.values())), len(groups))
        await asyncio.gather(*(self._worker(dom, srcs, stop) for dom, srcs in groups.items()))

    async def _worker(self, domain: str, sources: list[config.Source], stop: asyncio.Event) -> None:
        while not stop.is_set():
            progressed = False
            for src in sources:
                if stop.is_set():
                    return
                while getattr(self.mon, "cycle_busy", False) and not stop.is_set():   # ⚡ ciclos rápidos primero
                    await asyncio.sleep(1)
                with PriceStore(config.DB_PATH) as store:
                    if self.mon._paused(store, src):
                        continue
                await self._step(src)
                progressed = True
                try:
                    await asyncio.wait_for(stop.wait(), timeout=jittered(CRAWL_GAP))
                    return
                except asyncio.TimeoutError:
                    pass
            if not progressed:                          # toda la tienda en pausa por bloqueo
                try:
                    await asyncio.wait_for(stop.wait(), timeout=60)
                    return
                except asyncio.TimeoutError:
                    pass

    async def _step(self, src: config.Source) -> None:
        """Lee la siguiente página de la fuente, procesa y avanza el cursor."""
        scraper = SCRAPERS[src.site]()
        with PriceStore(config.DB_PATH) as store:
            cur = store.get_meta(f"crawl:{src.name}") or {"page": 1, "started": now_iso(), "prev_keys": []}
        page = cur["page"]
        url = scraper.page_url(src.url, page)
        if url is None:
            return
        t0 = time.monotonic()
        try:
            res = await scraper.fetch(self.mon.ctx, url)
        except BlockedError as exc:
            with PriceStore(config.DB_PATH) as store:
                alert = self.mon._update_breaker(store, ScrapeResult(src, error=str(exc), blocked=True), now_iso())
                if alert:
                    await self.mon.dispatch(store, [alert], now_iso())
            return
        except RobotsDisallowed as exc:
            log.info("[crawl] %s: %s", src.name, exc)
            return
        except Exception as exc:  # noqa: BLE001 - una página rota no detiene el barrido
            log.info("[crawl] %s pág %d falló: %s", src.name, page, str(exc)[:120])
            res = None

        products = res.products if res else []
        for p in products:
            p.found_at = url
        keys = [p.key for p in products]
        # ¿la tienda repitió la página anterior? (fin del catálogo). Se compara la página COMPLETA: Falabella pone
        # productos patrocinados arriba de todas las páginas, así que el primero puede repetirse sin ser el final.
        prev = set(cur.get("prev_keys") or [])
        repeated = page > 1 and bool(keys) and len(prev & set(keys)) >= 0.8 * len(keys)
        last_page = (not products and res is not None) or repeated \
            or (res is not None and res.total_pages and page >= res.total_pages) or page >= MAX_PAGES_PER_SOURCE
        ts = now_iso()
        with PriceStore(config.DB_PATH) as store:
            sweep = store.get_meta(f"sweep:{src.name}")
            targets = userprefs.all_targets(store)
            # 1er barrido completo: los productos nunca vistos solo se registran (sin alertas)
            alerts = self.mon.ingest(store, src, products, ts, warming=False, targets=targets,
                                     quiet_new=sweep is None) if products and not repeated else []
            store.commit()
            if last_page:
                started = datetime.fromisoformat(cur.get("started") or ts)
                minutes = (datetime.now() - started).total_seconds() / 60
                store.set_meta(f"sweep:{src.name}", {"done": ts, "pages": page if products else page - 1,
                                                     "minutes": round(minutes, 1)})
                store.set_meta(f"crawl:{src.name}", {"page": 1, "started": ts, "prev_keys": []})
                log.info("🔄 Barrido completo %s: %d páginas en %.0f min", src.name, page, minutes)
            else:
                store.set_meta(f"crawl:{src.name}", {"page": page + 1, "started": cur.get("started") or ts,
                                                     "prev_keys": keys})
            if alerts:
                await self.mon.dispatch(store, alerts, ts)
        self.pages_done += 1
        self.products_seen += len(products)
        log.debug("[crawl] %s pág %d: %d productos (%.1fs)", src.name, page, len(products), time.monotonic() - t0)
