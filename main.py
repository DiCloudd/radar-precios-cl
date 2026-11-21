"""Monitor de precios Chile 🇨🇱 — punto de entrada.

    python main.py                   # un ciclo rápido (página 1 de cada tienda)
    python main.py --deep            # un ciclo profundo (todas las páginas configuradas)
    python main.py --loop            # modo 24/7: rápido cada ~2 min, profundo cada ~20, publica el dashboard
    python main.py --loop --duration 12   # ventana de 12 min (así corre en GitHub Actions cada 15 min)
    python main.py --dry-run         # no envía nada a Telegram (imprime en consola)
    python main.py --summary         # envía el resumen diario ahora
    python main.py --site falabella  # solo una tienda
    python main.py --build-site      # regenera el dashboard local (data/site/index.html)
    python main.py --publish         # genera y publica el dashboard en GitHub Pages
    python main.py --test-telegram   # prueba la conexión con el bot
    python main.py --bot             # solo el bot de Telegram (comandos /precio, /top, /objetivo...)
    python main.py --godzilla-test   # 🦖 envía una alerta Godzilla de prueba
    python main.py --selftest        # ✅ verifica un producto real por tienda (listado + ficha)

Código de salida: 0 = todas las fuentes OK, 1 = alguna falló (útil para cron / Task Scheduler / CI).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import random
import signal
import sys
import time
from logging.handlers import RotatingFileHandler

import config
from analysis import clp
from calendar_cl import active_event


def setup_logging(verbose: bool) -> None:
    for stream in (sys.stdout, sys.stderr):    # consolas Windows (cp1252) no soportan emojis
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = RotatingFileHandler(config.LOG_PATH, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.handlers[:] = [sh, fh]
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def print_report(rep) -> None:
    print("\n" + "=" * 92)
    print(f" CICLO {rep.mode.upper()}  ·  {rep.duration:.0f}s  ·  {len(rep.alerts)} alertas nuevas")
    print("=" * 92)
    for r in rep.results:
        if r.ok:
            ps = [p.price for p in r.products]
            print(f" OK    {r.source.name:<30} {len(r.products):>4} prod. {r.pages}pág  "
                  f"{clp(min(ps))} – {clp(max(ps))}  {r.duration:5.1f}s  {r.method}")
        elif r.skipped:
            print(f" PAUSA {r.source.name:<30} {r.skipped}")
        else:
            print(f" FAIL  {r.source.name:<30} {'[BLOQUEO] ' if r.blocked else ''}{r.error}")
    print("=" * 92)


async def run_once(args) -> int:
    from monitor import Monitor
    if args.site:
        config.SOURCES[:] = [s for s in config.SOURCES if s.site == args.site]
        config.WATCHES[:] = []
    mon = Monitor(dry_run=args.dry_run)
    try:
        rep = await mon.cycle("deep" if args.deep else "fast")
    finally:
        await mon.aclose()
    print_report(rep)
    if args.publish or args.build_site:
        _build_and_publish(publish=args.publish)
    return 1 if rep.failed else 0


def _build_and_publish(publish: bool, essential: bool = True) -> bool:
    """essential=False (publicación periódica dentro de la ventana) respeta el cupo de builds/hora de Pages."""
    import maintenance as M
    from dashboard import build_site
    from publisher import publish as do_publish
    from storage import PriceStore
    log = logging.getLogger("main")
    try:
        builds = None
        with PriceStore(config.DB_PATH) as store:
            if publish and not M.publish_allowed(store, essential):
                log.info("Publicación periódica omitida: cupo de builds/hora de GitHub Pages (queda la final)")
                return False
            if publish:      # se registra ANTES de generar: así el registro viaja dentro de la BD publicada
                builds = M.record_publish(store)
        out = build_site()
        log.info("Dashboard generado en %s", out / "index.html")
        published = bool(publish and do_publish(out))
        with PriceStore(config.DB_PATH) as store:
            M.notify_once_a_day(store, "size", M.size_warnings(M.sizes(out), builds))
        return published
    except Exception as exc:  # noqa: BLE001 - publicar nunca debe detener el monitor
        log.error("Error generando/publicando el dashboard: %s", exc)
        return False


async def run_loop(args) -> None:
    from monitor import Monitor
    log = logging.getLogger("loop")
    mon = Monitor(dry_run=args.dry_run, hunting=True)
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:   # Windows
            pass
    last_deep = 0.0
    last_pub = time.time()             # sin publicación inmediata: 1 a mitad de ventana + 1 final (≤ ~9 builds/h)
    dirty = False                      # hay datos nuevos sin publicar
    deadline = time.time() + args.duration * 60 if args.duration else None
    from telegram_bot import TelegramBot
    bot, bot_stop = TelegramBot(), asyncio.Event()
    bot_task = asyncio.create_task(bot.run(bot_stop)) if bot.enabled else None   # 🤖 responde en paralelo
    from crawler import Crawler
    crawler = Crawler(mon)
    crawl_task = asyncio.create_task(crawler.run(bot_stop)) if config.CRAWL else None   # 🔄 catálogo completo
    mon.crawling = crawl_task is not None              # el 1er escaneo profundo ya no repite lo que rastrea
    import restock
    from storage import PriceStore, now_iso
    try:
        with PriceStore(config.DB_PATH) as store:
            if not store.get_meta(f"{restock.META}:seeded"):          # 1 sola vez: gangas que YA estaban agotadas
                added = restock.seed(store)
                log.info("👀 Vigilancia de stock: %d gangas agotadas", len(added))
                if added:
                    mon.notifier.send(restock.seed_message(added))
    except Exception:  # noqa: BLE001
        log.exception("No se pudo sembrar la vigilancia de stock")
    watcher = restock.RestockWatcher(mon)
    watch_task = asyncio.create_task(watcher.run(bot_stop))   # 🚨 relee las gangas agotadas cada ~2 min
    log.info("🚀 Modo 24/7 iniciado (rápido cada %.0fs, profundo cada %.0fs)", config.FAST_INTERVAL, config.DEEP_INTERVAL)
    try:
        while not stop.is_set():
            if deadline and time.time() + 60 >= deadline:     # sin tiempo para otro ciclo completo
                log.info("⏱️ Ventana de %d min cumplida", args.duration)
                break
            ev = active_event()
            speed = config.EVENT_SPEEDUP if ev else 1.0
            mode = "deep" if time.time() - last_deep >= config.DEEP_INTERVAL / speed else "fast"
            if ev and mode == "deep":
                log.info("🎉 Evento activo: %s (velocidad x%.1f)", ev.name, speed)
            t0 = time.time()
            try:
                rep = await mon.cycle(mode)
                dirty = True
                print_report(rep)
                with PriceStore(config.DB_PATH) as store:        # 🔥 gangas que la app ya muestra -> Telegram
                    pend = restock.pending_from_app(store)
                    if pend:
                        await mon.dispatch(store, pend, now_iso())
                if mode == "deep":
                    last_deep = t0
            except Exception:  # noqa: BLE001
                log.exception("Ciclo falló; se reintenta en el siguiente")
            if time.time() - last_pub >= config.PUBLISH_INTERVAL:
                if await asyncio.to_thread(_build_and_publish, True, False):
                    dirty = False
                last_pub = time.time()
            try:
                mon.daily_summary()
                import maintenance as M
                from storage import PriceStore
                with PriceStore(config.DB_PATH) as store:
                    M.daily_cleanup(store)                       # 🧹 1 vez al día
                    M.notify_once_a_day(store, "budget", M.budget_warnings(M.request_budget()))
            except Exception:  # noqa: BLE001
                log.exception("Resumen/limpieza diaria falló")
            wait = max(10.0, config.FAST_INTERVAL / speed * random.uniform(0.75, 1.25) - (time.time() - t0))
            if deadline:
                wait = min(wait, max(0.0, deadline - time.time()))
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
    finally:
        bot_stop.set()
        for t in (bot_task, crawl_task, watch_task):
            if t:
                t.cancel()
        await asyncio.gather(*(t for t in (bot_task, crawl_task, watch_task) if t), return_exceptions=True)
        log.info("🔄 Rastreador: %d páginas, %d productos en esta ventana", crawler.pages_done, crawler.products_seen)
        log.info("🚨 Vigilancia de stock: %d relecturas en esta ventana", watcher.reads)
        await bot.aclose()
        await mon.aclose()
        if dirty:                      # nunca terminar con datos sin publicar (en Actions se perderían)
            await asyncio.to_thread(_build_and_publish, True)
        log.info("Monitor detenido.")


async def run_selftest() -> int:
    """Autodiagnóstico contra tiendas reales: por cada tienda lee un producto y lo verifica (listado + ficha).
    Como el precio no cambió, lo esperado es ✅ confirmado. Sirve para saber que la verificación funciona en la nube."""
    from fetchers import BrowserPool, HttpFetcher
    from scrapers import SCRAPERS, FetchContext
    from verifier import PDP_SITES, Verifier
    ctx = FetchContext(HttpFetcher(), BrowserPool())
    ver, done, bad, wrong = Verifier(ctx), set(), 0, 0
    print(f"{'tienda':<12} {'lectura':<10} {'veredicto':<12} detalle")
    try:
        for src in config.SOURCES:
            if src.site in done or src.site in ("paris", "lider"):        # bloquean la nube (ya conocido)
                continue
            done.add(src.site)
            res = await SCRAPERS[src.site]().scrape(ctx, src, 1)
            if not res.ok:
                print(f"{src.site:<12} {'FALLA':<10} {'-':<12} {res.error or res.skipped}")
                bad += 1
                continue
            p = res.products[0]
            v = await ver.verify(p, delay=0)
            how = "lis+ficha" if src.site in PDP_SITES and src.site != "maconline" else ("ficha" if src.site == "maconline" else "listado")
            print(f"{src.site:<12} {how:<10} {v.status:<12} {v.note[:70]}  [{p.name[:30]} {p.price}]")
            bad += v.status != "confirmed"
            wrong += v.status == "corrected"            # el precio NO cambió: decir "corrigieron" sería un error real
    finally:
        await ctx.http.aclose()
    ok = len(done) - bad
    print(f"\n{ok}/{len(done)} tiendas verificadas correctamente")
    if bad and not wrong:
        print("::warning::Alguna tienda no se pudo releer (bloqueo o límite de peticiones pasajero); "
              "el monitor la pausa solo y reintenta.")
    # falla solo si la verificación se EQUIVOCA o si la mayoría de las tiendas no se puede verificar
    return 1 if wrong or ok < 0.8 * len(done) else 0


async def run_bot_only() -> None:
    """Solo el bot (sin scrapear): útil para probar comandos en local."""
    from telegram_bot import TelegramBot
    bot = TelegramBot()
    if not bot.enabled:
        logging.error("Falta TELEGRAM_BOT_TOKEN en .env")
        return
    try:
        await bot.run(asyncio.Event())
    finally:
        await bot.aclose()


def main() -> int:
    ap = argparse.ArgumentParser(description="Monitor de precios Chile con alertas a Telegram")
    ap.add_argument("--deep", action="store_true", help="recorre todas las páginas configuradas")
    ap.add_argument("--loop", action="store_true", help="modo 24/7")
    ap.add_argument("--duration", type=float, default=0,
                    help="con --loop: minutos que dura la ventana (GitHub Actions); 0 = infinito")
    ap.add_argument("--dry-run", action="store_true", help="no envía a Telegram")
    ap.add_argument("--site", help="solo una tienda (falabella, paris, pcfactory, lider, abc, hites, entel, samsung, apple)")
    ap.add_argument("--summary", action="store_true", help="envía el resumen diario ahora")
    ap.add_argument("--build-site", action="store_true", help="genera el dashboard local")
    ap.add_argument("--publish", action="store_true", help="genera y publica el dashboard")
    ap.add_argument("--test-telegram", action="store_true")
    ap.add_argument("--bot", action="store_true", help="solo el bot de Telegram (responde comandos)")
    ap.add_argument("--selftest", action="store_true", help="verifica un producto real por tienda (diagnóstico)")
    ap.add_argument("--godzilla-test", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    setup_logging(args.verbose)

    from publisher import restore_db_if_missing
    restore_db_if_missing()

    from notifier import TelegramNotifier
    if args.test_telegram:
        n = TelegramNotifier()
        if not n.live:
            logging.error("Faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID en .env")
            return 1
        return 0 if n.send("✅ <b>Monitor de precios</b>: conexión con Telegram OK.") else 1
    if args.godzilla_test:
        import html as _html

        import godzilla
        from analysis import clp as _clp
        from scrapers import SITE_LABELS
        from storage import PriceStore
        from telegram_bot import alert_buttons
        caption, buttons = "<b>🦖🔥 ALERTA GODZILLA (prueba) 🔥🦖</b>\nAsí se verá cuando un precio se desplome.", None
        if config.DB_PATH.exists():          # simula el error sobre un producto real (los botones funcionan)
            with PriceStore(config.DB_PATH) as store:
                r = store.conn.execute("""SELECT rowid AS rid, * FROM products WHERE missing_since IS NULL
                    AND (name LIKE '%PlayStation 5%' OR name LIKE '%iPhone 17 Pro%') ORDER BY price DESC LIMIT 1""").fetchone()
            if r:
                fake = int(r["price"] * 0.12 // 1000 * 1000 + 990)
                caption = (f"<b>🦖🔥 ALERTA GODZILLA 🔥🦖</b>  <i>(PRUEBA: precio simulado)</i>\n"
                           f"<b>{_html.escape(r['name'])}</b>\n🏬 {SITE_LABELS.get(r['site'], r['site'])}\n"
                           f"💰 <b>{_clp(fake)}</b>  (antes {_clp(r['price'])}, -{(1 - fake / r['price']) * 100:.0f}%)\n"
                           f"   Cayó <b>{(1 - fake / r['price']) * 100:.0f}%</b> en 24 h\n"
                           f"🔗 <a href=\"{_html.escape(r['url'], quote=True)}\">Ver producto</a>")
                buttons = alert_buttons(r["rid"])
        ok = TelegramNotifier(dry_run=args.dry_run).send_animation(godzilla.asset(), caption, buttons)
        return 0 if ok else 1
    if args.summary:
        from monitor import Monitor
        Monitor(dry_run=args.dry_run).daily_summary(force=True)
        return 0
    if args.build_site and not (args.deep or args.site):
        _build_and_publish(publish=args.publish)
        return 0
    if args.publish and not (args.deep or args.site):
        _build_and_publish(publish=True)
        return 0
    if args.loop:
        asyncio.run(run_loop(args))
        return 0
    if args.bot:
        asyncio.run(run_bot_only())
        return 0
    if args.selftest:
        return asyncio.run(run_selftest())
    return asyncio.run(run_once(args))


if __name__ == "__main__":
    sys.exit(main())
