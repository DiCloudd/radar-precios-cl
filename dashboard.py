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
from analysis import bargain_vs, fake_discount, family_key, predict
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
        if r["key"].startswith("url:"):            # 🙈 productos que sigues con /seguir: privados, no se publican
            continue
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
            "hist": [[ts[:16], p] for ts, p in hist][-40:] if len(hist) > 1 else [],   # compacto: miles de productos
            "pred": predict(hist) if len(hist) >= 4 else None,
            "fake": fake_discount(obs, store) if prod.list_price else None,
        })
    _mark_gangas(products)
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
                            "items": [{"key": i["key"], "store": i["store"], "price": i["price"], "url": i["url"], "card": i["card"],
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
            "site": src.site, "name": src.name, "cat": src.category, "trap": any(m in src.name for m in ("⚡", "🆕")), "label": SITE_LABELS.get(src.site, src.site), "url": src.url,
            "lastTs": last["ts"] if last else None, "ok": bool(last["ok"]) if last else None,
            "n": last["n"] if last else 0, "method": last["method"] if last else None,
            "fallback": bool(last["fallback"]) if last else False, "error": last["error"] if last else None,
            "blocked": bool(block.get("until", 0) > datetime.now().timestamp()),
            "strikes": block.get("strikes", 0), "uptime24": round(sum(r["ok"] for r in day) / len(day) * 100) if day else None,
            "avgSec": round(statistics.mean(r["duration"] for r in day), 1) if day else None,
            "products": sum(1 for p in products if p["site"] == src.site and not p["missing"]),
            "sweep": store.get_meta(f"sweep:{src.name}"), "crawlPage": (store.get_meta(f"crawl:{src.name}") or {}).get("page"),
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
        "SELECT ts, key, kind, price, message FROM alerts "
        # 🙈 privado: objetivos, stock y desaparecidos de lo que TÚ vigilas no van al feed público
        "WHERE kind NOT IN ('target', 'stock_out', 'stock_back', 'missing', 'back') AND key NOT LIKE 'url:%' "
        "ORDER BY id DESC LIMIT 150")]
    ev = active_event()
    return {
        "generated": datetime.now().isoformat(timespec="seconds"), "runner": config.RUNNER_NAME,
        "event": ev.name if ev else None,
        "upcoming": [{"name": e.name, "start": e.start.isoformat(), "end": e.end.isoformat(), "estimated": e.estimated}
                     for e in upcoming()],
        "categories": config.CATEGORY_LABELS,
        "errors": errors, "hall": hall, "ranking": ranking, "historyDays": history_days,
        "stores": stores, "products": products, "comparisons": comparisons[:300], "alerts": alerts,
        # 🚨 gangas agotadas que el radar relee seguido (aviso inmediato si vuelve el stock)
        "restock": [{"key": k, **{f: v.get(f) for f in ("name", "site", "price", "ref", "reason", "since", "url", "image")},
                     "store": SITE_LABELS.get(v.get("site"), v.get("site"))}
                    for k, v in sorted((store.get_meta("restock", {}) or {}).items(),
                                       key=lambda kv: kv[1]["price"] / max(kv[1]["ref"], 1))],
        "totals": {"products": len(products), "active": sum(1 for p in products if not p["missing"]),
                   "drops24": sum(1 for p in products if p["ch24"] and p["ch24"] < 0),
                   "godzillas": sum(1 for a in alerts if a["kind"] in ("godzilla", "price_error")),
                   "fakes": sum(1 for p in products if p["fake"]), "comparisons": len(comparisons),
                   "fresh60": sum(1 for p in products if not p["missing"] and p["last"] >= ago(minutes=60)),
                   "fresh24": sum(1 for p in products if not p["missing"] and p["last"] >= ago(hours=24))},
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
        "JOIN products p ON p.key=h.key WHERE h.key NOT LIKE 'url:%' ORDER BY h.ts").fetchall()
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


CORE_LIMIT = 2500


def _mark_gangas(products: list[dict]) -> None:
    """🔥 p["ganga"] = {"why", "ref"} si está muy bajo lo que cuesta el modelo NUEVO (misma regla que las alertas,
    calculada en memoria: 1 pasada en vez de 1 consulta por producto)."""
    new: dict[str, list[int]] = {}
    fams = {}
    for p in products:
        fam = family_key(p["group"], p["cat"]) if p["group"] and not p["missing"] else None
        fams[p["key"]] = fam
        if fam and not fam.endswith(" reacond"):
            new.setdefault(fam, []).append(p["price"])
    for p in products:
        p["ganga"] = None
        fam = fams[p["key"]]
        if not fam or any(b.startswith("Vendedor: ") for b in p["badges"] or []):
            continue
        refurb = fam.endswith(" reacond")
        refs = list(new.get(fam.removesuffix(" reacond"), []))
        if not refurb and p["price"] in refs:
            refs.remove(p["price"])                     # no compararse consigo mismo
        hit = bargain_vs(p["price"], refs, refurb)
        if hit:
            p["ganga"] = {"why": hit[0], "ref": hit[1]}


def _cat_counts(products: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in products:
        if not p["missing"]:
            out[p["cat"]] = out.get(p["cat"], 0) + 1
    return out


def core_products(data: dict) -> list[dict]:
    """Lo que la app necesita al abrir: ofertas, mínimos, sospechosas, comparador y errores (máx. CORE_LIMIT)."""
    needed = ({i["key"] for c in data["comparisons"] for i in c["items"]} | {e["key"] for e in data.get("errors", [])}
              | {r["key"] for r in data.get("restock", [])} | {p["key"] for p in data["products"] if p.get("ganga")})
    def score(p: dict) -> float:
        s = 0.0
        if p["key"] in needed:
            s += 1000
        if p["ch24"] is not None and p["ch24"] < 0:
            s += 500 - p["ch24"]
        if p["atMin"]:
            s += 300
        if p["fake"]:
            s += 200
        s += min(p["disc"] or 0, 90)
        return s
    ranked = sorted((p for p in data["products"] if not p["missing"]), key=score, reverse=True)
    return [p for p in ranked[:CORE_LIMIT] if score(p) > 0] or ranked[:300]


def build_site(out: Path = config.SITE_DIR) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    with PriceStore(config.DB_PATH) as store:
        data = build_data(store)
        export_files(data, store, out / "exports")
    dump = lambda obj: json.dumps(obj, ensure_ascii=False, separators=(",", ":"))   # noqa: E731
    # 📦 catálogo completo aparte (se carga solo al buscar / abrir "Todos"); data.json queda liviano
    (out / "products.json").write_text(dump(data["products"]), encoding="utf-8")
    core = dict(data, products=core_products(data), catCounts=_cat_counts(data["products"]), coreOnly=True)
    (out / "data.json").write_text(dump(core), encoding="utf-8")
    for f in TEMPLATE_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, out / f.name)
    shutil.copy2(godzilla.asset(), out / "godzilla.gif")
    # 🔐 respaldo de la BD: cifrado con DB_KEY (GitHub Secrets) porque la rama gh-pages es pública
    from backup import write_backup
    write_backup(config.DB_PATH, out / "data")
    (out / ".nojekyll").write_text("")
    # 🙈 que los buscadores no indexen el radar (solo lo ve quien tenga el link)
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    return out
