"""Paris: JSON-LD WebPage.mainEntity (ItemList) + tarjetas [data-cnstrc-item-id] (verificado nov-2026).

Paris corta conexiones al azar (ERR_CONNECTION_RESET) y en Chromium falla con HTTP/2:
la vía HTTP usa HTTP/1.1 con reintentos; el navegador de respaldo se lanza con --disable-http2.
"""
from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from models import PageResult, Product
from utils import absolute, json_ld_nodes, ld_item_list, ld_offer_prices, parse_price, prices_in_text

from .base import Scraper


class ParisScraper(Scraper):
    site, label = "paris", "Paris"
    browser_args = ("--disable-http2",)
    http_retries = 8   # Paris resetea ~50% de las conexiones nuevas; suele pasar al 2do-4to intento

    def parse(self, text: str, url: str) -> PageResult:
        cards = {}
        for node in HTMLParser(text).css("[data-cnstrc-item-id]"):
            a = node.css_first('a[href*=".html"]')
            cards[node.attributes.get("data-cnstrc-item-id")] = {
                "name": node.attributes.get("data-cnstrc-item-name") or "",
                "price": parse_price(node.attributes.get("data-cnstrc-item-price")),
                "text": node.text(separator=" "),
                "url": absolute(url, a.attributes.get("href")) if a else "",
            }
        out: list[Product] = []
        items = ld_item_list(json_ld_nodes(text))
        for it in items:
            prices = ld_offer_prices(it)
            link = it.get("url") or ""
            m = re.search(r"-([A-Z0-9]+)\.html", link)
            pid = m.group(1) if m else (it.get("sku") or "").split("-")[0]
            if prices and pid:
                out.append(self._build(pid, it.get("name", ""), link, min(prices),
                                       (it.get("brand") or {}).get("name"), it.get("image"), cards.get(pid)))
        if out:
            return PageResult(out, "JSON-LD ItemList + tarjetas")
        for pid, c in cards.items():
            if pid and c["price"]:
                out.append(self._build(pid, c["name"], c["url"], c["price"], None, None, c))
        return PageResult(out, "DOM data-cnstrc-item-price", fallback=True)

    def _build(self, pid, name, link, price, brand, image, card) -> Product:
        list_price = card_price = None
        if card:
            nums = prices_in_text(card["text"])
            if nums and max(nums) > price:
                list_price = max(nums)
            if nums and min(nums) < price:
                card_price = min(nums)
        return Product(f"paris:{pid}", self.site, name.strip(), link, price, list_price, card_price, brand,
                       image if isinstance(image, str) else None)
