"""✅ Verificación instantánea y 🎯 modo caza para errores de precio.

Verificación (antes de avisar), unos segundos después de detectar el error:
  1. Segunda lectura del MISMO listado donde apareció (todas las tiendas).
  2. Ficha del producto (JSON-LD) en las tiendas que la entregan a la nube: precio + stock.
  Veredictos: confirmed · unconfirmed (se avisa con advertencia) · corrected (la tienda ya lo arregló: no se avisa).

Modo caza (después de avisar): relee el producto cada ~30 s durante 10 min y avisa si lo corrigen,
se agota, lo retiran o sigue activo. Todo queda en la tabla `errors` (🏆 salón de la fama).
"""
from __future__ import annotations

import asyncio
import html
import logging
import random
import time
from dataclasses import dataclass

import config
from analysis import clp
from fetchers import BlockedError, RobotsDisallowed, jittered
from models import Product
from scrapers import SCRAPERS, SITE_LABELS, FetchContext
from scrapers.product_page import ProductPageScraper
from storage import PriceStore, now_iso

log = logging.getLogger("verifier")

# Tiendas cuya ficha de producto entrega JSON-LD a las IPs de la nube (verificado nov-2026).
# Falabella bloquea fichas (403), PCFactory es SPA, Entel responde 500, Abc no trae datos estructurados.
PDP_SITES = {"apple", "hites", "maconline", "motorola", "multicentro", "samsung", "xiaomi"}
HUNT_MINUTES = float(getattr(config, "HUNT_MINUTES", 10))
HUNT_EVERY = 30.0
MAX_HUNTS = 3


@dataclass
class Reading:
    where: str                 # "listado" | "ficha"
    found: bool
    price: int | None = None
    in_stock: bool | None = None


@dataclass
class Verdict:
    status: str                # confirmed | unconfirmed | corrected
    note: str
    price: int | None = None
    in_stock: bool | None = None

    @property
    def line(self) -> str:
        icon = {"confirmed": "✅", "unconfirmed": "⚠️", "corrected": "🛑"}[self.status]
        return f"{icon} <b>{self.note}</b>"


def close(a: int | None, b: int) -> bool:
    return a is not None and abs(a - b) <= max(1000, 0.02 * b)


class Verifier:
    def __init__(self, ctx: FetchContext) -> None:
        self.ctx = ctx

    async def read_listing(self, p: Product) -> Reading:
        scraper_cls = SCRAPERS.get(p.site)
        if not p.found_at or scraper_cls is None or p.site == "maconline":   # MacOnline: 1 petición por modelo -> ficha
            return Reading("listado", False)
        try:
            res = await scraper_cls().fetch(self.ctx, p.found_at)
        except (BlockedError, RobotsDisallowed, Exception) as exc:  # noqa: BLE001 - nunca romper el ciclo
            log.info("Relectura de listado falló (%s): %s", p.site, str(exc)[:100])
            return Reading("listado", False)
        hit = next((x for x in res.products if x.key == p.key), None)
        return Reading("listado", hit is not None, hit.price if hit else None, hit.in_stock if hit else None)

    async def read_pdp(self, p: Product) -> Reading | None:
        if p.site not in PDP_SITES or not p.url.startswith("http"):
            return None
        try:
            text = await self.ctx.http.get(p.url, retries=2)
        except Exception as exc:  # noqa: BLE001
            log.info("Ficha de %s no disponible: %s", p.site, str(exc)[:100])
            return Reading("ficha", False)
        res = ProductPageScraper().parse(text, p.url)
        if not res.products:
            return Reading("ficha", False)
        q = res.products[0]
        return Reading("ficha", True, q.price, q.in_stock)

    async def read(self, p: Product) -> Reading:
        """Lectura más barata para el modo caza: ficha si la tienda la entrega, si no el listado."""
        pdp = await self.read_pdp(p)
        return pdp if pdp and pdp.found else await self.read_listing(p)

    async def verify(self, p: Product, delay: float | None = None) -> Verdict:
        if ":st" in p.key:                       # 📚 vía SoloTodo: no podemos releer esa tienda desde la nube
            return Verdict("unconfirmed", f"Visto vía SoloTodo ({p.stock_text or 'actualiza ~cada hora'}): "
                                          "revisa en la tienda, puede haber cambiado")
        await asyncio.sleep(random.uniform(12, 20) if delay is None else delay)   # deja pasar un "parpadeo"
        lst, pdp = await asyncio.gather(self.read_listing(p), self.read_pdp(p))
        # ¿ya lo corrigieron?
        for r in (lst, pdp):
            if r and r.found and r.price and r.price > p.price * 1.15:
                if r is pdp and lst.found and close(lst.price, p.price):
                    return Verdict("unconfirmed", f"El listado sigue en {clp(p.price)}, pero la ficha dice {clp(r.price)}: "
                                                  "puede no respetarse al pagar", r.price, r.in_stock)
                return Verdict("corrected", f"La tienda lo corrigió en segundos ({clp(r.price)})", r.price, r.in_stock)
        if pdp and pdp.found and pdp.in_stock is False:
            return Verdict("unconfirmed", "Precio real, pero la ficha lo muestra SIN STOCK", pdp.price, False)
        listing_ok = lst.found and close(lst.price, p.price)
        pdp_ok = bool(pdp and pdp.found and close(pdp.price, p.price))
        if listing_ok and pdp_ok:
            return Verdict("confirmed", "CONFIRMADO: listado y ficha del producto (con stock)", p.price, pdp.in_stock)
        if listing_ok:
            return Verdict("confirmed", "CONFIRMADO: el precio sigue igual en una 2ª lectura", p.price, lst.in_stock)
        if pdp_ok:
            return Verdict("confirmed", "CONFIRMADO en la ficha del producto", p.price, pdp.in_stock)
        return Verdict("unconfirmed", "Sin confirmar: no pude releer el producto (revisa rápido)")

    # ------------------------------------------------------------------ 🎯 modo caza
    async def hunt(self, p: Product, error_id: int, ref_price: int | None, notify,
                   minutes: float = HUNT_MINUTES, every: float = HUNT_EVERY) -> str:
        started = time.monotonic()
        misses = 0
        label = f"<b>{html.escape(p.name[:70])}</b> ({SITE_LABELS.get(p.site, p.site)})"
        outcome, last = "seguía activo", p.price
        try:
            while time.monotonic() - started < minutes * 60:
                await asyncio.sleep(jittered(every))
                r = await self.read(p)
                mins = (time.monotonic() - started) / 60
                if not r.found:
                    misses += 1
                    if misses >= 3:
                        outcome = "retirado"
                        notify(f"👻 {label}\nLo retiraron de la tienda tras {mins:.0f} min.")
                        break
                    continue
                misses, last = 0, r.price
                if r.price and (r.price > p.price * 1.3 or (ref_price and r.price >= ref_price * 0.9)):
                    outcome = "corregido"
                    notify(f"🛑 {label}\nCorrigieron el precio tras <b>{mins:.0f} min</b>: ahora {clp(r.price)} "
                           f"(el error era {clp(p.price)}).")
                    break
                if r.in_stock is False:
                    outcome = "agotado"
                    notify(f"⛔ {label}\nSe agotó tras <b>{mins:.0f} min</b> a {clp(p.price)}.")
                    break
            else:
                notify(f"⏰ {label}\n¡Sigue activo tras {minutes:.0f} min a <b>{clp(p.price)}</b>! Aún alcanzas.")
        except asyncio.CancelledError:
            outcome = "seguía activo (fin de ventana)"
            raise
        finally:
            with PriceStore(config.DB_PATH) as store:
                store.close_error(error_id, outcome, last, now_iso(), (time.monotonic() - started) / 60)
            log.info("🎯 Caza terminada (%s): %s", p.key, outcome)
        return outcome
