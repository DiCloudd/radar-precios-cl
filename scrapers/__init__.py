"""Registro de extractores por tienda (patrón Strategy)."""
from __future__ import annotations

from .base import FetchContext, Scraper
from .brands import AppleScraper, SamsungScraper
from .falabella import FalabellaScraper
from .lider import LiderScraper
from .paris import ParisScraper
from .pcfactory import PCFactoryScraper
from .platforms import MacOnlineScraper, MotorolaScraper, MulticentroScraper, XiaomiScraper
from .product_page import ProductPageScraper
from .sfcc import AbcScraper, HitesScraper
from .telcos import EntelScraper, MovistarScraper

SCRAPERS: dict[str, type[Scraper]] = {
    cls.site: cls for cls in (
        FalabellaScraper, ParisScraper, PCFactoryScraper, LiderScraper, AbcScraper, HitesScraper,
        EntelScraper, MovistarScraper, SamsungScraper, AppleScraper, ProductPageScraper,
        MotorolaScraper, XiaomiScraper, MacOnlineScraper, MulticentroScraper,
    )
}

SITE_LABELS = {k: v.label for k, v in SCRAPERS.items()}

__all__ = ["SCRAPERS", "SITE_LABELS", "FetchContext", "Scraper"]
