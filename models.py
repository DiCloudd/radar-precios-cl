"""Modelos de datos compartidos."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Product:
    key: str                          # identificador estable "<sitio>:<id>"
    site: str
    name: str
    url: str
    price: int                        # mejor precio público (sin tarjeta de la tienda)
    list_price: int | None = None     # precio normal / tachado
    card_price: int | None = None     # precio con tarjeta de la tienda (CMR, Paris, La Polar, Hites...)
    brand: str | None = None
    image: str | None = None
    in_stock: bool | None = None      # None = la tienda no informa
    stock_text: str | None = None     # p.ej. "+100 unid."
    installments: int | None = None   # cuotas sin interés si la tienda lo informa
    badges: list[str] = field(default_factory=list)  # etiquetas de promo ("Cyber", "Envío gratis"...)
    found_at: str | None = None       # URL de la página donde apareció (para re-verificar errores)

    @property
    def discount_pct(self) -> float | None:
        if self.list_price and self.list_price > self.price:
            return (self.list_price - self.price) / self.list_price * 100
        return None


@dataclass
class PageResult:
    products: list[Product]
    method: str                       # estrategia que funcionó (para el vigilante de diseño)
    total_pages: int | None = None    # si el sitio informa cuántas páginas hay
    fallback: bool = False            # True si se usó un extractor de respaldo


@dataclass
class ScrapeResult:
    source: Any                       # config.Source
    products: list[Product] = field(default_factory=list)
    method: str = ""
    error: str | None = None
    pages: int = 0
    total_pages: int | None = None
    full_coverage: bool = False       # se recorrió la categoría completa
    fallback: bool = False
    blocked: bool = False
    skipped: str | None = None        # motivo si no se ejecutó (robots, pausa por bloqueo...)
    warnings: list[str] = field(default_factory=list)
    duration: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None and self.skipped is None and bool(self.products)
