"""Tiendas Salesforce Commerce Cloud (Abc/La Polar y Hites). Paginación ?start=N&sz=36 (verificado nov-2026)."""
from __future__ import annotations

import html
import json

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from models import PageResult, Product
from utils import absolute, parse_price, prices_in_text, with_query

from .base import Scraper

PAGE_SIZE = 36


class _SFCC(Scraper):
    def page_url(self, base_url: str, n: int) -> str | None:
        return with_query(base_url, start=(n - 1) * PAGE_SIZE, sz=PAGE_SIZE)


class AbcScraper(_SFCC):
    """Tarjetas .product-tile__wrapper[data-pid] con precios .la-polar / .internet / .normal [data-value]."""
    site, label = "abc", "Abc (La Polar)"

    def parse(self, text: str, url: str) -> PageResult:
        out = []
        for tile in HTMLParser(text).css(".product-tile__wrapper[data-pid]"):
            pid = tile.attributes.get("data-pid")

            def val(sel: str) -> int | None:
                n = tile.css_first(f"{sel} .price-value[data-value]")
                return parse_price(n.attributes.get("data-value")) if n else None
            card, internet, normal = val(".la-polar"), val(".internet"), val(".normal")
            price = internet or normal or card
            if not pid or not price:
                continue
            name, brand = "", None
            box = tile.css_first("[data-gtm]")
            try:
                imp = json.loads(html.unescape(box.attributes.get("data-gtm", "")))["ecommerce"]["impressions"]
                name, brand = imp.get("name", ""), imp.get("brand")
            except (AttributeError, KeyError, TypeError, json.JSONDecodeError):
                img = tile.css_first("img[alt]")
                name = img.attributes.get("alt", "") if img else ""
            link = tile.css_first("a.image-link, a[href$='.html']")
            img = tile.css_first("img.tile-image")
            badge = tile.css_first(".promotion-badge")
            out.append(Product(
                f"abc:{pid}", self.site, html.unescape(name).strip(),
                absolute(url, link.attributes.get("href")) if link else url, price,
                list_price=normal if normal and normal > price else None,
                card_price=card if card and card < price else None, brand=brand,
                image=img.attributes.get("src") if img else None,
                badges=[f"-{badge.text(strip=True)}"] if badge and badge.text(strip=True) else []))
        return PageResult(out, "SFCC tiles .price-value[data-value]")


class HitesScraper(_SFCC):
    """Tarjetas .plp-grid-tile[data-pid] con JSON data-gtmselectitem (price + hites_price)."""
    site, label = "hites", "Hites"

    def parse(self, text: str, url: str) -> PageResult:
        out = []
        for tile in HTMLParser(text).css(".plp-grid-tile[data-pid]"):
            pid = tile.attributes.get("data-pid")
            box = tile.css_first("[data-gtmselectitem]")
            try:
                item = json.loads(html.unescape(box.attributes.get("data-gtmselectitem", "")))["item"]
            except (AttributeError, KeyError, TypeError, json.JSONDecodeError):
                continue
            price = parse_price(item.get("price"))
            card = parse_price(item.get("hites_price"))
            if not pid or not price:
                continue
            nums = prices_in_text(tile.text(separator=" "))
            link = tile.css_first("a.image-item, a[href$='.html']")
            img = tile.css_first("img.tile-image")
            out.append(Product(
                f"hites:{pid}", self.site, html.unescape(item.get("item_name", "")).strip(),
                absolute(url, link.attributes.get("href")) if link else url, price,
                list_price=max(nums) if nums and max(nums) > price else None,
                card_price=card if card and card < price else None, brand=item.get("item_brand"),
                image=img.attributes.get("src") if img else None,
                badges=[item["promotion_name"].strip()] if (item.get("promotion_name") or "").strip() else []))
        return PageResult(out, "SFCC tiles data-gtmselectitem")
