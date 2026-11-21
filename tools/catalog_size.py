"""Mide el tamaño de cada catálogo (productos y páginas) y prueba tamaños de página más grandes.

    python tools/catalog_size.py
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from fetchers import BrowserPool, HttpFetcher  # noqa: E402
from scrapers import SCRAPERS, FetchContext  # noqa: E402
from utils import with_query  # noqa: E402


def sfcc_total(html: str) -> int | None:
    for pat in (r'data-total-count="(\d+)"', r'"totalCount"\s*:\s*(\d+)', r'(\d[\d.]*)\s+(?:productos|resultados)'):
        m = re.search(pat, html, re.I)
        if m:
            return int(m.group(1).replace(".", ""))
    return None


async def main() -> None:
    ctx = FetchContext(HttpFetcher(), BrowserPool())
    totals: dict[str, int] = {}
    try:
        for src in config.SOURCES:
            if "⚡" in src.name or src.site in ("paris", "lider", "maconline", "samsung", "apple", "xiaomi", "motorola", "multicentro"):
                continue
            sc = SCRAPERS[src.site]()
            url = sc.page_url(src.url, 1)
            try:
                text = await ctx.http.get(url, accept="application/json" if src.site == "pcfactory" else "text/html,*/*",
                                          retries=2)
                text = text if isinstance(text, str) else ""
            except Exception as exc:  # noqa: BLE001
                print(f"{src.name:<40} ERROR {exc}")
                continue
            if src.site == "pcfactory":
                res = await sc.fetch(ctx, url)
                pages, n = res.total_pages, None
            else:
                res = sc.parse(text, url)
                pages = res.total_pages
                n = sfcc_total(text) if src.site in ("abc", "hites") else None
                if src.site == "falabella":
                    m = re.search(r'"count":(\d+),"perPage":(\d+)', text)
                    n = int(m.group(1)) if m else None
            per = len(res.products)
            if not pages and n and per:
                pages = -(-n // per)
            totals[urlsplit(src.url).netloc] = totals.get(urlsplit(src.url).netloc, 0) + (pages or 0)
            print(f"{src.name:<40} {per:>3}/pág  total≈{n or '?':>6}  páginas={pages or '?'}")
        # ¿aceptan páginas más grandes?
        for name, url in [("abc sz=96", "https://www.abc.cl/tecnologia/celulares/smartphones/?start=0&sz=96"),
                          ("hites sz=96", "https://www.hites.com/celulares/smartphones/?start=0&sz=96"),
                          ("entel Nrpp=96", "https://miportal.entel.cl/personas/catalogo/celulares?No=0&Nrpp=96")]:
            site = name.split()[0]
            res = SCRAPERS[site]().parse(await ctx.http.get(url), url)
            print(f"{name:<40} -> {len(res.products)} productos en 1 página")
    finally:
        await ctx.http.aclose()
    print("\npáginas por dominio:", totals)


if __name__ == "__main__":
    asyncio.run(main())
