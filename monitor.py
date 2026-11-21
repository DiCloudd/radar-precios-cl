"""Orquestador de ciclos: scrapea en paralelo, guarda, analiza, protege contra bloqueos y notifica."""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime

import config
import godzilla
from analysis import Alert, clp, evaluate, fake_discount
from calendar_cl import active_event
from fetchers import BrowserPool, HttpFetcher
from matcher import group_key
from models import ScrapeResult
from notifier import TelegramNotifier, chunk, format_alert
from scrapers import SCRAPERS, SITE_LABELS, FetchContext
from storage import PriceStore, ago, now_iso

log = logging.getLogger("monitor")


@dataclass
class CycleReport:
    mode: str
    results: list[ScrapeResult] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    duration: float = 0.0

    @property
    def failed(self) -> list[ScrapeResult]:
        # un bloqueo no es un fallo del programa: se gestiona con pausa automática
        return [r for r in self.results if not r.ok and not r.skipped and not r.blocked]


def _watch_sources() -> list[config.Source]:
    return [config.Source(w.name, w.url, "watch") for w in config.WATCHES]


def _is_watched(key: str, name: str, site: str) -> bool:
    return key.startswith("url:") or any(t.matches(key, name, site) for t in config.TARGETS)


class Monitor:
    def __init__(self, dry_run: bool = False) -> None:
        self.ctx = FetchContext(HttpFetcher(), BrowserPool())
        self.notifier = TelegramNotifier(dry_run=dry_run)
        self.sem = asyncio.Semaphore(config.CONCURRENCY)

    async def aclose(self) -> None:
        await self.ctx.http.aclose()
        await self.ctx.browser.aclose()

    # ------------------------------------------------------------------ pausas por bloqueo
    def _paused(self, store: PriceStore, src: config.Source) -> str | None:
        st = store.get_meta(f"block:{src.site}")
        if st and st.get("until", 0) > time.time():
            return f"pausada por bloqueo hasta {datetime.fromtimestamp(st['until']):%H:%M} (strike {st['strikes']})"
        return None

    def _update_breaker(self, store: PriceStore, res: ScrapeResult, ts: str) -> Alert | None:
        k = f"block:{res.source.site}"
        st = store.get_meta(k, {"strikes": 0, "until": 0})
        if res.blocked and not res.products:
            st["strikes"] += 1
            pause = min(config.BLOCK_BASE_PAUSE * 2 ** (st["strikes"] - 1), config.BLOCK_MAX_PAUSE)
            st["until"] = time.time() + pause
            store.set_meta(k, st)
            log.warning("[%s] BLOQUEO detectado -> pausa %d min", res.source.site, pause // 60)
            if st["strikes"] in (1, 3, 6):
                return Alert("blocked", None, f"🚧 {SITE_LABELS.get(res.source.site)} está bloqueando",
                             [f"Pausa automática de {pause // 60:.0f} min para no empeorar el bloqueo.",
                              f"Detalle: {res.error}"], 4)
        elif res.ok and st["strikes"]:
            store.set_meta(k, {"strikes": 0, "until": 0})
            log.info("[%s] desbloqueada", res.source.site)
        return None

    # ------------------------------------------------------------------ vigilante de diseño
    def _layout_watch(self, store: PriceStore, res: ScrapeResult, mode: str) -> Alert | None:
        if res.skipped or res.blocked:
            return None
        prev = [r["n"] for r in store.recent_runs(res.source.name, 12) if r["ok"] and r["mode"] == mode]
        reason = None
        if res.error and ("diseño" in res.error or "sin productos" in res.error):
            reason = res.error
        elif res.ok and res.fallback:
            reason = f"la vía principal falló y se usó el respaldo ({res.method})"
        elif res.ok and len(prev) >= 3 and len(res.products) < 0.5 * statistics.median(prev):
            reason = f"trajo {len(res.products)} productos vs ~{statistics.median(prev):.0f} habituales"
        if reason:
            return Alert("layout", None, f"⚠️ {SITE_LABELS.get(res.source.site)}: posible cambio de diseño",
                         [reason, "Revisa el extractor en scrapers/ (los tests en tests/ ayudan)."], 4)
        return None

    # ------------------------------------------------------------------ ciclo
    async def _scrape(self, src: config.Source, max_pages: int) -> ScrapeResult:
        async with self.sem:
            scraper = SCRAPERS[src.site]()
            res = await scraper.scrape(self.ctx, src, max_pages)
            log.info("  %-30s %s %3d prod. %s/%s pág. %.1fs %s", src.name, "OK " if res.ok else "ERR",
                     len(res.products), res.pages, res.total_pages or "?", res.duration,
                     res.method if res.ok else (res.error or res.skipped or ""))
            return res

    async def cycle(self, mode: str = "fast") -> CycleReport:
        started = time.monotonic()
        report = CycleReport(mode)
        ts = now_iso()
        with PriceStore(config.DB_PATH) as store:
            jobs, skipped = [], []
            for src in config.SOURCES + _watch_sources():
                if src.site not in SCRAPERS:
                    log.error("Extractor desconocido '%s' en %s", src.site, src.name)
                    continue
                pause = self._paused(store, src)
                if pause:
                    skipped.append(ScrapeResult(src, skipped=pause))
                    continue
                jobs.append(self._scrape(src, 1 if mode == "fast" else src.max_pages))
            log.info("Ciclo %s: %d fuentes (%d en pausa)", mode.upper(), len(jobs), len(skipped))
            report.results = list(await asyncio.gather(*jobs)) + skipped

            system_alerts: list[Alert] = []
            for res in report.results:
                if res.skipped and "pausada" in res.skipped:
                    continue
                store.log_run(res, mode, config.RUNNER_NAME, ts)
                for a in (self._update_breaker(store, res, ts), self._layout_watch(store, res, mode)):
                    if a:
                        system_alerts.append(a)
                seen = set()
                for p in res.products:
                    seen.add(p.key)
                    obs = store.observe(p, group_key(p.name, p.brand), res.source.name, ts)
                    report.alerts.extend(evaluate(obs, store, _is_watched(p.key, p.name, p.site)))
                # desaparecidos: solo si se recorrió la categoría completa (si no, es ruido de paginación)
                if mode == "deep" and res.ok and res.full_coverage:
                    for row in store.mark_missing(res.source.name, seen, ts):
                        if _is_watched(row["key"], row["name"], row["site"]):
                            system_alerts.append(Alert("missing", None, "👻 Desapareció de la tienda",
                                                       [f"<b>{row['name']}</b> ({SITE_LABELS.get(row['site'])})",
                                                        f"Último precio {clp(row['price'])}", row["url"]], 3))
                if res.source.site == "watch" and not res.ok and not res.blocked:
                    system_alerts.append(Alert("missing", None, "👻 Producto vigilado no disponible",
                                               [res.source.name, res.error or "", res.source.url], 3))
            store.commit()

            # deduplicar con enfriamiento
            fresh = []
            for a in sorted(report.alerts + system_alerts, key=lambda a: a.priority):
                dedupe_key = a.key or a.title
                if store.alerted_recently(dedupe_key, a.kind, a.price, config.ALERT_COOLDOWN_HOURS):
                    continue
                fake = fake_discount(a.obs, store) if a.obs else None
                text = format_alert(a, SITE_LABELS, fake)
                store.add_alert(dedupe_key, a.kind, a.price, text, ts)
                fresh.append((a, text))
            store.commit()
            report.alerts = [a for a, _ in fresh]
            self._notify(fresh, report, store)
        report.duration = time.monotonic() - started
        return report

    def _notify(self, fresh: list[tuple[Alert, str]], report: CycleReport, store: PriceStore) -> None:
        big = [(a, t) for a, t in fresh if a.kind in ("godzilla", "price_error")]
        rest = [t for a, t in fresh if a.kind not in ("godzilla", "price_error")]
        for a, text in big:   # 🦖 cada Godzilla va solo, con GIF
            self.notifier.send_animation(godzilla.asset(), text)
        header = f"🛒 <b>Monitor de precios</b> · {datetime.now():%d-%m %H:%M}"
        if config.NOTIFY_MODE == "always" and report.mode == "deep":
            listing = []
            for r in report.results:
                for p in r.products:
                    listing.append(f"• {p.name[:60]} — <b>{clp(p.price)}</b> ({SITE_LABELS.get(p.site)})")
            rest = rest + listing
        if rest:
            self.notifier.send_many(chunk(rest, header))

    # ------------------------------------------------------------------ resumen diario
    def daily_summary(self, force: bool = False) -> bool:
        today = datetime.now().strftime("%Y-%m-%d")
        with PriceStore(config.DB_PATH) as store:
            if not force and (store.get_meta("summary:last") == today or datetime.now().hour < config.DAILY_SUMMARY_HOUR):
                return False
            text = build_summary(store)
            self.notifier.send(text)
            store.set_meta("summary:last", today)
        return True


def build_summary(store: PriceStore) -> str:
    since = ago(hours=24)
    rows = store.conn.execute("""
        SELECT p.key, p.name, p.site, p.price, p.url, p.group_key,
               (SELECT price FROM prices h WHERE h.key=p.key AND h.ts<? ORDER BY ts DESC LIMIT 1) AS old
        FROM products p WHERE p.missing_since IS NULL""", (since,)).fetchall()
    changes = [(r, (r["price"] - r["old"]) / r["old"] * 100) for r in rows if r["old"] and r["old"] != r["price"]]
    drops = sorted((c for c in changes if c[1] < 0), key=lambda c: c[1])[:10]
    rises = sorted((c for c in changes if c[1] > 0), key=lambda c: -c[1])[:5]
    groups: dict[str, list] = {}
    for r in rows:
        if r["group_key"]:
            groups.setdefault(r["group_key"], []).append(r)
    multi = [(g, sorted(v, key=lambda r: r["price"])) for g, v in groups.items() if len({r["site"] for r in v}) >= 2]
    savings = sorted(multi, key=lambda gv: -(gv[1][-1]["price"] - gv[1][0]["price"]))[:5]
    n_alerts = store.conn.execute("SELECT kind, COUNT(*) FROM alerts WHERE ts>=? GROUP BY kind", (since,)).fetchall()
    runs = store.conn.execute("SELECT site, AVG(ok)*100 FROM runs WHERE ts>=? GROUP BY site", (since,)).fetchall()
    import html as h
    out = [f"☀️ <b>Resumen diario</b> · {datetime.now():%d-%m-%Y}", f"📦 {len(rows)} productos en {len({r['site'] for r in rows})} tiendas"]
    ev = active_event()
    if ev:
        out.append(f"🎉 Evento activo: <b>{ev.name}</b> (revisando más seguido)")
    if drops:
        out.append("\n📉 <b>Top bajadas 24 h</b>")
        out += [f"{i}. {h.escape(r['name'][:55])} — <b>{clp(r['price'])}</b> ({pct:+.0f}%) · {SITE_LABELS.get(r['site'])}"
                for i, (r, pct) in enumerate(drops, 1)]
    if rises:
        out.append("\n📈 <b>Top subidas 24 h</b>")
        out += [f"• {h.escape(r['name'][:55])} {pct:+.0f}% · {SITE_LABELS.get(r['site'])}" for r, pct in rises]
    if savings:
        out.append("\n🔀 <b>Mismo modelo, distinta tienda</b>")
        for g, v in savings:
            out.append(f"• {h.escape(g)}: {clp(v[0]['price'])} en {SITE_LABELS.get(v[0]['site'])} "
                       f"(vs {clp(v[-1]['price'])} en {SITE_LABELS.get(v[-1]['site'])})")
    if n_alerts:
        out.append("\n🔔 Alertas: " + ", ".join(f"{k} {n}" for k, n in n_alerts))
    if runs:
        out.append("🩺 Salud: " + " · ".join(f"{SITE_LABELS.get(s, s)} {ok:.0f}%" for s, ok in runs))
    if not drops and not rises:
        out.append("\nSin cambios de precio en las últimas 24 h.")
    return "\n".join(out)
