"""SQLite: catálogo, historial (solo cambios), ejecuciones, alertas y estado (pausas, resúmenes)."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from models import Product

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS products (
    key TEXT PRIMARY KEY, site TEXT NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL,
    brand TEXT, image TEXT, group_key TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    price INTEGER, list_price INTEGER, card_price INTEGER,
    in_stock INTEGER, stock_text TEXT, installments INTEGER, badges TEXT,
    missing_since TEXT, source TEXT
);
CREATE INDEX IF NOT EXISTS idx_products_group ON products(group_key);
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL,
    price INTEGER NOT NULL, list_price INTEGER, card_price INTEGER, ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prices_key_ts ON prices(key, ts);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, source TEXT NOT NULL, site TEXT NOT NULL,
    mode TEXT, ok INTEGER, n INTEGER, pages INTEGER, method TEXT, fallback INTEGER,
    blocked INTEGER, error TEXT, duration REAL, runner TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_source_ts ON runs(source, ts);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, key TEXT, kind TEXT NOT NULL,
    price INTEGER, message TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_key_kind ON alerts(key, kind, ts);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS errors (                      -- 🏆 salón de la fama de errores de precio
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, site TEXT, name TEXT, url TEXT, image TEXT,
    category TEXT, kind TEXT, ref_price INTEGER, error_price INTEGER, detected_ts TEXT NOT NULL,
    verdict TEXT, verify_note TEXT, outcome TEXT, ended_ts TEXT, last_price INTEGER, minutes REAL
);
CREATE INDEX IF NOT EXISTS idx_errors_key ON errors(key, detected_ts);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ago(**kw) -> str:
    return (datetime.now() - timedelta(**kw)).isoformat(timespec="seconds")


@dataclass
class Observation:
    product: Product
    previous: int | None            # último precio conocido (None = producto nuevo)
    previous_list: int | None
    historic_min: int | None        # mínimo histórico ANTES de esta lectura
    first_seen: str                 # ISO
    was_in_stock: bool | None
    was_missing: bool
    previous_card: int | None = None  # precio con tarjeta anterior

    @property
    def is_new(self) -> bool:
        return self.previous is None

    @property
    def delta(self) -> int:
        return 0 if self.previous is None else self.product.price - self.previous

    @property
    def pct(self) -> float:
        return 0.0 if not self.previous else self.delta / self.previous * 100

    @property
    def direction(self) -> str:
        if self.previous is None:
            return "new"
        return "down" if self.delta < 0 else "up" if self.delta > 0 else "same"


class PriceStore:
    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(products)")}
        if "category" not in cols:                                   # migración v2
            self.conn.execute("ALTER TABLE products ADD COLUMN category TEXT DEFAULT 'celulares'")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "PriceStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- productos
    def observe(self, p: Product, group_key: str, source: str, ts: str | None = None,
                category: str = "celulares") -> Observation:
        ts = ts or now_iso()
        c = self.conn
        row = c.execute("SELECT * FROM products WHERE key=?", (p.key,)).fetchone()
        hmin = c.execute("SELECT MIN(price) FROM prices WHERE key=?", (p.key,)).fetchone()[0]
        obs = Observation(
            p, row["price"] if row else None, row["list_price"] if row else None, hmin,
            row["first_seen"] if row else ts,
            None if not row or row["in_stock"] is None else bool(row["in_stock"]),
            bool(row and row["missing_since"]), row["card_price"] if row else None)
        changed = (not row or row["price"] != p.price or row["list_price"] != p.list_price
                   or row["card_price"] != p.card_price)
        c.execute(
            """INSERT INTO products(key, site, name, url, brand, image, group_key, first_seen, last_seen, price,
                   list_price, card_price, in_stock, stock_text, installments, badges, missing_since, source, category)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?)
               ON CONFLICT(key) DO UPDATE SET name=excluded.name, url=excluded.url,
                   brand=COALESCE(excluded.brand, products.brand), image=COALESCE(excluded.image, products.image),
                   group_key=excluded.group_key, last_seen=excluded.last_seen, price=excluded.price,
                   list_price=excluded.list_price, card_price=excluded.card_price, in_stock=excluded.in_stock,
                   stock_text=excluded.stock_text, installments=excluded.installments, badges=excluded.badges,
                   missing_since=NULL, source=excluded.source, category=excluded.category""",
            (p.key, p.site, p.name, p.url, p.brand, p.image, group_key, ts, ts, p.price, p.list_price,
             p.card_price, None if p.in_stock is None else int(p.in_stock), p.stock_text, p.installments,
             json.dumps(p.badges, ensure_ascii=False) if p.badges else None, source, category))
        if changed:   # historial compacto: solo se guarda cuando algo cambia
            c.execute("INSERT INTO prices(key, price, list_price, card_price, ts) VALUES (?,?,?,?,?)",
                      (p.key, p.price, p.list_price, p.card_price, ts))
        return obs

    def mark_missing(self, source: str, seen_keys: set[str], ts: str) -> list[sqlite3.Row]:
        """Productos de la fuente que no aparecieron en un escaneo con cobertura completa."""
        rows = self.conn.execute(
            "SELECT * FROM products WHERE source=? AND missing_since IS NULL", (source,)).fetchall()
        gone = [r for r in rows if r["key"] not in seen_keys]
        self.conn.executemany("UPDATE products SET missing_since=? WHERE key=?", [(ts, r["key"]) for r in gone])
        return gone

    def commit(self) -> None:
        self.conn.commit()

    # ---------------------------------------------------------------- consultas
    def history(self, key: str, since: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT ts, price, list_price, card_price FROM prices WHERE key=?"
        args: list = [key]
        if since:
            q += " AND ts>=?"
            args.append(since)
        return self.conn.execute(q + " ORDER BY ts", args).fetchall()

    def max_price_since(self, key: str, since: str) -> int | None:
        """Precio máximo vigente en la ventana (incluye el precio que regía al inicio)."""
        before = self.conn.execute("SELECT price FROM prices WHERE key=? AND ts<? ORDER BY ts DESC LIMIT 1",
                                   (key, since)).fetchone()
        inside = self.conn.execute("SELECT MAX(price) FROM prices WHERE key=? AND ts>=?", (key, since)).fetchone()[0]
        vals = [v for v in ((before[0] if before else None), inside) if v]
        return max(vals) if vals else None

    def group_prices(self, group_key: str, exclude_key: str) -> list[int]:
        return [r[0] for r in self.conn.execute(
            "SELECT price FROM products WHERE group_key=? AND key<>? AND missing_since IS NULL AND price>0",
            (group_key, exclude_key))]

    def products(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM products ORDER BY site, name").fetchall()

    # ---------------------------------------------------------------- ejecuciones y alertas
    def log_run(self, res, mode: str, runner: str, ts: str) -> None:
        self.conn.execute(
            """INSERT INTO runs(ts, source, site, mode, ok, n, pages, method, fallback, blocked, error, duration, runner)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, res.source.name, res.source.site, mode, int(res.ok), len(res.products), res.pages, res.method,
             int(res.fallback), int(res.blocked), res.error or res.skipped, round(res.duration, 2), runner))

    def recent_runs(self, source: str, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM runs WHERE source=? ORDER BY id DESC LIMIT ?",
                                 (source, limit)).fetchall()

    def alerted_recently(self, key: str, kind: str, price: int | None, hours: float) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM alerts WHERE key=? AND kind=? AND (price IS ? OR price=?) AND ts>=? LIMIT 1",
            (key, kind, price, price, ago(hours=hours))).fetchone()
        return row is not None

    def add_alert(self, key: str | None, kind: str, price: int | None, message: str, ts: str) -> None:
        self.conn.execute("INSERT INTO alerts(ts, key, kind, price, message) VALUES (?,?,?,?,?)",
                          (ts, key, kind, price, message))

    # ---------------------------------------------------------------- meta (estado persistente)
    def get_meta(self, k: str, default=None):
        row = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, k: str, v) -> None:
        self.conn.execute("INSERT INTO meta(k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                          (k, json.dumps(v)))
        self.conn.commit()

    # ---------------------------------------------------------------- 🏆 errores de precio
    def add_error(self, p, kind: str, ref_price: int | None, ts: str, verdict: str, note: str, category: str) -> int:
        cur = self.conn.execute(
            """INSERT INTO errors(key, site, name, url, image, category, kind, ref_price, error_price, detected_ts,
                                  verdict, verify_note, outcome, last_price)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'en curso',?)""",
            (p.key, p.site, p.name, p.url, p.image, category, kind, ref_price, p.price, ts, verdict, note, p.price))
        self.conn.commit()
        return cur.lastrowid

    def close_error(self, error_id: int, outcome: str, last_price: int | None, ts: str, minutes: float) -> None:
        self.conn.execute("UPDATE errors SET outcome=?, last_price=?, ended_ts=?, minutes=? WHERE id=?",
                          (outcome, last_price, ts, round(minutes, 1), error_id))
        self.conn.commit()

    def errors(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM errors ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def prune(self, days: int = 400) -> None:
        self.conn.execute("DELETE FROM runs WHERE ts<?", (ago(days=30),))
        self.conn.execute("DELETE FROM alerts WHERE ts<?", (ago(days=days),))
        self.conn.commit()
