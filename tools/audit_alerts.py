"""Auditoría de alertas de "error de precio" con datos reales (usa EXACTAMENTE las mismas reglas que el monitor).

    python tools/audit_alerts.py COPIA/prices.db

1. Falsos positivos: ¿qué productos reales dispararían una alerta HOY si acabaran de aparecer?
2. Simulación: ¿y si un producto conocido bajara 35 %?
3. Sensibilidad: un producto real de alto valor aparece al 10 % de su precio, ¿lo detectamos?

⚠️ Recalcula los grupos de la BD que recibe: úsalo sobre una COPIA (p.ej. la bajada de GitHub Pages).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import TOO_CHEAP, family_error, is_accessory, price_error_reasons  # noqa: E402
from matcher import group_key  # noqa: E402
from models import Product  # noqa: E402
from scrapers import SITE_LABELS  # noqa: E402
from storage import PriceStore  # noqa: E402


def product(r, price=None, list_price=None) -> Product:
    return Product(r["key"], r["site"], r["name"], r["url"], price or r["price"],
                   r["list_price"] if list_price is None else list_price, r["card_price"],
                   badges=json.loads(r["badges"]) if r["badges"] else [])


def reasons(store, p, cat, gk, **kw) -> list[str]:
    out = price_error_reasons(p, cat, **kw)
    if not out and not is_accessory(p.name, cat):
        hit = family_error(store, p, gk, cat)
        if hit:
            out.append(hit[0])
    return out


def main() -> None:
    store = PriceStore(Path(sys.argv[1]))
    rows = store.conn.execute("SELECT * FROM products WHERE missing_since IS NULL").fetchall()
    for r in rows:                                   # grupos con el agrupador actual
        cat = r["category"] or "celulares"
        gk = group_key(r["name"], r["brand"], cat)
        store.conn.execute("UPDATE products SET group_key=? WHERE key=?",
                           (f"accesorio: {gk}" if is_accessory(r["name"], cat) else gk, r["key"]))
    store.commit()
    rows = store.conn.execute("SELECT * FROM products WHERE missing_since IS NULL").fetchall()

    hits, sim = [], []
    for r in rows:
        cat = r["category"] or "celulares"
        row = (cat, SITE_LABELS.get(r["site"], r["site"]), r["price"], r["list_price"], r["card_price"], r["name"])
        for x in reasons(store, product(r), cat, r["group_key"], is_new=True):
            hits.append(row + (x,))
        for x in reasons(store, product(r), cat, r["group_key"], is_new=False, drop_pct=35):
            sim.append(row + (x,))
    for title, data in ((f"💥 FALSOS POSITIVOS (alertarían HOY como producto nuevo): {len(hits)}", hits),
                        (f"🧪 alertarían SI bajaran 35 %: {len(sim)}", sim)):
        print(f"\n{title}  (de {len(rows)} productos)")
        for h in sorted(data)[:30]:
            print(f"[{h[0]}] {h[1]:<16} ${h[2]:>9,} normal {h[3] or '-':>9} 💳 {h[4] or '-':>8} | {h[5][:64]}\n        -> {h[6]}")
    print(f"\naccesorios detectados: {sum(1 for r in rows if is_accessory(r['name'], r['category'] or ''))} de {len(rows)}")

    tested, caught, missed = 0, 0, []
    for r in rows:
        cat = r["category"] or "celulares"
        rule = TOO_CHEAP.get(cat)
        if not rule or not rule[0].search(r["name"]) or r["price"] < rule[1] * 1.5 or is_accessory(r["name"], cat):
            continue
        tested += 1
        p = product(r, price=int(r["price"] * 0.10), list_price=r["list_price"] or r["price"])
        if reasons(store, p, cat, r["group_key"], is_new=True):
            caught += 1
        else:
            missed.append((cat, SITE_LABELS.get(r["site"]), r["price"], r["name"][:64]))
    print(f"\n🎯 SENSIBILIDAD: {caught}/{tested} errores simulados (producto real al 10 % de su precio) detectados "
          f"= {caught / max(tested, 1) * 100:.0f} %")
    for m in missed[:25]:
        print("   no detectado:", m)
    store.close()


if __name__ == "__main__":
    main()
