"""Lenovo Chile (tienda oficial). Su web pide los productos a una API JSON pública (verificado nov-2026):

    openapi.lenovo.com/cl/es/ofp/search/dlp/product/query/get/_tsc?pageFilterId=<id>&params=<json url-encoded 2 veces>

La API exige el Referer de la página de la tienda (como cualquier navegador); sin él responde 403.
En sources.toml la URL es la página humana + el pageFilterId en el fragmento:
    https://www.lenovo.com/cl/es/notebooks/#7ca29953-e3fd-48a9-8c04-b790cfef3842
"""
from __future__ import annotations

import json
from urllib.parse import quote, urlsplit

from models import PageResult, Product
from utils import parse_price

from .base import FetchContext, Scraper

API = "https://openapi.lenovo.com/cl/es/ofp/search/dlp/product/query/get/_tsc?pageFilterId={pf}&params={params}"
PAGE_SIZE = 40


class LenovoScraper(Scraper):
    site, label = "lenovo", "Lenovo"

    def page_url(self, base_url: str, n: int) -> str | None:
        parts = urlsplit(base_url)
        pf = parts.fragment
        if not pf:
            raise ValueError("URL de Lenovo sin pageFilterId en el fragmento (#...)")
        params = {"classificationGroupIds": "400001", "pageFilterId": pf, "facets": [], "page": str(n),
                  "pageSize": PAGE_SIZE, "init": True, "sorts": ["priceUp"], "version": "v2"}
        enc = quote(quote(json.dumps(params, separators=(",", ":"))))
        page = f"{parts.scheme}://{parts.netloc}{parts.path}"
        return API.format(pf=pf, params=enc) + "#" + quote(page, safe=":/")   # la página viaja como Referer

    async def fetch(self, ctx: FetchContext, url: str) -> PageResult:
        api, _, referer = url.partition("#")
        from urllib.parse import unquote
        data = await ctx.http.get(api, accept="application/json, text/plain, */*", as_json=True,
                                  referer=unquote(referer) or "https://www.lenovo.com/cl/es/")
        return self.parse(json.dumps(data), url)

    def parse(self, text: str, url: str) -> PageResult:
        data = (json.loads(text) or {}).get("data") or {}
        out = []
        for group in data.get("data") or []:
            for it in group.get("products") or []:
                price = parse_price(it.get("finalPrice"))
                code = it.get("productCode")
                if not price or not code:
                    continue
                web = parse_price(it.get("webPrice"))
                summary = (it.get("summary") or "").strip()
                cfg = (it.get("productName") or "").replace("NB ", "", 1).removesuffix(" 11S").strip()
                name = summary if summary.lower().startswith("lenovo") else f"Lenovo {summary}"
                name = f"{name} · {cfg} · {code}" if cfg else f"{name} · {code}"
                img = ((it.get("media") or {}).get("thumbnail") or {}).get("imageAddress")
                in_stock = it.get("inventoryStatus") == 1 and bool(it.get("purchaseFlag"))
                badges = ["Outlet"] if it.get("isOutlet") else []
                out.append(Product(
                    f"lenovo:{code}", self.site, name, "https://www.lenovo.com/cl/es" + (it.get("url") or ""), price,
                    list_price=web if web and web > price else None, brand="Lenovo",
                    image=("https:" + img) if img and img.startswith("//") else img, in_stock=in_stock,
                    stock_text=it.get("marketingStatus"), badges=badges))
        total = data.get("pageCount")
        return PageResult(out, "API ofp/search (JSON)", int(total) if total else None)
