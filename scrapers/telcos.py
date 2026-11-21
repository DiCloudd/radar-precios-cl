"""Operadoras: Entel (JSON Endeca embebido) y Movistar (Magento). Verificado nov-2026."""
from __future__ import annotations

import json
import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from models import PageResult, Product
from utils import absolute, parse_price, prices_in_text, with_query

from .base import Scraper

_RECORD = '"@class": "com.endeca.infront.cartridge.model.Record"'


def _first(attrs: dict, *keys: str):
    for k in keys:
        v = attrs.get(k)
        if isinstance(v, list) and v:
            return v[0]
        if v not in (None, "", []):
            return v
    return None


class EntelScraper(Scraper):
    site, label = "entel", "Entel"
    PAGE = 24

    def page_url(self, base_url: str, n: int) -> str | None:
        return base_url if n == 1 else with_query(base_url, No=(n - 1) * self.PAGE, Nrpp=self.PAGE)

    def parse(self, text: str, url: str) -> PageResult:
        dec = json.JSONDecoder()
        out, seen = [], set()
        pos = 0
        while True:
            i = text.find(_RECORD, pos)
            if i < 0:
                break
            j = text.find('"attributes":', i)
            pos = i + len(_RECORD)
            if j < 0 or j - i > 20000:
                continue
            try:
                attrs, _ = dec.raw_decode(text, text.index("{", j))
            except (ValueError, json.JSONDecodeError):
                continue
            pid = _first(attrs, "productId")
            price = parse_price(_first(attrs, "priceFormatted", "price.formatted", "listPrice"))
            if not pid or not price or pid in seen:
                continue
            seen.add(pid)
            ref = parse_price(_first(attrs, "referencePrice"))
            inv = str(_first(attrs, "inventoryStatus") or "").upper()
            cuotas = _first(attrs, "installmentQty")
            img = _first(attrs, "productImage")
            badges = [b for b in [_first(attrs, "categoryDescription")] if b and len(str(b)) <= 30]
            if str(_first(attrs, "cyberDays")) == "1":
                badges.append("Cyber")
            out.append(Product(
                f"entel:{pid}", self.site, str(_first(attrs, "displayName", "description") or "").strip(),
                absolute("https://miportal.entel.cl", _first(attrs, "seoUrl")), price,
                list_price=ref if ref and ref > price else None, brand=_first(attrs, "brand"),
                image=absolute("https://miportal.entel.cl", img) if img else None,
                in_stock=(inv == "IN_STOCK") if inv else None, stock_text=inv.replace("_", " ").title() or None,
                installments=int(cuotas) if str(cuotas or "").isdigit() and str(_first(attrs, "showInstallmentQty")) == "1" else None,
                badges=badges))
        return PageResult(out, "JSON Endeca records")


class MovistarScraper(Scraper):
    """Su robots.txt prohíbe ?p=2+, así que solo se lee la página 1."""
    site, label = "movistar", "Movistar"

    def parse(self, text: str, url: str) -> PageResult:
        out = []
        for tile in HTMLParser(text).css(".product-item-info"):
            link = tile.css_first("a.product-item-link")
            m = re.search(r"(\d+)", tile.attributes.get("id", ""))
            pid = (link.attributes.get("data-crosss-id-catalog") if link else None) or (m.group(1) if m else None)
            vals = tile.css_first(".infoValores")
            nums = prices_in_text(vals.text(separator=" ")) if vals else []
            main = tile.css_first(".divCon-plan-txt-valor-sinLabel")
            price = parse_price(main.text()) if main else (nums[0] if nums else None)
            if not pid or not link or not price:
                continue
            badge = tile.css_first(".information-cucarda")
            img = tile.css_first("img.product-image-photo")
            out.append(Product(
                f"movistar:{pid}", self.site, (link.attributes.get("title") or link.text()).strip(),
                absolute(url, link.attributes.get("href")), price,
                list_price=max(nums) if nums and max(nums) > price else None,
                image=img.attributes.get("src") if img else None,
                badges=[badge.text(strip=True)] if badge and badge.text(strip=True) else []))
        return PageResult(out, "Magento .product-item-info")
