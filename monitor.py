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
import inflation
import pricecheck
import restock
from analysis import Alert, clp, evaluate, fake_discount, is_accessory
from calendar_cl import active_event
from fetchers import BrowserPool, HttpFetcher
from matcher import group_key
from models import ScrapeResult
from notifier import TelegramNotifier, chunk, format_alert
from scrapers import SCRAPERS, SITE_LABELS, FetchContext
from scrapers import solotodo
import userprefs
from storage import PriceStore, ago, now_iso
from telegram_bot import alert_buttons
from verifier import MAX_HUNTS, Verifier

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


def _watch_sources(store: PriceStore) -> list[config.Source]:
    return [config.Source(w.name, w.url, "watch") for w in userprefs.all_watches(store)]


def _is_watched(key: str, name: str, site: str, targets: list) -> bool:
    return key.startswith("url:") or any(t.matches(key, name, site) for t in targets)


class Monitor:
    def __init__(self, dry_run: bool = False, hunting: bool = False) -> None:
        self.ctx = FetchContext(HttpFetcher(), BrowserPool())
        self.notifier = TelegramNotifier(dry_run=dry_run)
        self.sem = asyncio.Semaphore(config.CONCURRENCY)
        self.verifier = Verifier(self.ctx)
        self.checker = pricecheck.PriceChecker(self.ctx)   # 📚 segunda opinión (SoloTodo)
        self.hunting = hunting                  # modo caza solo en el modo 24/7 (necesita seguir vivo)
        self.hunts: set[asyncio.Task] = set()
        self.fast_count = 0                     # para rotar fuentes con fast_every > 1
        self.crawling = False                   # True si el rastreador de catálogo completo está activo
        self.cycle_busy = False                 # True mientras un ciclo scrapea
        from crawler import crawl_sources
        self._crawled = {s for srcs in crawl_sources(config.SOURCES).values() for s in srcs}

    async def aclose(self) -> None:
        for t in list(self.hunts):              # fin de ventana: cierra las cazas (quedan como "seguía activo")
            t.cancel()
        if self.hunts:
            await asyncio.gather(*self.hunts, return_exceptions=True)
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

    def _ran_recently(self, store: PriceStore, src: config.Source) -> bool:
        last = next((r for r in store.recent_runs(src.name, 3) if r["ok"]), None)
        return bool(last) and (datetime.now() - datetime.fromisoformat(last["ts"])).total_seconds() < src.min_interval

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
        if mode == "fast":
            self.fast_count += 1
        ts = now_iso()
        with PriceStore(config.DB_PATH) as store:
            jobs, skipped = [], []
            targets = userprefs.all_targets(store)          # sources.toml + los creados en Telegram
            for src in config.SOURCES + _watch_sources(store):
                if src.site not in SCRAPERS:
                    log.error("Extractor desconocido '%s' en %s", src.site, src.name)
                    continue
                if mode == "fast" and (not src.fast or (self.fast_count + src.fast_offset) % src.fast_every):
                    continue                                  # solo profundo, o le toca en otro ciclo rápido
                if mode == "deep" and self.crawling and src in self._crawled:
                    continue                                  # el rastreador ya recorre todas sus páginas
                if src.min_interval and self._ran_recently(store, src):
                    continue                                  # fuente lenta (SoloTodo): 1 lectura por intervalo
                pause = self._paused(store, src)
                if pause:
                    skipped.append(ScrapeResult(src, skipped=pause))
                    continue
                jobs.append(self._scrape(src, 1 if mode == "fast" else src.max_pages))
            log.info("Ciclo %s: %d fuentes (%d en pausa)", mode.upper(), len(jobs), len(skipped))
            self.cycle_busy = True                  # el rastreador cede el paso a los ciclos (⚡ más rápidos)
            try:
                report.results = list(await asyncio.gather(*jobs)) + skipped
            finally:
                self.cycle_busy = False

            system_alerts: list[Alert] = []
            for res in report.results:
                if res.skipped and "pausada" in res.skipped:
                    continue
                # calentamiento: la 1ª vez que corre una fuente solo se registra (evita una avalancha de alertas)
                warming = not store.recent_runs(res.source.name, 1)
                store.log_run(res, mode, config.RUNNER_NAME, ts)
                for a in (self._update_breaker(store, res, ts), self._layout_watch(store, res, mode)):
                    if a:
                        system_alerts.append(a)
                seen = {p.key for p in res.products}
                report.alerts.extend(self.ingest(store, res.source, res.products, ts, warming, targets))
                # desaparecidos: solo si se recorrió la categoría completa (si no, es ruido de paginación)
                if mode == "deep" and res.ok and res.full_coverage:
                    for row in store.mark_missing(res.source.name, seen, ts):
                        if _is_watched(row["key"], row["name"], row["site"], targets):
                            system_alerts.append(Alert("missing", None, "👻 Desapareció de la tienda",
                                                       [f"<b>{row['name']}</b> ({SITE_LABELS.get(row['site'])})",
                                                        f"Último precio {clp(row['price'])}", row["url"]], 3))
                if res.source.site == "watch" and not res.ok and not res.blocked:
                    system_alerts.append(Alert("missing", None, "👻 Producto vigilado no disponible",
                                               [res.source.name, res.error or "", res.source.url], 3))
            store.commit()
            report.alerts = await self.dispatch(store, report.alerts + system_alerts, ts, report)
        report.duration = time.monotonic() - started
        return report

    # ------------------------------------------------------------------ procesamiento compartido (ciclos + rastreador)
    def ingest(self, store: PriceStore, src: config.Source, products: list, ts: str, warming: bool,
               targets: list, quiet_new: bool = False) -> list[Alert]:
        """Guarda los productos y devuelve las alertas que generan. Sin awaits: la transacción no cruza esperas.
        quiet_new: no alertar productos nunca vistos (1er barrido del rastreador)."""
        alerts: list[Alert] = []
        cat = src.category
        watch = restock.load(store)
        before = dict(watch)
        for p in products:
            gk = group_key(p.name, p.brand, cat)
            if is_accessory(p.name, cat):              # una correa no se compara con el reloj
                gk = f"accesorio: {gk}"
            obs = store.observe(p, gk, src.name, ts, cat)
            if warming:
                continue
            quiet = quiet_new and obs.is_new
            found = [] if quiet else evaluate(obs, store, _is_watched(p.key, p.name, p.site, targets), targets, cat)
            # 🔥 gangas / 🚨 reposición (en silencio en el 1er barrido: solo se anotan para vigilarlas)
            extra = restock.check(store, obs, gk, cat, src, watch, quiet=quiet)
            if any(a.kind in ("price_error", "godzilla") for a in found):
                extra = [a for a in extra if a.kind != "ganga"]        # ya sale como error de precio
            alerts.extend(found + extra)
        if watch != before:
            store.set_meta(restock.META, restock.prune(watch))
        return alerts

    async def dispatch(self, store: PriceStore, alerts: list[Alert], ts: str, report: CycleReport | None = None) -> list[Alert]:
        """Deduplica (enfriamiento + silencios), registra y notifica. Devuelve las alertas efectivamente nuevas."""
        # 📚 precios vía SoloTodo con más de 24 h: sirven para comparar, no para avisar (la tienda pudo cambiarlos)
        alerts = [a for a in alerts if not (a.obs and getattr(a.obs.product, "age_h", 0) > solotodo.ALERT_MAX_AGE_H)]
        alerts = await self._vet(store, alerts)
        fresh = []
        for a in sorted(alerts, key=lambda a: a.priority):
            dedupe_key = a.key or a.title
            if store.alerted_recently(dedupe_key, a.kind, a.price, config.ALERT_COOLDOWN_HOURS):
                continue
            # errores, Godzilla y gangas: 1 aviso por semana salvo que baje MÁS (anoche: el mismo Godzilla 2 veces)
            if a.key and a.kind in ("godzilla", "price_error", "ganga", "restock_watch") and \
                    store.alerted_at_or_below(a.key, a.kind, a.price, 7):
                continue
            if a.kind not in ("godzilla", "price_error") and userprefs.is_muted(store, a.key):
                continue                                   # 🔕 silenciado desde Telegram
            fake = fake_discount(a.obs, store) if a.obs else None
            text = format_alert(a, SITE_LABELS, fake)
            store.add_alert(dedupe_key, a.kind, a.price, text, ts)
            fresh.append((a, text))
        store.commit()
        if fresh:
            await self._notify(fresh, report or CycleReport("crawl"), store)
        return [a for a, _ in fresh]

    async def _vet(self, store: PriceStore, alerts: list[Alert]) -> list[Alert]:
        """📚 Segunda opinión (historial de 90 días de SoloTodo) para mínimos históricos, bajadas y Godzilla:
        descarta los "mínimos" que no lo son y los "antes" inflados, y deja anotado cada caso en 📈 inflados."""
        out, checks = [], 0
        for a in sorted(alerts, key=lambda a: a.priority):
            if (a.kind not in ("historic_low", "drop", "godzilla") or not a.obs or checks >= pricecheck.CHECKS_PER_BATCH
                    or store.alerted_recently(a.key, a.kind, a.price, config.ALERT_COOLDOWN_HOURS)):
                out.append(a)
                continue
            checks += 1
            h = await self.checker.history(store, a.obs.product)
            if not h:
                out.append(a)
                continue
            p = a.obs.product
            claimed = a.ref if a.kind == "godzilla" else a.obs.previous
            real = (1 - p.price / h.usual) * 100
            when = f"{h.min_date[8:10]}-{h.min_date[5:7]}"
            if a.kind == "historic_low" and p.price > h.min90:
                inflation.log(store, p, "falso mínimo", claimed, h.min90, "solotodo",
                              f"no es mínimo: estuvo a {clp(h.min90)} el {when} (SoloTodo)")
                log.info("Descartado 'mínimo histórico' de %s: estuvo a %s el %s", p.key, h.min90, when)
                continue
            inflated = bool(claimed and claimed > h.usual * 1.10)
            if inflated:
                inflation.log(store, p, "antes inflado", claimed, h.usual, "solotodo",
                              f"anunciaba 'antes {clp(claimed)}', su precio habitual era {clp(h.usual)} (SoloTodo)")
            need = config.GODZILLA_REAL_PCT if a.kind == "godzilla" else config.DROP_ALERT_PCT
            if real < need:              # frente a su precio HABITUAL no ahorras lo suficiente: no vale el aviso
                log.info("Descartado %s de %s: ahorro real %.0f%% vs habitual %s", a.kind, p.key, real, h.usual)
                continue
            if inflated:
                a.lines.append(f"⚠️ El 'antes' estaba inflado: precio habitual {clp(h.usual)} → ahorro real -{real:.0f}%")
            a.lines.append(f"📚 SoloTodo 90 días: habitual {clp(h.usual)} · mínimo {clp(h.min90)} ({when})")
            out.append(a)
        return out

    async def _notify(self, fresh: list[tuple[Alert, str]], report: CycleReport, store: PriceStore) -> None:
        def row_of(a: Alert):
            return store.conn.execute("SELECT rowid, category, image FROM products WHERE key=?", (a.key,)).fetchone() if a.obs else None

        def buttons(a: Alert) -> dict | None:
            row = row_of(a)
            return alert_buttons(row[0]) if row else None

        big = [(a, t) for a, t in fresh if a.kind in ("godzilla", "price_error") and a.obs]
        urgent = [(a, t) for a, t in fresh if a.kind in ("restock", "ganga", "restock_watch") and (a, t) not in big]
        normal = [(a, t) for a, t in fresh if (a, t) not in big and (a, t) not in urgent]

        # 🚨🔥 reposición de gangas y gangas: siempre al instante, una por mensaje con foto (nunca agrupadas)
        for a, text in urgent:
            row = row_of(a)
            image = (a.obs.product.image if a.obs else None) or (row[2] if row else None)
            self.notifier.send_photo(image, text, buttons(a)) if image else self.notifier.send(text, buttons(a))

        # ✅ verificación instantánea de todos los errores en paralelo
        verdicts = await asyncio.gather(*(self.verifier.verify(a.obs.product) for a, _ in big)) if big else []
        for (a, text), v in zip(big, verdicts):
            p, row, ts = a.obs.product, row_of(a), now_iso()
            eid = store.add_error(p, a.kind, a.ref, ts, v.status, v.note, row[1] if row else "celulares")
            if v.status == "corrected":
                store.close_error(eid, "corregido antes del aviso", v.price, ts, 0.3)
                log.info("Error de precio en %s corregido antes de avisar (%s)", p.key, v.note)
                continue
            title, _, rest = text.partition("\n")
            self.notifier.send_animation(godzilla.asset(), f"{title}\n{v.line}\n{rest}", buttons(a))
            if self.hunting and len(self.hunts) < MAX_HUNTS and ":st" not in p.key:   # 🎯 modo caza
                task = asyncio.create_task(self.verifier.hunt(p, eid, a.ref, self.notifier.send))
                self.hunts.add(task)
                task.add_done_callback(self.hunts.discard)
            else:
                store.close_error(eid, "sin seguimiento", p.price, ts, 0)

        # pocas alertas -> una por mensaje con foto y botones 📊🎯🔕; muchas -> agrupadas (evita spam)
        rest = []
        if len(normal) <= 8:
            for a, text in normal:
                row = row_of(a)
                image = (a.obs.product.image if a.obs else None) or (row[2] if row else None)
                self.notifier.send_photo(image, text, buttons(a)) if image else self.notifier.send(text, buttons(a))
        else:
            rest = [t for _, t in normal]
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
