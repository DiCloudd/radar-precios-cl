"""Página de producto individual (lista [[watch]]): JSON-LD Product + metatags Open Graph como respaldo."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from models import PageResult, Product
from utils import json_ld_nodes, ld_in_stock, ld_offer_prices, parse_price

from .base import Scraper

SITE_BY_DOMAIN = {"falabella": "falabella", "paris": "paris", "pcfactory": "pcfactory", "lider": "lider",
                  "abc.cl": "abc", "hites": "hites", "entel": "entel", "movistar": "movistar",
                  "samsung": "samsung", "apple": "apple",
                  "motorola": "motorola", "mi.com": "xiaomi", "maconline": "maconline", "multicentro": "multicentro"}


def site_of(url: str) -> str:
    host = urlsplit(url).netloc
    return next((v for k, v in SITE_BY_DOMAIN.items() if k in host), host)


class ProductPageScraper(Scraper):
    site, label = "watch", "Producto vigilado"
    browser_args = ("--disable-http2",)

    def parse(self, text: str, url: str) -> PageResult:
        site = site_of(url)
        for node in json_ld_nodes(text):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "Product" not in types:
                continue
            prices = ld_offer_prices(node)
            if not prices:
                continue
            brand = node.get("brand")
            image = node.get("image")
            return PageResult([Product(
                f"url:{url}", site, str(node.get("name", "")).strip(), url, min(prices),
                list_price=max(prices) if len(prices) > 1 and max(prices) > min(prices) else None,
                brand=brand.get("name") if isinstance(brand, dict) else brand,
                image=image[0] if isinstance(image, list) and image else image if isinstance(image, str) else None,
                in_stock=ld_in_stock(node))], "JSON-LD Product")
        price = re.search(r'<meta[^>]+property="(?:product|og):price:amount"[^>]+content="([^"]+)"', text)
        title = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', text)
        if price and parse_price(price.group(1)):
            return PageResult([Product(f"url:{url}", site, title.group(1) if title else url, url,
                                       parse_price(price.group(1)))], "meta og:price", fallback=True)
        return PageResult([], "sin datos")
