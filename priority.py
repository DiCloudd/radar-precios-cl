"""⭐ Productos PRIORITARIOS: vigilados en todas las tiendas, con aviso inmediato si caen a un precio ridículo
o hay un posible error de precio.

La lista es PRIVADA: vive en el secreto de GitHub PRIORITY_JSON (o en data/priority.json en local), nunca en el
repo público. Formato (lista JSON):
    [{"label": "DJI Osmo Pocket 4P Standard Combo", "match": "osmo pocket 4p", "exclude": "creator", "ref": 769990},
     {"label": "Samsung Galaxy S26 Ultra", "match": "galaxy s26 ultra"}]          # sin "ref": se usa el catálogo
  match / exclude: expresiones regulares sobre el nombre (sin tildes, minúsculas).
  ref: precio de referencia (el normal de hoy). Sin ref se calcula con la mediana del catálogo (tiendas, sin
       revendedores, sin reacondicionados).

Niveles (sobre la referencia):
  * ≤ ERROR_RATIO (55 %)   -> 💥 ⭐ ¿ERROR DE PRECIO?  (verificación + GIF + modo caza, como los errores)
  * ≤ DEAL_RATIO (85 %)    -> ⭐ PRIORIDAD: precio muy bajo (al instante, con foto)
Los reacondicionados, accesorios y revendedores del marketplace no cuentan.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import time
from dataclasses import dataclass

import config
from analysis import REFURB, Alert, clp, is_accessory
from matcher import _ascii

ERROR_RATIO = config._float("PRIORITY_ERROR_RATIO", 0.55)
DEAL_RATIO = config._float("PRIORITY_DEAL_RATIO", 0.85)
REF_TTL = 1800


@dataclass
class Priority:
    label: str
    match: re.Pattern
    exclude: re.Pattern | None
    ref: int | None

    def matches(self, name: str) -> bool:
        n = _ascii(name or "").lower()
        return bool(self.match.search(n)) and not (self.exclude and self.exclude.search(n))


def load() -> list[Priority]:
    raw = os.getenv("PRIORITY_JSON") or ""
    path = config.DATA_DIR / "priority.json"
    if not raw and path.exists():
        raw = path.read_text(encoding="utf-8")
    try:
        items = json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError:
        return []
    return _parse(items)


PRIORITIES: list[Priority] = []        # se carga al final del módulo
_refs: dict[str, tuple[float, int | None]] = {}


def _eligible(name: str, category: str, badges) -> bool:
    return not (REFURB.search(name or "") or is_accessory(name, category)
                or any(str(b).startswith("Vendedor: ") for b in badges or []))


def reference(store, pr: Priority) -> int | None:
    """Precio de referencia: el del usuario o la mediana del catálogo (se recalcula cada 30 min)."""
    if pr.ref:
        return pr.ref
    hit = _refs.get(pr.label)
    if hit and hit[0] > time.time() - REF_TTL:
        return hit[1]
    words = [w for w in re.findall(r"[a-z0-9]+", pr.match.pattern.lower()) if len(w) >= 3][:2]
    rows = store.conn.execute(
        "SELECT name, price, category, badges FROM products WHERE missing_since IS NULL AND price>0 AND "
        + " AND ".join("LOWER(name) LIKE ?" for _ in words), [f"%{w}%" for w in words]).fetchall() if words else []
    prices = [r["price"] for r in rows if pr.matches(r["name"])
              and _eligible(r["name"], r["category"] or "", json.loads(r["badges"]) if r["badges"] else [])]
    ref = int(statistics.median(prices)) if len(prices) >= 3 else None
    _refs[pr.label] = (time.time(), ref)
    return ref


def from_text(text: str, ref: int | None = None) -> dict:
    """"/prioridad osmo pocket 4p" -> entrada flexible: las palabras en orden, "4p" también calza "4 Pro"."""
    words = re.findall(r"[a-z0-9]+", _ascii(text).lower())
    parts = []
    for w in words:
        m = re.fullmatch(r"(\d+)([a-z]+)", w)
        parts.append(rf"{m.group(1)}\s*{re.escape(m.group(2))}" if m else re.escape(w))
    return {"label": " ".join(words), "match": r".*?".join(parts), "ref": ref}


def _parse(items) -> list[Priority]:
    out = []
    for i in items or []:
        try:
            if i.get("match"):
                out.append(Priority(i["label"], re.compile(i["match"], re.I),
                                    re.compile(i["exclude"], re.I) if i.get("exclude") else None,
                                    int(i["ref"]) if i.get("ref") else None))
        except (re.error, KeyError, ValueError):
            continue
    return out


def all_priorities(store) -> list[Priority]:
    """Los del secreto PRIORITY_JSON + los agregados desde Telegram (/prioridad, guardados en la BD cifrada)."""
    try:
        mine = store.get_meta("tg:priority", [])
    except Exception:  # noqa: BLE001
        mine = []
    return PRIORITIES + _parse(mine)


def check(store, obs, category: str, priorities: list[Priority] | None = None) -> list[Alert]:
    p = obs.product
    pris = all_priorities(store) if priorities is None else priorities
    if not pris or not (obs.is_new or obs.direction == "down" or (obs.was_in_stock is False and p.in_stock)):
        return []
    if not _eligible(p.name, category, p.badges) or p.in_stock is False:
        return []
    for pr in pris:
        if not pr.matches(p.name):
            continue
        ref = reference(store, pr)
        if ref:
            ratio = p.price / ref
            save = f"{(1 - ratio) * 100:.0f}% bajo su precio de referencia ({clp(ref)})"
            if ratio <= ERROR_RATIO:
                return [Alert("price_error", obs, "💥⭐ PRIORIDAD: ¿ERROR DE PRECIO?", [f"⭐ {pr.label}", save], 0, ref)]
            if ratio <= DEAL_RATIO:
                return [Alert("priority", obs, "⭐🔥 PRIORIDAD: PRECIO MUY BAJO", [f"⭐ {pr.label}", save], 0, ref)]
        # ⭐📉 cualquier bajada real de un prioritario (no solo las ridículas), en cualquier tienda
        if obs.direction == "down" and -obs.pct >= config.MIN_CHANGE_PCT:
            return [Alert("priority", obs, "⭐📉 PRIORIDAD: BAJÓ DE PRECIO",
                          [f"⭐ {pr.label}", f"Bajó {-obs.pct:.0f}% (antes {clp(obs.previous)})"], 1, ref)]
        return []
    return []


PRIORITIES = load()
