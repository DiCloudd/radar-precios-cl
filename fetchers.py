"""Capa de red "buen ciudadano": HTTP liviano + navegador de respaldo.

Estrategia anti-bloqueo (sin evadir protecciones):
  * Una sesión HTTP/1.1 persistente por dominio (cookies, keep-alive), cabeceras de navegador reales y
    coherentes, Referer de la propia tienda.
  * Espaciado mínimo por dominio (DOMAIN_MIN_GAP) + jitter aleatorio -> nunca ráfagas.
  * robots.txt respetado (cacheado 12 h).
  * Detección de bloqueo (403/429/captcha/"Access Denied") -> BlockedError. El orquestador pausa esa
    tienda con backoff exponencial en vez de insistir (insistir es lo que hace que te baneen la IP).
  * Reintentos con backoff solo para errores de red transitorios (Paris corta conexiones al azar).
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
import urllib.robotparser
from urllib.parse import urlsplit

import httpx

import config

log = logging.getLogger("fetch")

BLOCK_MARKERS = (
    "access denied", "pardon our interruption", "px-captcha", "captcha-delivery", "cf-chl-",
    "attention required", "request unsuccessful. incapsula", "are you a robot", "| blocked",
    "account-verification", "verifica que eres humano", "robot or human",
)


class BlockedError(Exception):
    """La tienda detectó tráfico automatizado. No reintentar: pausar."""


class RobotsDisallowed(Exception):
    pass


def jittered(seconds: float) -> float:
    return max(0.0, seconds * random.uniform(1 - config.JITTER, 1 + config.JITTER))


def looks_blocked(status: int, text: str, url: str) -> bool:
    if status in (403, 429):
        return True
    head = text[:6000].lower()
    if any(m in head for m in BLOCK_MARKERS) and len(text) < 200_000:
        return True
    return "account-verification" in url


class HttpFetcher:
    def __init__(self) -> None:
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}
        self._robots: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}

    def _headers(self, origin: str, accept: str) -> dict[str, str]:
        return {
            "User-Agent": config.USER_AGENT,
            "Accept": accept,
            "Accept-Language": "es-CL,es;q=0.9,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Referer": origin + "/",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
        }

    def _client(self, domain: str) -> httpx.AsyncClient:
        if domain not in self._clients:
            self._clients[domain] = httpx.AsyncClient(
                http2=False, follow_redirects=True, timeout=config.HTTP_TIMEOUT,
                proxy=config.PROXY_URL, limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            )
            self._locks[domain] = asyncio.Lock()
        return self._clients[domain]

    async def allowed(self, url: str) -> bool:
        if not config.RESPECT_ROBOTS:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = self._robots.get(origin)
        if not cached or time.time() - cached[0] > 12 * 3600:
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                r = await self._client(parts.netloc).get(origin + "/robots.txt",
                                                        headers={"User-Agent": config.USER_AGENT})
                if r.status_code >= 400:
                    rp = None          # sin robots.txt -> todo permitido
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[origin] = (time.time(), rp)
        rp = self._robots[origin][1]
        return True if rp is None else rp.can_fetch("*", url)

    async def get(self, url: str, *, accept: str = "text/html,application/xhtml+xml,*/*;q=0.8",
                  as_json: bool = False, retries: int | None = None):
        retries = retries or config.MAX_RETRIES
        parts = urlsplit(url)
        domain, origin = parts.netloc, f"{parts.scheme}://{parts.netloc}"
        if not await self.allowed(url):
            raise RobotsDisallowed(f"robots.txt de {domain} no permite {parts.path}?{parts.query}")
        client = self._client(domain)
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            async with self._locks[domain]:                      # una petición a la vez por dominio
                wait = self._last.get(domain, 0) + jittered(config.DOMAIN_MIN_GAP) - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    r = await client.get(url, headers=self._headers(origin, accept))
                except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError,
                        httpx.TimeoutException) as exc:
                    last_exc = exc
                    r = None
                finally:
                    self._last[domain] = time.monotonic()
            if r is not None:
                text = r.text
                if looks_blocked(r.status_code, text, str(r.url)):
                    raise BlockedError(f"{domain} respondió {r.status_code} (bloqueo/captcha)")
                if r.status_code < 500:
                    r.raise_for_status()
                    return r.json() if as_json else text
                last_exc = httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            if attempt < retries:
                await asyncio.sleep(jittered(3 * attempt))
        raise last_exc or RuntimeError("sin respuesta")

    async def aclose(self) -> None:
        for c in self._clients.values():
            await c.aclose()


class BrowserPool:
    """Chromium compartido (se lanza solo si algún extractor lo necesita)."""

    def __init__(self) -> None:
        self._pw = None
        self._browsers: dict[tuple, object] = {}
        self._lock = asyncio.Lock()

    async def context(self, args: tuple[str, ...] = ()):
        from playwright.async_api import async_playwright  # import diferido: solo si hace falta
        async with self._lock:
            if self._pw is None:
                self._pw = await async_playwright().start()
            if args not in self._browsers:
                self._browsers[args] = await self._pw.chromium.launch(
                    headless=config.HEADLESS, args=list(args),
                    proxy={"server": config.PROXY_URL} if config.PROXY_URL else None)
        ctx = await self._browsers[args].new_context(
            user_agent=config.USER_AGENT, locale="es-CL", timezone_id="America/Santiago",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "es-CL,es;q=0.9"})
        await ctx.route("**/*", lambda route: route.abort()
                        if route.request.resource_type in ("image", "media", "font") else route.continue_())
        return ctx

    async def aclose(self) -> None:
        for b in self._browsers.values():
            await b.close()
        if self._pw:
            await self._pw.stop()
