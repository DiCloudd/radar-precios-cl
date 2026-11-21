"""Construye el sitio estático (GitHub Pages): data.json + exportaciones CSV/XLSX + respaldo de la BD."""
from __future__ import annotations

import csv
import gzip
import json
import shutil
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

import config
import godzilla
from analysis import fake_discount, predict
from calendar_cl import active_event, upcoming
from matcher import comparable
from models import Product
from scrapers import SITE_LABELS
from storage import Observation, PriceStore, ago

TEMPLATE_DIR = config.BASE_DIR / "dashboard"


def _product_from_row(r: sqlite3.Row) -> Product:
    return Product(r["key"], r["site"], r["name"], r["url"], r["price"], r["list_price"], r["card_price"],
                   r["brand"], r["image"], None if r["in_stock"] is None else bool(r["in_stock"]),
                   r["stock_text"], r["installments"], json.loads(r["badges"]) if r["badges"] else [])


def build_data(store: PriceStore) -> dict:
    since24 = ago(hours=24)
    products = []
    for r in store.products():
        hist = [(h["ts"], h["price"]) for h in store.history(r["key"])]
        prices = [p for _, p in hist] or [r["price"]]
        old = next((p for ts, p in reversed(hist) if ts < since24), None)
        prod = _product_from_row(r)
        obs = Observation(prod, None, None, min(prices), r["first_seen"], None, False)
        products.append({
            "key": r["key"], "site": r["site"], "store": SITE_LABELS.get(r["site"], r["site"]), "name": r["name"],
            "cat": r["category"] or "celulares",
            "url": r["url"], "brand": r["brand"], "image": r["image"], "group": r["group_key"],
            "price": r["price"], "list": r["list_price"], "card": r["card_price"],
            "disc": round(prod.discount_pct, 1) if prod.discount_pct else None,
            "stock": None if r["in_stock"] is None else bool(r["in_stock"]), "stockText": r["stock_text"],
            "cuotas": r["installments"], "badges": prod.badges, "first": r["first_seen"], "last": r["last_seen"],
            "missing": r["missing_since"], "min": min(prices), "max": max(prices),
            "ch24": round((r["price"] / old - 1) * 100, 1) if old else None,
            "atMin": r["price"] <= min(prices) and len(hist) > 1,
            "hist": [[ts[:16], p] for ts, p in hist][-120:],
            "pred": predict(hist), "fake": fake_discount(obs, store),
        })
    groups: dict[str, list] = {}
    for p in products:
        if p["group"] and not p["missing"]:
            groups.setdefault(p["group"], []).append(p)
    comparisons = []
    for g, items in groups.items():
        if not comparable(g) or g.startswith("accesorio") or len({i["site"] for i in items}) < 2:
            continue
        best_per_store: dict[str, dict] = {}            # una fila por tienda: su variante más barata
        for i in sorted(items, key=lambda i: i["price"]):
            best_per_store.setdefault(i["site"], i)
        items = sorted(best_per_store.values(), key=lambda i: i["price"])
        if items[-1]["price"] > items[0]["price"] * 2.5:   # red de seguridad: casi seguro son productos distintos
            continue
        comparisons.append({"group": g, "cat": items[0]["cat"], "best": items[0]["key"], "spread": items[-1]["price"] - items[0]["price"],
                            "median": statistics.median(i["price"] for i in items),
                            "items": [{"key": i["key"], "store": i["store"], "price": i["price"], "url": i["url"],
                                       "seller": next((b.split(": ", 1)[1].strip() for b in i["badges"] if b.startswith("Vendedor: ")), None)}
                                      for i in items]})
    comparisons.sort(key=lambda c: -c["spread"])

    stores = []
    for src in config.SOURCES:
        runs = store.recent_runs(src.name, 60)
        last = runs[0] if runs else None
        block = store.get_meta(f"block:{src.site}") or {}
        day = [r for r in runs if r["ts"] >= since24]
        stores.append({
            "site": src.site, "name": src.name, "cat": src.category, "trap": "⚡" in src.name, "label": SITE_LABELS.get(src.site, src.site), "url": src.url,
            "lastTs": last["ts"] if last else None, "ok": bool(last["ok"]) if last else None,
            "n": last["n"] if last else 0, "method": last["method"] if last else None,
            "fallback": bool(last["fallback"]) if last else False, "error": last["error"] if last else None,
            "blocked": bool(block.get("until", 0) > datetime.now().timestamp()),
            "strikes": block.get("strikes", 0), "uptime24": round(sum(r["ok"] for r in day) / len(day) * 100) if day else None,
            "avgSec": round(statistics.mean(r["duration"] for r in day), 1) if day else None,
            "products": sum(1 for p in products if p["site"] == src.site and not p["missing"]),
        })
    # 🏆 salón de la fama: errores de precio detectados, cuánto duraron y cuánto ahorraban
    errors = []
    for e in store.errors(200):
        ref = e["ref_price"] or 0
        errors.append({**dict(e), "store": SITE_LABELS.get(e["site"], e["site"]),
                       "disc": round((1 - e["error_price"] / ref) * 100) if ref > e["error_price"] else None,
                       "saving": ref - e["error_price"] if ref > e["error_price"] else None})
    durations = [e["minutes"] for e in errors if e["minutes"] and e["outcome"] in ("corregido", "agotado", "retirado")]
    hall = {"count": len(errors), "confirmed": sum(1 for e in errors if e["verdict"] == "confirmed"),
            "median_minutes": round(statistics.median(durations), 1) if durations else None,
            "max_disc": max((e["disc"] for e in errors if e["disc"]), default=None),
            "total_saving": sum(e["saving"] for e in errors if e["saving"] and e["verdict"] != "corrected")}

    # 🤡 ranking de tiendas "chantas": descuentos inflados (necesita historial) + qué tanto abusan del precio tachado
    ranking = []
    for site in sorted({p["site"] for p in products if not p["missing"]}):
        items = [p for p in products if p["site"] == site and not p["missing"]]
        listed = [p for p in items if p["disc"]]
        fakes = sum(1 for p in items if p["fake"])
        fake_share = fakes / len(items) if items else 0
        avg_disc = statistics.mean(p["disc"] for p in listed) if listed else 0
        index = round(0.6 * min(100, fake_share * 400) + 0.4 * min(100, avg_disc * 2))
        ranking.append({"site": site, "store": SITE_LABELS.get(site, site), "products": len(items),
                        "tachado_pct": round(len(listed) / len(items) * 100) if items else 0,
                        "avg_disc": round(avg_disc, 1), "fakes": fakes, "index": index})
    ranking.sort(key=lambda r: -r["index"])
    history_days = 0
    first = store.conn.execute("SELECT MIN(ts) FROM prices").fetchone()[0]
    if first:
        history_days = (datetime.now() - datetime.fromisoformat(first)).days

    alerts = [dict(r) for r in store.conn.execute(
        "SELECT ts, key, kind, price, message FROM alerts ORDER BY id DESC LIMIT 150")]
    ev = active_event()
    return {
        "generated": datetime.now().isoformat(timespec="seconds"), "runner": config.RUNNER_NAME,
        "event": ev.name if ev else None,
        "upcoming": [{"name": e.name, "start": e.start.isoformat(), "end": e.end.isoformat(), "estimated": e.estimated}
                     for e in upcoming()],
        "categories": config.CATEGORY_LABELS,
        "errors": errors, "hall": hall, "ranking": ranking, "historyDays": history_days,
        "stores": stores, "products": products, "comparisons": comparisons[:300], "alerts": alerts,
        "totals": {"products": len(products), "active": sum(1 for p in products if not p["missing"]),
                   "drops24": sum(1 for p in products if p["ch24"] and p["ch24"] < 0),
                   "godzillas": sum(1 for a in alerts if a["kind"] in ("godzilla", "price_error")),
                   "fakes": sum(1 for p in products if p["fake"]), "comparisons": len(comparisons)},
    }


def export_files(data: dict, store: PriceStore, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    cols = ["store", "name", "price", "card", "list", "disc", "stock", "cuotas", "min", "max", "ch24", "group", "url", "key"]
    with open(out / "precios_actuales.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for p in data["products"]:
            w.writerow([p.get(c) for c in cols])
    hist_rows = store.conn.execute(
        "SELECT h.ts, p.site, p.name, h.price, h.list_price, h.card_price, h.key FROM prices h "
        "JOIN products p ON p.key=h.key ORDER BY h.ts").fetchall()
    with open(out / "historial.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "site", "name", "price", "list_price", "card_price", "key"])
        w.writerows(tuple(r) for r in hist_rows)
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        wb = Workbook()
        ws = wb.active
        ws.title = "Precios actuales"
        ws.append(cols)
        for p in data["products"]:
            ws.append([p.get(c) if not isinstance(p.get(c), list) else ", ".join(p.get(c)) for c in cols])
        ws2 = wb.create_sheet("Comparador")
        ws2.append(["modelo", "tienda", "precio", "url"])
        for c in data["comparisons"]:
            for i in c["items"]:
                ws2.append([c["group"], i["store"], i["price"], i["url"]])
        ws3 = wb.create_sheet("Historial")
        ws3.append(["ts", "site", "name", "price", "list_price", "card_price", "key"])
        for r in hist_rows[-50000:]:
            ws3.append(list(r))
        for sheet in wb.worksheets:
            for cell in sheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="4F46E5")
            sheet.freeze_panes = "A2"
        wb.save(out / "precios.xlsx")
    except ImportError:
        pass


def build_site(out: Path = config.SITE_DIR) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    with PriceStore(config.DB_PATH) as store:
        data = build_data(store)
        export_files(data, store, out / "exports")
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for f in TEMPLATE_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, out / f.name)
    shutil.copy2(godzilla.asset(), out / "godzilla.gif")
    (out / "data").mkdir(exist_ok=True)
    # respaldo de la BD (snapshot consistente con la API de backup de sqlite)
    tmp = out / "data" / "prices.snapshot.db"
    src = sqlite3.connect(config.DB_PATH)
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    dst.close(), src.close()
    with open(tmp, "rb") as fi, gzip.open(out / "data" / "prices.db.gz", "wb", compresslevel=9) as fo:
        shutil.copyfileobj(fi, fo)
    tmp.unlink()
    (out / ".nojekyll").write_text("")
    return out
