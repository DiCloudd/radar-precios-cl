"""🔥 Gangas y 🚨 vigilancia de reposición.

Una GANGA es un producto muy por debajo de lo que cuesta ese modelo NUEVO en el catálogo (analysis.bargain),
por ejemplo un Xiaomi 14 Ultra seminuevo a $299.990 cuando el nuevo cuesta ~$800.000.

  * Ganga CON stock   -> 🔥 aviso inmediato con foto.
  * Ganga SIN stock   -> entra a la lista de vigilancia (meta "restock") y se avisa 1 vez "👀 la vigilo".
  * Vuelve el stock   -> 🚨 aviso inmediato (prioridad máxima), aunque no la sigas con /seguir.

¿Cada cuánto se relee una ganga agotada?
  * Tiendas chicas (Entel: 2 páginas) -> el rastreador ya las recorre enteras cada ~30–60 s.
  * Tiendas grandes (Falabella, Abc, Hites, PCFactory: barridos de 30 min a 2 h) -> RestockWatcher relee la página
    exacta donde apareció cada ~2 min (agrupando: varias gangas de la misma página = 1 sola petición).
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime

import config
import userprefs
from analysis import Alert, bargain, clp
from matcher import group_key
from fetchers import BlockedError, RobotsDisallowed, jittered
from models import ScrapeResult
from scrapers import SCRAPERS, SITE_LABELS
from storage import Observation, PriceStore, ago, now_iso

log = logging.getLogger("restock")

META = "restock"
MAX_WATCH = 80                 # tope de la lista (se quedan las de mayor ahorro)
WATCH_DAYS = 30                # una ganga agotada por más de 30 días sale de la lista
EVERY = config._float("RESTOCK_EVERY", 120)        # s entre rondas del vigilante
MAX_URLS_PER_ROUND = 12
SKIP_SITES = {"maconline"}     # su "listado" son ~15 fichas: releerlo cada 2 min sería abusivo (lo cubre el ciclo profundo)
FAST_SWEEP_MIN = 5            # tiendas cuyo barrido completo dura menos que esto ya se releen solas


def load(store: PriceStore) -> dict:
    return store.get_meta(META, {}) or {}


def _available(p) -> bool:
    return p.in_stock is not False                   # sin dato de stock = se asume disponible


def check(store: PriceStore, obs: Observation, gk: str, category: str, src: config.Source, watch: dict,
          quiet: bool = False) -> list[Alert]:
    """Evalúa ganga / reposición para una observación. Modifica `watch` en el lugar."""
    p = obs.product
    entry = watch.get(p.key)
    restocked = obs.was_in_stock is False and _available(p)
    if not (obs.is_new or obs.direction == "down" or entry or restocked):
        return []                                    # nada cambió: no se recalcula (es una consulta a la BD)
    hit = bargain(store, p, gk, category)
    out: list[Alert] = []
    if hit and not _available(p):
        if not entry:
            watch[p.key] = {"name": p.name[:90], "site": p.site, "price": p.price, "ref": hit[1], "reason": hit[0],
                            "since": now_iso(), "found_at": p.found_at, "source": src.name, "image": p.image,
                            "url": p.url}
            if not quiet:
                out.append(Alert("restock_watch", obs, "👀 GANGA AGOTADA: la vigilo",
                                 [hit[0], "Te aviso apenas vuelva el stock 🚨"], 2, hit[1]))
        else:
            entry.update(price=p.price, name=p.name[:90], image=p.image or entry.get("image"),
                         found_at=p.found_at or entry.get("found_at"))
        return out
    if entry and _available(p):                      # 🚨 volvió el stock
        watch.pop(p.key, None)
        if hit or p.price <= entry["price"] * 1.1:
            since = datetime.fromisoformat(entry["since"])
            out.append(Alert("restock", obs, "🚨🔥 ¡VOLVIÓ EL STOCK DE UNA GANGA! 🔥🚨",
                             [hit[0] if hit else entry.get("reason", ""),
                              f"Agotada desde el {since:%d-%m %H:%M}: corre, suele durar poco"], 0, entry.get("ref")))
        return out
    if entry and not hit:                            # subió de precio estando agotada: ya no es ganga
        watch.pop(p.key, None)
        return out
    if hit and not quiet and (obs.is_new or obs.direction == "down" or restocked):
        out.append(Alert("ganga", obs, "🔥 GANGA", [hit[0]], 1, hit[1]))
    return out


def prune(watch: dict) -> dict:
    now = datetime.now()
    keep = {k: v for k, v in watch.items()
            if (now - datetime.fromisoformat(v["since"])).days < WATCH_DAYS}
    if len(keep) > MAX_WATCH:
        best = sorted(keep.items(), key=lambda kv: kv[1]["price"] / max(kv[1]["ref"], 1))[:MAX_WATCH]
        keep = dict(best)
    return keep


def seed(store: PriceStore) -> list[dict]:
    """Primera vez: busca en todo el catálogo las gangas que YA están agotadas y las pone en vigilancia."""
    from models import Product
    watch = load(store)
    added = []
    rows = store.conn.execute(
        "SELECT * FROM products WHERE in_stock=0 AND missing_since IS NULL AND price>0 AND group_key NOT LIKE '%:%'"
    ).fetchall()
    for r in rows:
        if r["key"] in watch:
            continue
        p = Product(r["key"], r["site"], r["name"], r["url"], r["price"], list_price=r["list_price"],
                    brand=r["brand"], image=r["image"], in_stock=False)
        cat = r["category"] or "celulares"
        hit = bargain(store, p, group_key(r["name"], r["brand"], cat), cat)   # clave recalculada (reglas nuevas)
        if hit:
            watch[p.key] = {"name": p.name[:90], "site": p.site, "price": p.price, "ref": hit[1], "reason": hit[0],
                            "since": now_iso(), "found_at": None, "source": r["source"], "image": p.image,
                            "url": p.url}
            added.append(watch[p.key])
    store.set_meta(META, prune(watch))
    store.set_meta(f"{META}:seeded", now_iso())
    store.commit()
    return added


def seed_message(added: list[dict]) -> str:
    lines = [f"👀 <b>Vigilando {len(added)} gangas agotadas</b>",
             "Te aviso al instante (🚨) si alguna vuelve a tener stock:"]
    for e in sorted(added, key=lambda e: e["price"] / max(e["ref"], 1))[:25]:
        lines.append(f"• {html.escape(e['name'][:55])} — <b>{clp(e['price'])}</b> "
                     f"(nuevo ~{clp(e['ref'])}) · {SITE_LABELS.get(e['site'], e['site'])}")
    if len(added) > 25:
        lines.append(f"… y {len(added) - 25} más (pestaña Ofertas → 🚨 Agotadas en vigilancia)")
    return "\n".join(lines)


def pending_from_app(store: PriceStore) -> list[Alert]:
    """Gangas que la app marca (dashboard.py guarda "gangas:app") y que Telegram todavía no avisó en 7 días.
    Pasa cuando la ganga aparece por un cambio de la REFERENCIA (otra tienda subió) o en un 1er barrido silencioso.
    Con stock -> 🔥 GANGA; agotada -> entra a vigilancia y 👀 aviso."""
    from models import Product
    marked = store.get_meta("gangas:app", {}) or {}
    watch, out = load(store), []
    since = ago(days=7)
    for key, g in marked.items():
        r = store.conn.execute("SELECT * FROM products WHERE key=?", (key,)).fetchone()
        if not r or r["missing_since"] or key.startswith("url:"):
            continue
        sold_out = r["in_stock"] == 0
        kinds = ("restock_watch", "restock", "ganga") if sold_out else ("ganga", "restock", "price_error", "godzilla")
        done = store.conn.execute(
            f"SELECT 1 FROM alerts WHERE key=? AND ts>=? AND kind IN ({','.join('?' * len(kinds))}) LIMIT 1",
            (key, since, *kinds)).fetchone()
        if sold_out and key not in watch:
            watch[key] = {"name": r["name"][:90], "site": r["site"], "price": r["price"], "ref": g["ref"],
                          "reason": g["why"], "since": now_iso(), "found_at": None, "source": r["source"],
                          "image": r["image"], "url": r["url"]}
        if done:
            continue
        p = Product(key, r["site"], r["name"], r["url"], r["price"], list_price=r["list_price"],
                    card_price=r["card_price"], brand=r["brand"], image=r["image"],
                    in_stock=None if r["in_stock"] is None else bool(r["in_stock"]), installments=r["installments"])
        obs = Observation(p, r["price"], r["list_price"], None, r["first_seen"], p.in_stock, False)
        if sold_out:
            out.append(Alert("restock_watch", obs, "👀 GANGA AGOTADA: la vigilo",
                             [g["why"], "Te aviso apenas vuelva el stock 🚨"], 2, g["ref"]))
        else:
            out.append(Alert("ganga", obs, "🔥 GANGA", [g["why"]], 1, g["ref"]))
    store.set_meta(META, prune(watch))
    return out


class RestockWatcher:
    """Relee seguido las páginas donde aparecieron las gangas agotadas de tiendas con barridos lentos."""

    def __init__(self, monitor) -> None:
        self.mon = monitor
        self.reads = 0

    def _fast_sources(self, store: PriceStore) -> set[str]:
        fast = set()
        for s in config.SOURCES:
            sw = store.get_meta(f"sweep:{s.name}")
            if sw and sw.get("minutes", 999) < FAST_SWEEP_MIN:
                fast.add(s.name)
        return fast

    async def run(self, stop: asyncio.Event) -> None:
        sources = {s.name: s for s in config.SOURCES}
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=jittered(EVERY))
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self._round(sources)
            except Exception:  # noqa: BLE001 - el vigilante nunca debe botar la ventana
                log.exception("Ronda de vigilancia de stock falló")

    async def _round(self, sources: dict[str, config.Source]) -> None:
        with PriceStore(config.DB_PATH) as store:
            watch = load(store)
            fast = self._fast_sources(store)
            urls: dict[str, config.Source] = {}
            for e in watch.values():
                src = sources.get(e.get("source") or "")
                if (not e.get("found_at") or not src or src.name in fast or src.site in SKIP_SITES
                        or self.mon._paused(store, src)):
                    continue
                urls.setdefault(e["found_at"], src)
        for url, src in list(urls.items())[:MAX_URLS_PER_ROUND]:
            while self.mon.cycle_busy:
                await asyncio.sleep(1)
            try:
                res = await SCRAPERS[src.site]().fetch(self.mon.ctx, url)
            except BlockedError as exc:
                with PriceStore(config.DB_PATH) as store:
                    self.mon._update_breaker(store, ScrapeResult(src, error=str(exc), blocked=True), now_iso())
                continue
            except (RobotsDisallowed, Exception) as exc:  # noqa: BLE001
                log.info("Vigilancia: no pude releer %s (%s)", src.name, str(exc)[:80])
                continue
            self.reads += 1
            for p in res.products:
                p.found_at = url
            ts = now_iso()
            with PriceStore(config.DB_PATH) as store:
                alerts = self.mon.ingest(store, src, res.products, ts, warming=False,
                                         targets=userprefs.all_targets(store))
                store.commit()
                if alerts:
                    await self.mon.dispatch(store, alerts, ts)
