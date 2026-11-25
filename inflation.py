"""📈 Registro de precios inflados: tiendas que suben un precio para después "bajarlo" (o anuncian un "antes"
mayor al que realmente cobraban). Se muestra en la app (pestaña 🤡 Chantas) y en Telegram con /inflados.

Fuentes:
  * "historial": nuestro propio historial (subió y volvió al precio de siempre).
  * "solotodo":  historial público de 90 días de SoloTodo (api.solotodo.com), que cubre meses antes de que el
                 radar existiera. Ver pricecheck.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from storage import PriceStore, now_iso

META = "inflados"
MAX_ENTRIES = 400


def load(store: PriceStore) -> list[dict]:
    return store.get_meta(META, []) or []


def log(store: PriceStore, p, kind: str, claimed: int | None, usual: int | None, source: str,
        note: str = "") -> bool:
    """Anota un caso (1 por producto y semana). Devuelve True si era nuevo."""
    items = load(store)
    week = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    if any(e["key"] == p.key and e["ts"] >= week for e in items):
        return False
    items.insert(0, {"ts": now_iso(), "key": p.key, "site": p.site, "name": p.name[:90], "url": p.url,
                     "kind": kind, "claimed": claimed, "usual": usual, "price": p.price, "source": source,
                     "note": note[:160]})
    store.set_meta(META, items[:MAX_ENTRIES])
    return True


def scan(store: PriceStore, days: int = 21, min_price: int = 50_000) -> int:
    """Barrido diario del catálogo: productos que en los últimos `days` días subieron ≥15 % sobre su precio de
    siempre y luego volvieron a él (±3 %). Así quedan registrados aunque no hayan generado un aviso."""
    from models import Product
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    rows = store.conn.execute(
        "SELECT h.key, h.price, h.ts FROM prices h JOIN products p ON p.key=h.key "
        "WHERE h.ts>=? AND p.missing_since IS NULL AND p.key NOT LIKE 'url:%' ORDER BY h.key, h.ts", (since,)).fetchall()
    series: dict[str, list[int]] = {}
    for r in rows:
        series.setdefault(r["key"], []).append(r["price"])
    n = 0
    for key, ps in series.items():
        if len(ps) < 3:
            continue
        top = max(range(len(ps)), key=lambda i: ps[i])
        base, peak, now = min(ps[:top] or ps[:1]), ps[top], ps[-1]
        if 0 < top < len(ps) - 1 and peak >= base * 1.15 and now <= base * 1.03 and now >= min_price:
            r = store.conn.execute("SELECT * FROM products WHERE key=?", (key,)).fetchone()
            p = Product(key, r["site"], r["name"], r["url"], now)
            n += log(store, p, "subió y bajó", peak, base, "historial",
                     f"lo subieron de {_clp(base)} a {_clp(peak)} (+{(peak / base - 1) * 100:.0f}%) y volvió a {_clp(now)}")
    return n


def _clp(n) -> str:
    return "$" + f"{int(n):,}".replace(",", ".")


def per_store(items: list[dict], days: int = 30) -> dict[str, int]:
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    out: dict[str, int] = {}
    for e in items:
        if e["ts"] >= since:
            out[e["site"]] = out.get(e["site"], 0) + 1
    return out
