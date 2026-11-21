"""PCFactory: SPA Vue. API JSON pública que usa su propia web (verificado nov-2026):
api.pcfactory.cl/pcfactory-services-catalogo/v1/catalogo/productos/query?page=<0..>&size=<<=48>&categorias=<id>
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

from models import PageResult, Product
from utils import parse_price, with_query

from .base import FetchContext, Scraper

API = "https://api.pcfactory.cl/pcfactory-services-catalogo/v1/catalogo/productos/query?page={page}&size=48&categorias={cat}"
CATEGORY_IDS = {"smartphones": 5, "notebooks": 425, "smart-tv": 789, "consolas": 438}   # agrega aquí otras categorías (o usa ?categorias=<id> en la URL)


class PCFactoryScraper(Scraper):
    site, label = "pcfactory", "PCFactory"

    def _category(self, base_url: str) -> int:
        q = parse_qs(urlsplit(base_url).query)
        if "categorias" in q:
            return int(q["categorias"][0])
        slug = urlsplit(base_url).path.rstrip("/").rsplit("/", 1)[-1]
        if slug not in CATEGORY_IDS:
            raise ValueError(f"categoría PCFactory desconocida '{slug}': agrega ?categorias=<id> a la URL")
        return CATEGORY_IDS[slug]

    def page_url(self, base_url: str, n: int) -> str | None:
        self._human_base = base_url
        url = API.format(page=n - 1, cat=self._category(base_url))
        sort = parse_qs(urlsplit(base_url).query).get("sort")   # ⚡ ?sort=precio,asc -> más baratos primero
        return with_query(url, sort=sort[0]) if sort else url

    async def fetch(self, ctx: FetchContext, url: str) -> PageResult:
        data = await ctx.http.get(url, accept="application/json, text/plain, */*", as_json=True)
        return self.parse(json.dumps(data), url)

    def parse(self, text: str, url: str) -> PageResult:
        content = (json.loads(text) or {}).get("content") or {}
        total = (content.get("pageable") or {}).get("totalPages")
        out = []
        for it in content.get("items") or []:
            precio = it.get("precio") or {}
            efectivo, normal, ref = (parse_price(precio.get(k)) for k in ("efectivo", "normal", "referencia"))
            price = efectivo or normal
            if not price or not it.get("id"):
                continue
            stock = str(it.get("stock") or "").strip()
            out.append(Product(
                key=f"pcfactory:{it['id']}", site=self.site, name=(it.get("nombre") or "").strip(),
                url=f"https://www.pcfactory.cl/producto/{it.get('slug') or it['id']}", price=price,
                list_price=ref if ref and ref > price else None, brand=it.get("marca"),
                image=f"https://assets.pcfactory.cl{it['thumbnail']}" if it.get("thumbnail") else None,
                in_stock=bool(stock) and stock not in ("0", "-"), stock_text=f"{stock} unid." if stock else None,
                badges=["Outlet"] if it.get("outlet") else []))
        return PageResult(out, "API catalogo/productos/query", int(total) if total else None)

    async def browser_fetch(self, ctx: FetchContext, url: str) -> PageResult | None:
        # Respaldo: abre la página real de la categoría y lee la respuesta de la API que hace la propia web.
        m = re.search(r"page=(\d+).*categorias=(\d+)", url)
        page_n = int(m.group(1)) + 1 if m else 1
        human = with_query(getattr(self, "_human_base", url), size=48, page=page_n)
        bctx = await ctx.browser.context()
        payloads: list[str] = []
        try:
            page = await bctx.new_page()

            async def on_response(resp):
                if "/catalogo/productos/query?" in resp.url and resp.status == 200:
                    try:
                        payloads.append(await resp.text())
                    except Exception:  # noqa: BLE001
                        pass
            page.on("response", on_response)
            await page.goto(human, wait_until="domcontentloaded", timeout=60000)
            for _ in range(20):
                if payloads:
                    break
                await page.wait_for_timeout(500)
        finally:
            await bctx.close()
        if not payloads:
            return None
        res = self.parse(payloads[-1], url)
        res.fallback, res.method = True, "navegador · API interceptada"
        return res
