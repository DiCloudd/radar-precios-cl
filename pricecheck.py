"""📚 Segunda opinión con el historial público de SoloTodo antes de avisar una bajada.

Por qué: el radar existe desde nov-2026 y en Black Friday las tiendas suben precios días antes para luego "bajarlos".
SoloTodo (api.solotodo.com, API pública sin clave) guarda el historial por tienda desde hace meses: con eso sabemos
si el "antes $X" del aviso era real y si un "mínimo histórico" lo es de verdad.

Uso respetuoso: solo para avisos (pocos al día), máx. CHECKS_PER_BATCH por tanda, respuestas en caché 7 días,
y el fetcher ya separa las peticiones por dominio (≥4 s). Si SoloTodo no responde o no encuentra el producto,
el aviso sigue su curso con las reglas propias (nunca se pierde un aviso por esto).
"""
from __future__ import annotations

import logging
import re
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import config
from matcher import _ascii
from storage import PriceStore

log = logging.getLogger("pricecheck")

API = "https://api.solotodo.com"
ENABLED = config._bool("SOLOTODO_CHECK", True)
CHECKS_PER_BATCH = 6
CACHE_DAYS = 7
# ids de tienda en SoloTodo (verificado nov-2026, /stores/?country=1)
STORES = {"falabella": [9, 8147], "abc": [8015, 5, 30], "hites": [87], "pcfactory": [12], "lenovo": [199],
          "entel": [38, 181], "samsung": [223], "apple": [6365], "xiaomi": [8411, 6794], "paris": [11],
          "lider": [43], "maconline": [], "motorola": [], "multicentro": []}
_STOP = {"de", "con", "y", "el", "la", "para", "en", "gb", "ram", "ssd", "win", "11", "smart", "tv", "notebook", "gamer",
         "celular", "smartphone", "liberado", "negro", "blanco", "gris", "azul", "plata", "reacondicionado", "nuevo",
         "bluetooth", "wifi", "fi", "wi", "silver", "black", "white", "gray", "grey", "grafito", "monitor", "plano"}


@dataclass
class History:
    product: str
    usual: int            # precio habitual: mediana de los mínimos diarios (90 días, sin los últimos 3)
    min90: int            # mínimo de 90 días (sin los últimos 3 días)
    min_date: str


def _tokens(name: str) -> set[str]:
    txt = _ascii(name or "").lower()
    txt = re.sub(r"(\d+)\s+(mm|gb|tb|kg|l|lts|hz|pulgadas)\b", r"\1\2", txt)      # "40 Mm" == "40mm"
    words = re.findall(r"[a-z0-9]+", txt)
    return {w for w in words if w not in _STOP and len(w) >= 2}


def _query(name: str, brand: str | None) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9+]+", _ascii(name or "")) if w.lower() not in _STOP]
    q = words[:6]
    if brand and brand.lower() not in " ".join(q).lower():
        q = [brand] + q[:5]
    return " ".join(q)


class PriceChecker:
    def __init__(self, ctx) -> None:
        self.ctx = ctx

    async def _get(self, path: str, **params):
        url = f"{API}/{path}" + ("?" + urlencode(params, doseq=True) if params else "")
        return await self.ctx.http.get(url, accept="application/json", as_json=True, retries=2)

    async def _find_product(self, p) -> tuple[int, str] | None:
        q = _query(p.name, p.brand)
        mine = _tokens(p.name)
        cands = []
        stores = STORES.get(p.site) or []
        if stores:
            res = await self._get("entities/", stores=stores, search=q, page_size=8)
            cands += [(e["product"]["id"], e["product"]["name"], e["name"]) for e in res.get("results", []) if e.get("product")]
        if not cands:
            res = await self._get("products/", search=q, page_size=5)
            cands += [(x["id"], x["name"], x["name"]) for x in res.get("results", [])]
        best, score = None, 0.0
        for pid, pname, ename in cands:
            theirs = _tokens(pname) | _tokens(ename)
            s = len(mine & theirs) / max(len(mine), 1)
            if s > score:
                best, score = (pid, pname), s
        return best if best and score >= 0.6 else None

    async def history(self, store: PriceStore, p) -> History | None:
        from analysis import REFURB
        if not ENABLED or REFURB.search(p.name or ""):
            return None          # reacondicionados: SoloTodo los mezcla con el equipo nuevo (auditado: iPhone SE 3)
        key = f"st:{p.key}"
        cached = store.get_meta(key)
        if cached and cached.get("t", 0) > time.time() - CACHE_DAYS * 86400:
            return History(**cached["h"]) if cached.get("h") else None
        h = None
        try:
            found = await self._find_product(p)
            if found:
                pid, pname = found
                ents = (await self._get("entities/", products=pid)).get("results", [])
                stores = set(STORES.get(p.site) or [])
                ents = sorted(ents, key=lambda e: (e["store"] not in stores, not e.get("active_registry")))[:3]
                since = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
                cut = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
                daily: dict[str, int] = {}
                for e in ents:
                    for r in await self._get(f"entities/{e['id']}/pricing_history/", timestamp_after=since):
                        day = r["timestamp"][:10]
                        if r.get("is_available") and r.get("normal_price") and day < cut:
                            v = int(float(r["normal_price"]))
                            daily[day] = min(v, daily.get(day, v))
                if len(daily) >= 10:
                    vals = list(daily.values())
                    mday = min(daily, key=daily.get)
                    h = History(pname[:80], int(statistics.median(vals)), min(vals), mday)
        except Exception as exc:  # noqa: BLE001 - la segunda opinión nunca bloquea un aviso
            log.info("SoloTodo no disponible para %s: %s", p.key, str(exc)[:100])
            return None
        store.set_meta(key, {"t": time.time(), "h": h.__dict__ if h else None})
        return h
