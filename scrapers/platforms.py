"""Extractores por plataforma (verificados desde IPs de GitHub, nov-2026):

* VTEX      -> API pública de catálogo /api/catalog_system/pub/products/search   (Motorola Chile)
* Shopify   -> /collections/<c>/products.json                                    (Multicentro)
* JSON-LD   -> ItemList de la página de listado                                  (Xiaomi Tienda Oficial)
* Spree     -> JSON-LD Product de cada modelo enlazado desde la portada         (MacOnline, Apple Premium Partner)
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from models import PageResult, Product
from utils import absolute, json_ld_nodes, ld_in_stock, ld_item_list, ld_offer_prices, parse_price, with_query

from .base import FetchContext, Scraper


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


# ---------------------------------------------------------------- VTEX
class MotorolaScraper(Scraper):
    """URL de la fuente = API de búsqueda con el filtro de categoría (fq=C:/1/ = Smartphones)."""
    site, label = "motorola", "Motorola Store"
    PAGE = 50

    def page_url(self, base_url: str, n: int) -> str | None:
        return with_query(base_url, _from=(n - 1) * self.PAGE, _to=n * self.PAGE - 1)

    async def fetch(self, ctx: FetchContext, url: str) -> PageResult:
        data = await ctx.http.get(url, accept="application/json", as_json=True)
        return self.parse(json.dumps(data), url)

    def parse(self, text: str, url: str) -> PageResult:
        out = []
        for p in json.loads(text) or []:
            items = p.get("items") or []
            offers = [(s.get("commertialOffer") or {}) for it in items for s in it.get("sellers") or []]
            avail = [o for o in offers if o.get("IsAvailable") and o.get("Price")]
            best = min(avail or offers, key=lambda o: o.get("Price") or 9e12, default={})
            price = parse_price(best.get("Price"))
            if not price or not p.get("productId"):
                continue
            lst = parse_price(best.get("ListPrice"))
            cuotas = max((i.get("NumberOfInstallments", 0) for i in best.get("Installments") or []
                          if not i.get("InterestRate")), default=0)
            img = ((items[0].get("images") or [{}])[0].get("imageUrl")) if items else None
            out.append(Product(
                f"motorola:{p['productId']}", self.site, f"Motorola {p.get('productName', '').strip()}".replace("Motorola motorola", "Motorola"),
                p.get("link") or absolute(url, f"/{p.get('linkText')}/p"), price,
                list_price=lst if lst and lst > price else None, brand=p.get("brand") or "Motorola", image=img,
                in_stock=bool(avail), stock_text=f"{int(best.get('AvailableQuantity') or 0)} unid." if avail else "Agotado",
                installments=cuotas if cuotas > 1 else None))
        return PageResult(out, "VTEX catalog API", fallback=False)


# ---------------------------------------------------------------- Shopify
class MulticentroScraper(Scraper):
    """URL de la fuente = /collections/<colección>."""
    site, label = "multicentro", "Multicentro"

    def page_url(self, base_url: str, n: int) -> str | None:
        return f"{base_url.rstrip('/')}/products.json?limit=250&page={n}"

    async def fetch(self, ctx: FetchContext, url: str) -> PageResult:
        data = await ctx.http.get(url, accept="application/json", as_json=True)
        return self.parse(json.dumps(data), url)

    def parse(self, text: str, url: str) -> PageResult:
        out, origin = [], _origin(url)
        for p in (json.loads(text) or {}).get("products") or []:
            variants = p.get("variants") or []
            avail = [v for v in variants if v.get("available")]
            best = min(avail or variants, key=lambda v: parse_price(v.get("price")) or 9e12, default=None)
            price = parse_price(best.get("price")) if best else None
            if not price:
                continue
            cmp_ = parse_price(best.get("compare_at_price"))
            img = (p.get("images") or [{}])[0].get("src")
            out.append(Product(
                f"multicentro:{p['id']}", self.site, (p.get("title") or "").strip(), f"{origin}/products/{p['handle']}",
                price, list_price=cmp_ if cmp_ and cmp_ > price else None, brand=p.get("vendor"), image=img,
                in_stock=bool(avail)))
        return PageResult(out, "Shopify products.json")


# ---------------------------------------------------------------- JSON-LD ItemList
class XiaomiScraper(Scraper):
    site, label = "xiaomi", "Xiaomi Store"

    def parse(self, text: str, url: str) -> PageResult:
        out = []
        for it in ld_item_list(json_ld_nodes(text)):
            prices = ld_offer_prices(it)
            link = it.get("url") or ""
            slug = link.rstrip("/").rsplit("/", 1)[-1]
            if not prices or not slug:
                continue
            name = (it.get("name") or "").strip()
            if not name.lower().startswith("xiaomi"):
                name = f"Xiaomi {name}"
            out.append(Product(f"xiaomi:{slug}", self.site, f"{name} (desde)", link, min(prices), brand="Xiaomi",
                               image=it.get("image") if isinstance(it.get("image"), str) else None,
                               in_stock=ld_in_stock(it)))
        return PageResult(out, "JSON-LD ItemList")


# ---------------------------------------------------------------- Spree (MacOnline)
class MacOnlineScraper(Scraper):
    """Portada -> enlaces /products/iphone-* -> JSON-LD Product (una entrada por color/capacidad).
    Agrupa por modelo+capacidad y guarda el color más barato. ~10 peticiones: conviene fast=false."""
    site, label = "maconline", "MacOnline"
    MAX_MODELS = 14

    async def fetch(self, ctx: FetchContext, url: str) -> PageResult:
        kind = urlsplit(url).fragment or "iphone"            # https://www.maconline.com/#macbook -> MacBooks
        home = await ctx.http.get(url.split("#")[0])
        links = list(dict.fromkeys(re.findall(rf'href="(/products/{re.escape(kind)}[^"?#]*)"', home)))[: self.MAX_MODELS]
        out: list[Product] = []
        for href in links:
            try:
                out += self.parse(await ctx.http.get(absolute(url, href)), absolute(url, href)).products
            except Exception:  # noqa: BLE001 - un modelo caído no tumba los demás
                continue
        return PageResult(out, "JSON-LD Product por modelo")

    def parse(self, text: str, url: str) -> PageResult:
        best: dict[str, Product] = {}
        for node in json_ld_nodes(text):
            if node.get("@type") != "Product":
                continue
            prices = ld_offer_prices(node)
            name = (node.get("name") or "").strip()
            if not prices or not name:
                continue
            model = re.sub(r"\s+-\s+[^-]+$", "", name)           # "iPhone 17 de 256 GB - Negro" -> sin color
            if not model.lower().startswith(("iphone", "macbook", "imac", "ipad", "mac")):
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
            img = node.get("image")
            p = Product(f"maconline:{slug}", self.site, f"Apple {model}", url, min(prices), brand="Apple",
                        image=img[0] if isinstance(img, list) and img else img if isinstance(img, str) else None,
                        in_stock=ld_in_stock(node))
            if slug not in best or p.price < best[slug].price:
                best[slug] = p
        return PageResult(list(best.values()), "JSON-LD Product")
