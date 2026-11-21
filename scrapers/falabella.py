"""Falabella: Next.js. Datos en #__NEXT_DATA__ -> props.pageProps.results[] (verificado nov-2026)."""
from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from models import PageResult, Product
from utils import ld_item_list, ld_offer_prices, json_ld_nodes, next_data, parse_price, with_query

from .base import Scraper

PUBLIC_TYPES = ("eventPrice", "internetPrice", "normalPrice")


class FalabellaScraper(Scraper):
    site, label = "falabella", "Falabella"

    def page_url(self, base_url: str, n: int) -> str | None:
        return base_url if n == 1 else with_query(base_url, page=n)

    def parse(self, text: str, url: str) -> PageResult:
        nd = next_data(text)
        props = ((nd or {}).get("props") or {}).get("pageProps") or {}
        total = None
        pag = props.get("pagination") or {}
        if pag.get("count") and pag.get("perPage"):
            total = -(-int(pag["count"]) // int(pag["perPage"]))
        products = [p for p in (self._from_result(r) for r in props.get("results") or []) if p]
        if products:
            return PageResult(products, "__NEXT_DATA__", total)

        products = []
        for item in ld_item_list(json_ld_nodes(text)):
            prices = sorted(ld_offer_prices(item))
            m = re.search(r"/product/(\d+)", item.get("url", ""))
            if prices and m:
                products.append(Product(
                    f"falabella:{m.group(1)}", self.site, item.get("name", "").strip(), item["url"],
                    price=prices[1] if len(prices) > 2 else prices[0],
                    list_price=prices[-1] if len(prices) > 1 else None,
                    brand=(item.get("brand") or {}).get("name"), image=item.get("image")))
        if products:
            return PageResult(products, "JSON-LD ItemList", total, fallback=True)
        return PageResult(self._from_dom(text), "DOM data-*-price", total, fallback=True)

    def _from_result(self, r: dict) -> Product | None:
        typed: dict[str, int] = {}
        crossed: list[int] = []
        for p in r.get("prices") or []:
            val = parse_price(p.get("price"))
            if not val:
                continue
            if p.get("crossed"):
                crossed.append(val)
            else:
                typed[p.get("type", "")] = val
        public = [typed[t] for t in PUBLIC_TYPES if t in typed]
        card = typed.get("cmrPrice")
        price = min(public) if public else card
        pid = r.get("productId") or r.get("skuId")
        if not price or not pid:
            return None
        badges = []
        for b in (r.get("badges") or []) + (r.get("multipurposeBadges") or []):
            if isinstance(b, dict) and (b.get("label") or b.get("value")):
                badges.append(str(b.get("label") or b.get("value")))
        media = r.get("mediaUrls") or []
        name = (r.get("displayName") or "").strip()
        seller = (r.get("sellerName") or "").strip()
        if seller and seller.upper() != "FALABELLA":
            badges.insert(0, f"Vendedor: {seller}")       # marketplace: no lo vende Falabella
            if re.search(r"renov|reacond|refurb|seminuev|usad", seller, re.I) and not re.search(r"reacond", name, re.I):
                name += " (Reacondicionado)"
        return Product(
            key=f"falabella:{pid}", site=self.site, name=name,
            url=r.get("url") or "", price=price, list_price=max(crossed) if crossed else None,
            card_price=card if card and card < price else None, brand=r.get("brand"),
            image=media[0] if media else None, badges=badges[:4])

    def _from_dom(self, text: str) -> list[Product]:
        out = []
        for a in HTMLParser(text).css("a[data-pod]"):
            pid = a.attributes.get("data-key")
            def dp(attr: str) -> int | None:
                node = a.css_first(f"[{attr}]")
                return parse_price(node.attributes.get(attr)) if node else None
            public = [p for p in (dp("data-event-price"), dp("data-internet-price")) if p]
            normal, cmr = dp("data-normal-price"), dp("data-cmr-price")
            price = min(public) if public else normal
            img = a.css_first("img[alt]")
            if pid and price:
                out.append(Product(f"falabella:{pid}", self.site, img.attributes.get("alt", "") if img else "",
                                   (a.attributes.get("href") or "").split("?")[0], price,
                                   list_price=normal if normal and normal > price else None,
                                   card_price=cmr if cmr and cmr < price else None))
        return out
