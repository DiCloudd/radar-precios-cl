"""Tiendas oficiales de marca: Samsung (JSON-LD ItemList) y Apple (tarjetas "Desde $X"). Verificado nov-2026."""
from __future__ import annotations

import html
import re

from models import PageResult, Product
from utils import json_ld_nodes, ld_in_stock, ld_item_list, ld_offer_prices

from .base import Scraper


class SamsungScraper(Scraper):
    site, label = "samsung", "Samsung Store"

    def parse(self, text: str, url: str) -> PageResult:
        out = []
        for it in ld_item_list(json_ld_nodes(text)):
            link = it.get("url") or it.get("@id") or ""
            prices = ld_offer_prices(it)
            mc = re.search(r"modelCode=([A-Z0-9-]+)", link)
            code = mc.group(1) if mc else link.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
            if not prices or not code:
                continue
            storage = re.search(r"-(\d+(?:gb|tb))-", link)
            name = it.get("name", "").strip()
            if storage and storage.group(1).lower() not in name.lower():
                name = f"{name} {storage.group(1).upper()}"
            out.append(Product(f"samsung:{code}", self.site, f"Samsung {name}", link, min(prices),
                               brand="Samsung", image=it.get("image") if isinstance(it.get("image"), str) else None,
                               in_stock=ld_in_stock(it)))
        return PageResult(out, "JSON-LD ItemList")


class AppleScraper(Scraper):
    """Precio base ("Desde") de cada modelo en apple.com/cl/shop/buy-iphone."""
    site, label = "apple", "Apple Store"
    CARD = re.compile(
        r'<a href="(?P<url>https://www\.apple\.com/cl/shop/buy-iphone/[^"]+)"[^>]*?'
        r'data-part-number="(?P<part>[^"]+)"[^>]*>.*?'
        r'rf-hcard-content-title">(?P<title>.*?)</div>.*?'
        r'rf-hcard-scrim-price[^>]*>\s*Desde\s*<span class="nowrap">(?P<price>[^<]+)</span>', re.S)

    def parse(self, text: str, url: str) -> PageResult:
        from utils import parse_price
        out = []
        for m in self.CARD.finditer(text):
            price = parse_price(m.group("price"))
            title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m.group("title")))).strip()
            if price and title:
                out.append(Product(f"apple:{m.group('part')}", self.site, f"Apple {title} (desde)",
                                   m.group("url"), price, brand="Apple"))
        return PageResult(out, "tarjetas rf-hcard")
