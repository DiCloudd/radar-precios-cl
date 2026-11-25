"""Registro de extractores por tienda (patrón Strategy)."""
from __future__ import annotations

from .base import FetchContext, Scraper
from .brands import AppleScraper, SamsungScraper
from .falabella import FalabellaScraper
from .lenovo import LenovoScraper
from .lider import LiderScraper
from .paris import ParisScraper
from .pcfactory import PCFactoryScraper
from .platforms import MacOnlineScraper, MotorolaScraper, MulticentroScraper, XiaomiScraper
from .product_page import ProductPageScraper
from .sfcc import AbcScraper, HitesScraper
from .solotodo import LABELS as SOLOTODO_LABELS, SoloTodoScraper
from .telcos import EntelScraper, MovistarScraper

SCRAPERS: dict[str, type[Scraper]] = {
    cls.site: cls for cls in (
        FalabellaScraper, ParisScraper, PCFactoryScraper, LiderScraper, AbcScraper, HitesScraper,
        EntelScraper, MovistarScraper, SamsungScraper, AppleScraper, ProductPageScraper,
        MotorolaScraper, XiaomiScraper, MacOnlineScraper, MulticentroScraper, LenovoScraper, SoloTodoScraper,
    )
}

SITE_LABELS = {k: v.label for k, v in SCRAPERS.items()}
for _site, _label in SOLOTODO_LABELS.items():           # tiendas que llegan vía SoloTodo
    SITE_LABELS.setdefault(_site, _label)

__all__ = ["SCRAPERS", "SITE_LABELS", "FetchContext", "Scraper"]
