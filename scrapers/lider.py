"""Lider (plataforma Walmart, Next.js): __NEXT_DATA__ -> props.pageProps.initialData.searchResult (verificado nov-2026)."""
from __future__ import annotations

from models import PageResult, Product
from utils import next_data, parse_price, with_query

from .base import Scraper


class LiderScraper(Scraper):
    site, label = "lider", "Lider"

    def page_url(self, base_url: str, n: int) -> str | None:
        return base_url if n == 1 else with_query(base_url, page=n)

    def parse(self, text: str, url: str) -> PageResult:
        nd = next_data(text) or {}
        sr = (((nd.get("props") or {}).get("pageProps") or {}).get("initialData") or {}).get("searchResult") or {}
        total = (sr.get("paginationV2") or {}).get("maxPage")
        out = []
        for stack in sr.get("itemStacks") or []:
            for it in stack.get("items") or []:
                if it.get("__typename") != "Product" or not it.get("id"):
                    continue
                pi = it.get("priceInfo") or {}
                price = parse_price(pi.get("linePrice") or pi.get("linePriceDisplay"))
                was = parse_price(pi.get("wasPrice") or pi.get("itemPrice"))
                if not price:
                    continue
                status = ((it.get("availabilityStatusV2") or {}).get("value") or "").upper()
                badges = [f.get("text") for f in ((it.get("badges") or {}).get("flags") or []) if f.get("text")]
                link = (it.get("canonicalUrl") or "").split("?")[0]
                out.append(Product(
                    key=f"lider:{it['id']}", site=self.site, name=(it.get("name") or "").strip().rstrip("."),
                    url=f"https://www.lider.cl{link}" if link.startswith("/") else link, price=price,
                    list_price=was if was and was > price else None, brand=it.get("brand"),
                    image=(it.get("imageInfo") or {}).get("thumbnailUrl"),
                    in_stock=(status == "IN_STOCK") if status else None,
                    stock_text=it.get("availabilityStatusDisplayValue"), badges=badges[:4]))
        return PageResult(out, "__NEXT_DATA__ searchResult", int(total) if total else None)
