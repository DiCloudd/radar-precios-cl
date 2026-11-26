"""🇨🇱 Nodo en Chile (este PC): lee las tiendas que bloquean a GitHub y la nube integra lo leído.

    En el PC (tools/node_windows.ps1, tarea "RadarPreciosNodo" al iniciar sesión):
        NODE_MODE=1 NODE_SITES=paris python main.py --loop --duration 12   # monitor local, sin publicar ni Telegram
        python node.py export node.json.gz                                  # foto de lo leído -> rama node-data

    En la nube (cada ventana, main.run_loop): import_into(monitor) trae node.json.gz de la rama node-data y lo
    procesa con las MISMAS reglas (historial, alertas, verificación). Si el nodo está apagado, no pasa nada.

Privacidad: el archivo solo tiene productos y precios públicos de las tiendas (nada personal).
"""
from __future__ import annotations

import gzip
import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import config
from storage import PriceStore, ago, now_iso

log = logging.getLogger("node")

BRANCH = "node-data"
FILE = "node.json.gz"
FRESH_MIN = 45                     # datos del nodo más viejos que esto no se integran
FIELDS = ("key", "site", "name", "url", "price", "list_price", "card_price", "brand", "image", "in_stock",
          "stock_text", "installments", "badges", "source", "category", "last_seen")


def export(db: Path, out: Path, sites: set[str] | None = None) -> int:
    sites = sites or config.NODE_SITES
    with PriceStore(db) as store:
        marks = ",".join("?" * len(sites))
        rows = store.conn.execute(
            f"SELECT * FROM products WHERE site IN ({marks}) AND last_seen>=? AND missing_since IS NULL",
            (*sites, ago(minutes=FRESH_MIN))).fetchall()
    data = {"ts": now_iso(), "runner": config.RUNNER_NAME, "sites": sorted(sites),
            "products": [{f: r[f] for f in FIELDS} for r in rows]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False).encode("utf-8")))
    return len(rows)


def _fetch() -> dict | None:
    subprocess.run(["git", "fetch", "-q", "--depth", "1", "origin", BRANCH], capture_output=True)
    r = subprocess.run(["git", "show", f"origin/{BRANCH}:{FILE}"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    return json.loads(gzip.decompress(r.stdout).decode("utf-8"))


async def import_into(mon, data: dict | None = None) -> int:
    """Integra la foto del nodo (si es nueva y fresca). Devuelve cuántos productos procesó."""
    from models import Product, ScrapeResult
    import userprefs
    data = data if data is not None else _fetch()
    if not data:
        return 0
    with PriceStore(config.DB_PATH) as store:
        if store.get_meta("node:last_ts") == data["ts"]:
            return 0                                          # esa foto ya se integró
        if datetime.fromisoformat(data["ts"]) < datetime.now() - timedelta(minutes=FRESH_MIN):
            return 0                                          # el nodo está apagado: datos viejos
        sources = {s.name: s for s in config.SOURCES}
        by_src: dict[str, list] = {}
        for r in data["products"]:
            badges = json.loads(r["badges"]) if r.get("badges") else []
            p = Product(r["key"], r["site"], r["name"], r["url"], r["price"], r.get("list_price"), r.get("card_price"),
                        r.get("brand"), r.get("image"), None if r.get("in_stock") is None else bool(r["in_stock"]),
                        r.get("stock_text"), r.get("installments"), badges)
            by_src.setdefault(r.get("source") or "", []).append(p)
        ts, alerts, n = now_iso(), [], 0
        targets = userprefs.all_targets(store)
        for name, products in by_src.items():
            src = sources.get(name)
            if not src:
                continue
            warming = not store.recent_runs(src.name, 1)
            res = ScrapeResult(src, products=products, method=f"🇨🇱 nodo Chile ({data.get('runner') or 'PC'})")
            store.log_run(res, "node", data.get("runner") or "chile", ts)
            store.set_meta(f"block:{src.site}", {"strikes": 0, "until": 0})
            alerts += mon.ingest(store, src, products, ts, warming, targets)
            n += len(products)
        store.commit()
        store.set_meta("node:last_ts", data["ts"])
        mon.node_sites = set(data.get("sites") or [])
        if alerts:
            await mon.dispatch(store, alerts, ts)
    log.info("🇨🇱 Nodo Chile: %d productos integrados (%s)", n, ", ".join(data.get("sites") or []))
    return n


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "export":
        print(f"exportados {export(config.DB_PATH, Path(sys.argv[2]))} productos")
    else:
        print(__doc__)
        sys.exit(2)
