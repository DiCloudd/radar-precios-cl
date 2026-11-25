"""📚 SoloTodo (api.solotodo.com): precios de las tiendas que bloquean a la nube (Ripley, Paris, Lider, MercadoLibre,
Tottus). API pública sin clave (verificado nov-2026, responde a las IPs de GitHub).

    /entities/?categories=<id>&stores=<id>&stores=<id>…&is_available=1&page_size=200&page=<n>

Cada oferta ("entity") trae su precio vigente (active_registry), la tienda, el link y el PRODUCTO canónico que
SoloTodo asoció a mano ("Apple iPhone 15 (128 GB / Black)"): usamos ese nombre, que compara mejor entre tiendas.

Precios: normal_price = precio público · offer_price = mejor medio de pago (normalmente la tarjeta de la tienda).
SoloTodo actualiza las tiendas grandes ~cada hora: por eso estas fuentes se leen 1 vez por hora (min_interval) y
cada producto lleva la etiqueta "vía SoloTodo" (sus errores de precio se avisan como "sin confirmar").

Respeto: ~20 peticiones por hora en total, separadas ≥4 s (fetcher). Si SoloTodo cae, el resto del radar sigue igual.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from models import PageResult, Product
from utils import parse_price

from .base import FetchContext, Scraper

BADGE = "vía SoloTodo"
PAGE_SIZE = 200
STALE_HOURS = 72          # precios más viejos no se usan (Lider se actualiza ~1 vez al día en SoloTodo)
ALERT_MAX_AGE_H = 24      # con más antigüedad se guardan (comparador) pero NO se avisan
# categoría de SoloTodo -> la nuestra (para descartar accesorios y productos mal categorizados)
CATEGORIES = {6: "celulares", 1: "notebooks", 11: "televisores", 33: "consolas", 14: "tablets", 50: "audifonos",
              48: "smartwatches", 4: "monitores", 15: "electro", 19: "electro"}
_SANITY = {"televisores": re.compile(r"\btv\b|televisor|smart|led|oled|qled|\d{2}\s*(\"|''|”|pulg)|"
                                     r"\b(?=\w*\d)(?=\w*[a-z])\w{6,}\b", re.I)}   # o un código de modelo (QN55Q60D)
# id de tienda en SoloTodo -> nuestro "site" (y etiqueta)
STORES = {18: ("ripley", "Ripley"), 11: ("paris", "Paris"), 43: ("lider", "Lider"),
          260: ("mercadolibre", "MercadoLibre"), 170: ("tottus", "Tottus")}
LABELS = {site: label for site, label in STORES.values()}


class SoloTodoScraper(Scraper):
    site, label = "solotodo", "SoloTodo"

    def page_url(self, base_url: str, n: int) -> str | None:
        sep = "&" if "?" in base_url else "?"
        url = base_url if "page_size=" in base_url else f"{base_url}{sep}page_size={PAGE_SIZE}"
        return url if n == 1 else f"{url}&page={n}"

    async def fetch(self, ctx: FetchContext, url: str) -> PageResult:
        data = await ctx.http.get(url, accept="application/json", as_json=True)
        return self.parse(json.dumps(data), url)

    def parse(self, text: str, url: str, now: datetime | None = None) -> PageResult:
        data = json.loads(text) or {}
        now = now or datetime.now(timezone.utc)
        from analysis import is_accessory
        cid = (parse_qs(urlsplit(url).query).get("categories") or ["0"])[0]
        cat = CATEGORIES.get(int(cid) if cid.isdigit() else 0, "")
        out = []
        for e in data.get("results") or []:
            reg = e.get("active_registry") or {}
            site = STORES.get(e.get("store"), (None,))[0]
            normal, offer = parse_price(reg.get("normal_price")), parse_price(reg.get("offer_price"))
            if not site or not normal or e.get("cell_plan") or not reg.get("is_available"):
                continue                               # tienda ajena, sin precio, con plan de celular o agotado
            try:
                age = (now - datetime.fromisoformat(reg["timestamp"].replace("Z", "+00:00"))).total_seconds() / 3600
            except (KeyError, ValueError):
                age = 0
            if age > STALE_HOURS:
                continue
            prod = e.get("product") or {}
            name = (prod.get("name") or e.get("name") or "").strip()
            # "Apple iPhone 15 (128 GB / Black)" -> "Apple iPhone 15 128 GB Black" (el agrupador ignora los paréntesis)
            name = re.sub(r"\s+", " ", name.replace("(", " ").replace(")", " ").replace(" / ", " ")).strip()
            if cat and (is_accessory(name, cat) or (cat in _SANITY and not _SANITY[cat].search(name))):
                continue                               # soportes, cables… o productos mal categorizados en SoloTodo
            cond = str(e.get("condition") or "")
            if "Refurbished" in cond or "Used" in cond:
                name += " (reacondicionado)"
            badges = [BADGE]
            if site == "mercadolibre" or e.get("seller"):
                badges.append(f"Vendedor: {e.get('seller') or 'MercadoLibre'}")   # marketplace: no es referencia
            pics = e.get("picture_urls") or []
            p = Product(
                f"{site}:st{e['id']}", site, name, e.get("external_url") or "", normal,
                card_price=offer if offer and offer < normal else None, brand=name.split(" ")[0] if name else None,
                image=pics[0] if pics else None, in_stock=True,
                stock_text=f"vía SoloTodo · hace {age:.0f} h" if age >= 1 else "vía SoloTodo · <1 h",
                badges=badges)
            p.age_h = age                              # antigüedad del precio (no se avisa si es > ALERT_MAX_AGE_H)
            out.append(p)
        count = data.get("count")
        total = -(-int(count) // PAGE_SIZE) if count else None
        return PageResult(out, "API SoloTodo /entities", total)
