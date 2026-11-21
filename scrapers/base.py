"""Clase base de extractores (patrón Strategy) y recorrido de páginas."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import config
from fetchers import BlockedError, BrowserPool, HttpFetcher, RobotsDisallowed
from models import PageResult, ScrapeResult
from utils import dedupe

log = logging.getLogger("scrapers")


@dataclass
class FetchContext:
    http: HttpFetcher
    browser: BrowserPool


class Scraper:
    site: str = ""
    label: str = ""
    browser_args: tuple[str, ...] = ()
    http_retries: int | None = None   # None = config.MAX_RETRIES

    # ---- a implementar por cada tienda ------------------------------------
    def page_url(self, base_url: str, n: int) -> str | None:
        """URL de la página n (1 = primera). None = el sitio no pagina (o no se permite)."""
        return base_url if n == 1 else None

    def parse(self, text: str, url: str) -> PageResult:
        """Extrae productos de la respuesta HTTP. Función pura -> fácil de testear."""
        raise NotImplementedError

    async def fetch(self, ctx: FetchContext, url: str) -> PageResult:
        return self.parse(await ctx.http.get(url, retries=self.http_retries), url)

    async def browser_fetch(self, ctx: FetchContext, url: str) -> PageResult | None:
        """Respaldo con navegador real. Por defecto: renderiza y aplica el mismo parse()."""
        bctx = await ctx.browser.context(self.browser_args)
        try:
            page = await bctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=config.PAGE_TIMEOUT_MS)
            await page.wait_for_timeout(2500)
            res = self.parse(await page.content(), url)
            res.fallback, res.method = True, f"navegador · {res.method}"
            return res
        finally:
            await bctx.close()

    # ---- recorrido ----------------------------------------------------------
    async def scrape(self, ctx: FetchContext, source: config.Source, max_pages: int) -> ScrapeResult:
        started = time.monotonic()
        result = ScrapeResult(source)
        seen: set[str] = set()
        for n in range(1, max(1, max_pages) + 1):
            if result.total_pages is not None and n > result.total_pages:
                break
            url = self.page_url(source.url, n)
            if url is None:
                break
            try:
                page = await self._fetch_page(ctx, url)
            except BlockedError as exc:
                result.blocked = True
                if n == 1:
                    result.error = str(exc)
                else:
                    result.warnings.append(f"página {n}: {exc}")
                break
            except RobotsDisallowed as exc:
                if n == 1:
                    result.skipped = str(exc)
                else:
                    result.warnings.append(str(exc))
                break
            except Exception as exc:  # noqa: BLE001 - una tienda nunca tumba a las demás
                msg = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}"
                if n == 1:
                    result.error = msg
                else:
                    result.warnings.append(f"página {n}: {msg}")
                break
            fresh = [p for p in dedupe(page.products) if p.key not in seen]
            if page.total_pages:
                result.total_pages = page.total_pages
            if not fresh:
                if n == 1:
                    result.error = "la página cargó pero no trajo productos (¿cambió el diseño?)"
                break
            seen.update(p.key for p in fresh)
            result.products.extend(fresh)
            result.method = result.method or page.method
            result.fallback = result.fallback or page.fallback
            result.pages = n
        result.full_coverage = bool(result.total_pages and result.pages >= result.total_pages)
        result.duration = time.monotonic() - started
        return result

    async def _fetch_page(self, ctx: FetchContext, url: str) -> PageResult:
        try:
            res = await self.fetch(ctx, url)
            if res.products:
                return res
            reason = "sin productos"
        except (BlockedError, RobotsDisallowed):
            raise
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"
        if not config.BROWSER_FALLBACK:
            raise RuntimeError(reason)
        log.warning("[%s] vía HTTP falló (%s) -> probando navegador", self.site, str(reason)[:120])
        res = await self.browser_fetch(ctx, url)
        if res is None or not res.products:
            raise RuntimeError(f"HTTP: {reason} | navegador: sin productos")
        return res
