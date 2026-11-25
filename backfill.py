"""📚 Importa el historial de 90 días de SoloTodo a NUESTRA base (una sola vez, en segundo plano, a ritmo lento).

Fase 1 (mapa): recorre /entities/ por categoría para todas las tiendas que leemos y empareja cada oferta de SoloTodo
               con nuestro producto por el número del link o el SKU (exacto, sin adivinar por nombre).
Fase 2 (historial): 1 petición por MODELO (/products/<id>/pricing_history/) trae todas sus tiendas; se guardan solo
               los CAMBIOS de precio anteriores a nuestro primer registro, y first_seen retrocede (mínimos reales).

Todo el avance vive en la BD (meta "bf:*"), así que continúa entre ventanas de GitHub Actions. ~1 petición cada
PACE s, cede el paso a los ciclos rápidos. Si SoloTodo falla, se reintenta en la ventana siguiente.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import config
from scrapers.solotodo import STORES as ST_SOURCED
from storage import PriceStore

log = logging.getLogger("backfill")

API = "https://api.solotodo.com"
PACE = config._float("BACKFILL_PACE", 5)       # s extra entre peticiones (además del espaciado del fetcher)
DAYS = 90
CATS = [6, 1, 11, 33, 14, 50, 48, 4, 15, 19]
# tiendas que leemos directo: id SoloTodo -> nuestro site
DIRECT = {9: "falabella", 8147: "falabella", 8015: "abc", 87: "hites", 12: "pcfactory", 223: "samsung", 38: "entel",
          181: "entel", 6365: "apple", 8411: "xiaomi", 6794: "xiaomi", 199: "lenovo"}


def _candidates(site: str, e: dict) -> list[str]:
    if e["store"] in ST_SOURCED:                                   # llegó vía SoloTodo: clave por id de oferta
        return [f"{ST_SOURCED[e['store']][0]}:st{e['id']}"]
    nums = re.findall(r"\d{4,}", e.get("external_url") or "") + [str(e.get("sku") or "")]
    return [f"{site}:{n}" for n in dict.fromkeys(nums) if n]


class Backfill:
    def __init__(self, monitor) -> None:
        self.mon = monitor
        self.requests = self.imported = 0

    async def _get(self, path: str, **params):
        while self.mon.cycle_busy:
            await asyncio.sleep(1)
        await asyncio.sleep(PACE)
        self.requests += 1
        url = f"{API}/{path}" + ("?" + urlencode(params, doseq=True) if params else "")
        return await self.mon.ctx.http.get(url, accept="application/json", as_json=True, retries=2)

    async def run(self, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                with PriceStore(config.DB_PATH) as store:
                    st = store.get_meta("bf:state") or {"phase": "map", "cat": 0, "page": 1}
                if st["phase"] == "done":
                    return
                if st["phase"] == "map":
                    await self._map_step(st)
                else:
                    await self._history_step()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - nunca botar la ventana; sigue en la próxima
            log.info("📚 Importación de historial en pausa (%s); sigue en la próxima ventana", str(exc)[:120])

    async def _map_step(self, st: dict) -> None:
        stores = list(DIRECT) + list(ST_SOURCED)
        cat = CATS[st["cat"]]
        data = await self._get("entities/", categories=cat, stores=stores, page_size=200, page=st["page"])
        with PriceStore(config.DB_PATH) as store:
            pending = store.get_meta("bf:pending") or {}             # product_id -> {entity_id: our_key}
            for e in data.get("results") or []:
                prod = e.get("product") or {}
                site = DIRECT.get(e["store"], "")
                if not prod.get("id") or (not site and e["store"] not in ST_SOURCED):
                    continue
                for k in _candidates(site, e):
                    if store.conn.execute("SELECT 1 FROM products WHERE key=?", (k,)).fetchone():
                        pending.setdefault(str(prod["id"]), {})[str(e["id"])] = k
                        break
            store.set_meta("bf:pending", pending)
            if data.get("next"):
                st["page"] += 1
            elif st["cat"] + 1 < len(CATS):
                st.update(cat=st["cat"] + 1, page=1)
            else:
                st = {"phase": "history"}
                log.info("📚 Mapa listo: %d modelos de SoloTodo emparejados con nuestro catálogo", len(pending))
            store.set_meta("bf:state", st)

    async def _history_step(self) -> None:
        with PriceStore(config.DB_PATH) as store:
            pending = store.get_meta("bf:pending") or {}
        if not pending:
            with PriceStore(config.DB_PATH) as store:
                store.set_meta("bf:state", {"phase": "done", "ts": datetime.now().isoformat(timespec="seconds")})
            log.info("📚 Historial importado por completo")
            return
        pid, ents = next(iter(pending.items()))
        since = (datetime.now(timezone.utc) - timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = await self._get(f"products/{pid}/pricing_history/", timestamp_after=since)
        with PriceStore(config.DB_PATH) as store:
            for item in rows or []:
                key = ents.get(str((item.get("entity") or {}).get("id")))
                if key:
                    self.imported += import_history(store, key, item.get("pricing_history") or [])
            pending.pop(pid, None)
            store.set_meta("bf:pending", pending)


def import_history(store: PriceStore, key: str, history: list[dict]) -> int:
    """Guarda los cambios de precio de SoloTodo ANTERIORES a nuestro primer registro de ese producto."""
    first = store.conn.execute("SELECT MIN(ts) FROM prices WHERE key=?", (key,)).fetchone()[0]
    if not first:
        return 0
    last, n, earliest = None, 0, None
    for r in sorted(history, key=lambda r: r["timestamp"]):
        if not r.get("is_available") or not r.get("normal_price"):
            continue
        ts = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        ts = ts.isoformat(timespec="seconds")
        if ts >= first:
            break
        price = int(float(r["normal_price"]))
        offer = int(float(r["offer_price"])) if r.get("offer_price") else None
        card = offer if offer and offer < price else None
        if (price, card) != last:                                   # historial compacto: solo cambios
            store.conn.execute("INSERT INTO prices(key, price, list_price, card_price, ts) VALUES (?,?,?,?,?)",
                               (key, price, None, card, ts))
            last, n, earliest = (price, card), n + 1, earliest or ts
    if earliest:
        store.conn.execute("UPDATE products SET first_seen=MIN(first_seen, ?) WHERE key=?", (earliest, key))
    store.commit()
    return n
