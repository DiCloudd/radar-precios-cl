"""Bot interactivo de Telegram (nivel 3): comandos, botones bajo las alertas y gráficos PNG.

Funciona con long-polling (getUpdates), así que no necesita servidor público: corre dentro de cada ventana
de GitHub Actions. Lo que escribas entre ventanas queda en cola en Telegram (24 h) y se responde al inicio
de la siguiente. El primer chat que envía /start queda como dueño; el bot ignora a cualquier otro.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import unicodedata

import httpx

import config
import userprefs
from analysis import clp
from charts import price_chart
from matcher import comparable
from scrapers import SITE_LABELS
from scrapers.product_page import SITE_BY_DOMAIN
from storage import PriceStore, ago
from utils import parse_price

log = logging.getLogger("telegram")

COMMANDS = [
    ("precio", "Buscar: /precio galaxy a57 · /precio notebook lenovo · /precio ps5"),
    ("top", "Mayores bajadas de las últimas 24 h"),
    ("errores", "🏆 Últimos errores de precio: cuánto duraron y cuánto ahorrabas"),
    ("comparar", "Mismo modelo en todas las tiendas: /comparar iphone 17"),
    ("grafico", "Gráfico del historial: /grafico iphone 17 256"),
    ("objetivo", "Avisar bajo un precio: /objetivo 350000 galaxy a57"),
    ("objetivos", "Ver y borrar tus objetivos"),
    ("seguir", "Vigilar un producto por su link: /seguir https://..."),
    ("siguiendo", "Ver y dejar de seguir productos"),
    ("estado", "Salud de cada tienda"),
    ("resumen", "Resumen del día ahora"),
    ("ayuda", "Todos los comandos"),
]
HELP = ("📡 <b>Radar de Precios</b>\n\n" + "\n".join(f"/{c} — {html.escape(d)}" for c, d in COMMANDS) +
        "\n\nBajo cada alerta tienes botones: 📊 historial · 🎯 avisar si baja 5% · 🔕 silenciar 24 h."
        "\n\n⏱️ Respondo en segundos mientras el radar está activo; si escribes entre ventanas, "
        "te contesto apenas parta la siguiente (máx. unos minutos).")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def alert_buttons(rowid: int) -> dict:
    return {"inline_keyboard": [[
        {"text": "📊 Historial", "callback_data": f"h:{rowid}"},
        {"text": "🎯 Avisar -5%", "callback_data": f"t:{rowid}"},
        {"text": "🔕 24 h", "callback_data": f"m:{rowid}"},
    ]]}


def search(store: PriceStore, query: str, limit: int = 8) -> list:
    words = _norm(query).split()
    if not words:
        return []
    rows = store.conn.execute("SELECT rowid AS rid, * FROM products WHERE missing_since IS NULL").fetchall()
    hay = lambda r: _norm(f"{r['name']} {r['brand'] or ''} {r['site']} {r['category'] or ''} "
                          f"{config.CATEGORY_LABELS.get(r['category'] or '', '')}")
    hits = [r for r in rows if all(w in hay(r) for w in words)]
    return sorted(hits, key=lambda r: r["price"])[:limit]


class TelegramBot:
    def __init__(self, token: str = config.TELEGRAM_BOT_TOKEN) -> None:
        self.token = token
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(45, connect=15))
        self.username = ""

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def api(self, method: str, data: dict | None = None, files: dict | None = None) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        payload = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in (data or {}).items() if v is not None}
        r = await self.client.post(url, data=payload, files=files)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method}: {r.status_code} {body.get('description', r.text[:200])}")
        return body

    async def reply(self, chat: str | int, text: str, buttons: dict | None = None) -> None:
        await self.api("sendMessage", {"chat_id": chat, "text": text[:4096], "parse_mode": "HTML",
                                       "disable_web_page_preview": "true", "reply_markup": buttons})

    # ------------------------------------------------------------------ bucle
    async def setup(self) -> None:
        me = await self.api("getMe")
        self.username = me["result"].get("username", "")
        with PriceStore(config.DB_PATH) as store:
            version = hashlib.md5(repr(COMMANDS).encode()).hexdigest()[:8]   # cambia si edito comandos
            if store.get_meta("tg:commands_v") != version:
                await self.api("setMyCommands", {"commands": [{"command": c, "description": d[:256]} for c, d in COMMANDS]})
                store.set_meta("tg:commands_v", version)

    async def poll_once(self, timeout: int = 0) -> int:
        with PriceStore(config.DB_PATH) as store:
            offset = store.get_meta("tg:offset")
        res = await self.api("getUpdates", {"offset": offset, "timeout": timeout,
                                            "allowed_updates": ["message", "callback_query"]})
        updates = res.get("result", [])
        for upd in updates:
            try:
                await self.handle(upd)
            except Exception:  # noqa: BLE001 - un comando roto no detiene el bot
                log.exception("Error procesando update %s", upd.get("update_id"))
            with PriceStore(config.DB_PATH) as store:
                store.set_meta("tg:offset", upd["update_id"] + 1)
        return len(updates)

    async def run(self, stop: asyncio.Event) -> None:
        try:
            await self.setup()
            log.info("🤖 Bot de Telegram escuchando")       # sin el @nombre: los logs de Actions son públicos
        except Exception as exc:  # noqa: BLE001
            log.error("No se pudo iniciar el bot: %s", exc)
            return
        while not stop.is_set():
            try:
                await self.poll_once(timeout=25)
            except (httpx.HTTPError, RuntimeError) as exc:
                log.warning("Polling Telegram: %s", exc)
                await asyncio.sleep(5)

    # ------------------------------------------------------------------ despacho
    async def handle(self, upd: dict) -> None:
        if "callback_query" in upd:
            return await self.on_callback(upd["callback_query"])
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat = str((msg.get("chat") or {}).get("id", ""))
        if not chat or not text:
            return
        with PriceStore(config.DB_PATH) as store:
            owner = userprefs.owner_chat(store)
            if not owner:
                if text.startswith("/start"):
                    userprefs.set_owner_chat(store, chat)
                    log.info("Chat dueño registrado")
                    return await self.reply(chat, "✅ ¡Listo! Este chat recibirá las alertas del radar.\n\n" + HELP)
                return await self.reply(chat, "Envía /start para activar el radar en este chat.")
            if chat != str(owner):
                return await self.reply(chat, "🔒 Este radar es privado.")
            m = re.match(r"^/(\w+)(?:@\w+)?\s*(.*)$", text, re.S)
            if not m:
                return await self.reply(chat, "Usa un comando, por ejemplo /precio iphone 17 · /ayuda")
            cmd, arg = m.group(1).lower(), m.group(2).strip()
            handler = getattr(self, f"cmd_{cmd}", None)
            if handler is None:
                return await self.reply(chat, "No conozco ese comando 🤔\n\n" + HELP)
            await handler(store, chat, arg)

    # ------------------------------------------------------------------ comandos
    async def cmd_start(self, store, chat, arg):
        await self.reply(chat, HELP)

    cmd_ayuda = cmd_help = cmd_start

    async def cmd_precio(self, store, chat, arg):
        if not arg:
            return await self.reply(chat, "¿Qué busco? Ejemplo: /precio galaxy a57")
        rows = search(store, arg)
        if not rows:
            return await self.reply(chat, f"No encontré «{html.escape(arg)}» en las tiendas monitoreadas.")
        lines = [f"🔎 <b>{html.escape(arg)}</b> — {len(rows)} resultado(s), del más barato:"]
        for i, r in enumerate(rows, 1):
            extra = f" · 💳 {clp(r['card_price'])}" if r["card_price"] else ""
            emoji = config.CATEGORY_LABELS.get(r["category"] or "", "• x").split()[0]
            lines.append(f"{i}. {emoji} <b>{clp(r['price'])}</b> {SITE_LABELS.get(r['site'], r['site'])}{extra}\n"
                         f"    <a href=\"{html.escape(r['url'], quote=True)}\">{html.escape(r['name'][:70])}</a>")
        buttons = {"inline_keyboard": [[{"text": f"📊 {i}", "callback_data": f"h:{r['rid']}"}
                                        for i, r in enumerate(rows[:5], 1)]]}
        await self.reply(chat, "\n".join(lines), buttons)

    async def cmd_top(self, store, chat, arg):
        since = ago(hours=24)
        rows = store.conn.execute("""
            SELECT p.rowid AS rid, p.name, p.site, p.price, p.url,
                   (SELECT price FROM prices h WHERE h.key=p.key AND h.ts<? ORDER BY ts DESC LIMIT 1) AS old
            FROM products p WHERE p.missing_since IS NULL""", (since,)).fetchall()
        drops = sorted(((r, (r["price"] / r["old"] - 1) * 100) for r in rows if r["old"] and r["price"] < r["old"]),
                       key=lambda x: x[1])[:10]
        if not drops:
            return await self.reply(chat, "Sin bajadas en las últimas 24 h todavía. El radar sigue mirando 📡")
        lines = ["📉 <b>Top bajadas 24 h</b>"]
        for i, (r, pct) in enumerate(drops, 1):
            lines.append(f"{i}. <b>{clp(r['price'])}</b> ({pct:+.0f}%) {SITE_LABELS.get(r['site'])} — "
                         f"<a href=\"{html.escape(r['url'], quote=True)}\">{html.escape(r['name'][:55])}</a>")
        await self.reply(chat, "\n".join(lines))

    async def cmd_errores(self, store, chat, arg):
        rows = store.errors(10)
        if not rows:
            return await self.reply(chat, "🦖 Aún no hay errores de precio registrados. ¡Te aviso apenas una tienda se equivoque!")
        icons = {"corregido": "🛑", "agotado": "⛔", "retirado": "👻", "en curso": "🎯", "corregido antes del aviso": "⚡"}
        lines = ["🏆 <b>Últimos errores de precio</b>"]
        for e in rows:
            ref = e["ref_price"] or 0
            disc = f" (-{(1 - e['error_price'] / ref) * 100:.0f}%)" if ref > e["error_price"] else ""
            dur = f" · {e['minutes']:.0f} min" if e["minutes"] else ""
            lines.append(f"{icons.get(e['outcome'], '⏰')} <b>{clp(e['error_price'])}</b>{disc} {SITE_LABELS.get(e['site'], e['site'])}{dur}\n"
                         f"    <a href=\"{html.escape(e['url'] or '', quote=True)}\">{html.escape((e['name'] or '')[:60])}</a> · {e['outcome']}")
        await self.reply(chat, "\n".join(lines))

    async def cmd_comparar(self, store, chat, arg):
        if not arg:
            return await self.reply(chat, "Ejemplo: /comparar iphone 17 256")
        words = _norm(arg).split()
        rows = store.conn.execute("SELECT * FROM products WHERE missing_since IS NULL AND group_key<>''").fetchall()
        groups: dict[str, list] = {}
        for r in rows:
            if comparable(r["group_key"]) and all(w in r["group_key"] for w in words):
                groups.setdefault(r["group_key"], []).append(r)
        multi = sorted(((g, v) for g, v in groups.items() if len({r["site"] for r in v}) >= 2),
                       key=lambda gv: -len({r["site"] for r in gv[1]}))[:3]
        if not multi:
            return await self.reply(chat, f"No encontré «{html.escape(arg)}» en 2+ tiendas. Prueba /precio {html.escape(arg)}")
        out = []
        for g, items in multi:
            best: dict[str, object] = {}
            for r in sorted(items, key=lambda r: r["price"]):
                best.setdefault(r["site"], r)
            ordered = sorted(best.values(), key=lambda r: r["price"])
            out.append(f"🔀 <b>{html.escape(g)}</b> (ahorras {clp(ordered[-1]['price'] - ordered[0]['price'])})")
            out += [f"{'🥇' if i == 0 else '  '} <b>{clp(r['price'])}</b> "
                    f"<a href=\"{html.escape(r['url'], quote=True)}\">{SITE_LABELS.get(r['site'])}</a>"
                    for i, r in enumerate(ordered)]
        await self.reply(chat, "\n".join(out))

    async def cmd_grafico(self, store, chat, arg):
        rows = search(store, arg, 1) if arg else []
        if not rows:
            return await self.reply(chat, "Ejemplo: /grafico iphone 17 256 (busca y grafica el más barato)")
        await self.send_chart(store, chat, rows[0])

    async def cmd_objetivo(self, store, chat, arg):
        tokens = arg.split()
        price_tok = next((t for t in tokens if re.fullmatch(r"\$?[\d.]{4,}", t)), None)
        price = parse_price(price_tok) if price_tok else None
        words = [t for t in tokens if t != price_tok]
        if not price or not words:
            return await self.reply(chat, "Ejemplo: /objetivo 350000 galaxy a57\n(te aviso cuando un producto con esas palabras baje de ese precio)")
        label = f"{' '.join(words)} bajo {clp(price)}"
        userprefs.add_target(store, label, price, tuple(w.lower() for w in words))
        now = [r for r in search(store, " ".join(words), 50) if r["price"] <= price]
        tail = (f"\n\n👀 Ojo: ya hay {len(now)} bajo ese precio, ej. <b>{clp(now[0]['price'])}</b> en "
                f"{SITE_LABELS.get(now[0]['site'])}: /precio {html.escape(' '.join(words))}") if now else ""
        await self.reply(chat, f"🎯 Objetivo guardado: <b>{html.escape(label)}</b>{tail}")

    async def cmd_objetivos(self, store, chat, arg):
        fixed = [f"• {html.escape(t.label)} <i>(fijo en sources.toml)</i>" for t in config.TARGETS]
        mine = [f"{i}. {html.escape(t['label'])}" for i, t in enumerate(userprefs.db_targets(store), 1)]
        if not fixed and not mine:
            return await self.reply(chat, "No tienes objetivos. Crea uno: /objetivo 350000 galaxy a57")
        await self.reply(chat, "🎯 <b>Tus objetivos</b>\n" + "\n".join(mine + fixed) +
                         ("\n\nBorrar: /borrar 1" if mine else ""))

    async def cmd_borrar(self, store, chat, arg):
        gone = userprefs.remove_target(store, int(arg) - 1) if arg.isdigit() else None
        await self.reply(chat, f"🗑️ Borrado: {html.escape(gone['label'])}" if gone else "Uso: /borrar <número> (ver /objetivos)")

    async def cmd_seguir(self, store, chat, arg):
        if not re.match(r"^https?://\S+$", arg):
            return await self.reply(chat, "Pega el link del producto: /seguir https://www.falabella.com/...")
        supported = any(k in arg for k in SITE_BY_DOMAIN)
        added = userprefs.add_watch(store, arg)
        note = "" if supported else "\n⚠️ Esa tienda no está en mi lista; intentaré leer el precio igual."
        await self.reply(chat, ("👁️ Lo sigo desde el próximo ciclo: te aviso si baja, se agota o desaparece." if added
                                else "Ya lo estaba siguiendo 👍") + note)

    async def cmd_siguiendo(self, store, chat, arg):
        items = userprefs.db_watches(store)
        if not items:
            return await self.reply(chat, "No sigues productos. Usa /seguir <link>")
        lines = [f"{i}. <a href=\"{html.escape(w['url'], quote=True)}\">{html.escape(w['name'][:60])}</a>"
                 for i, w in enumerate(items, 1)]
        await self.reply(chat, "👁️ <b>Siguiendo</b>\n" + "\n".join(lines) + "\n\nDejar: /dejar 1")

    async def cmd_dejar(self, store, chat, arg):
        gone = userprefs.remove_watch(store, int(arg) - 1) if arg.isdigit() else None
        await self.reply(chat, f"👋 Dejé de seguir: {html.escape(gone['name'])}" if gone else "Uso: /dejar <número> (ver /siguiendo)")

    async def cmd_estado(self, store, chat, arg):
        import time as _t
        from datetime import datetime as _dt
        by_site: dict[str, list] = {}
        for src in config.SOURCES:
            by_site.setdefault(src.site, []).append(src)
        lines = ["🩺 <b>Estado y cobertura</b>"]
        for site, srcs in by_site.items():
            block = store.get_meta(f"block:{site}") or {}
            if block.get("until", 0) > _t.time():
                lines.append(f"⏸️ {SITE_LABELS.get(site)}: pausada por bloqueo (×{block.get('strikes')})")
                continue
            ok = sum(1 for s_ in srcs if (r := store.recent_runs(s_.name, 1)) and r[0]["ok"])
            sweeps = [store.get_meta(f"sweep:{s_.name}") for s_ in srcs]
            done = [_dt.fromisoformat(w["done"]) for w in sweeps if w]
            cov = ""
            if done:
                oldest = (_dt.now() - min(done)).total_seconds() / 60
                cov = f" · 🔄 todo revisado hace ≤{oldest:.0f} min"
            n = store.conn.execute("SELECT COUNT(*) FROM products WHERE site=? AND missing_since IS NULL", (site,)).fetchone()[0]
            lines.append(f"{'✅' if ok else '❔'} {SITE_LABELS.get(site)}: {n} prod.{cov}")
        total = store.conn.execute("SELECT COUNT(*) FROM products WHERE missing_since IS NULL").fetchone()[0]
        fresh = store.conn.execute("SELECT COUNT(*) FROM products WHERE missing_since IS NULL AND last_seen>=?",
                                   (ago(minutes=60),)).fetchone()[0]
        lines.append(f"\n📦 {total} productos · {fresh} revisados en la última hora ({fresh / max(total, 1) * 100:.0f}%)")
        lines.append(f"🌐 {config.PUBLIC_SITE_URL or 'dashboard local'}")
        await self.reply(chat, "\n".join(lines))

    async def cmd_resumen(self, store, chat, arg):
        from monitor import build_summary
        await self.reply(chat, build_summary(store))

    # ------------------------------------------------------------------ botones
    async def on_callback(self, cq: dict) -> None:
        chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
        data = cq.get("data") or ""
        with PriceStore(config.DB_PATH) as store:
            if chat != str(userprefs.owner_chat(store) or ""):
                return await self.api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "🔒 Privado"})
            kind, _, rid = data.partition(":")
            row = store.conn.execute("SELECT rowid AS rid, * FROM products WHERE rowid=?", (rid,)).fetchone() if rid.isdigit() else None
            if row is None:
                return await self.api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Producto no encontrado"})
            if kind == "h":
                await self.api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "📊 Generando gráfico…"})
                await self.send_chart(store, chat, row)
            elif kind == "t":
                target = int(row["price"] * 0.95)
                userprefs.add_target(store, f"{row['name'][:50]} bajo {clp(target)}", target, product_key=row["key"])
                await self.api("answerCallbackQuery", {"callback_query_id": cq["id"],
                                                       "text": f"🎯 Te aviso si baja de {clp(target)}", "show_alert": "true"})
            elif kind == "m":
                userprefs.mute(store, row["key"], 24)
                await self.api("answerCallbackQuery", {"callback_query_id": cq["id"],
                                                       "text": "🔕 Silenciado 24 h (salvo 🦖 Godzilla)", "show_alert": "true"})

    async def send_chart(self, store: PriceStore, chat, row) -> None:
        hist = [(h["ts"], h["price"]) for h in store.history(row["key"])]
        png = price_chart(row["name"], SITE_LABELS.get(row["site"], row["site"]), hist, row["price"])
        prices = [p for _, p in hist] or [row["price"]]
        caption = (f"📊 <b>{html.escape(row['name'][:80])}</b>\n{SITE_LABELS.get(row['site'])} · actual <b>{clp(row['price'])}</b>"
                   f" · mín {clp(min(prices))} · {len(hist)} cambio(s) registrados\n"
                   f"<a href=\"{html.escape(row['url'], quote=True)}\">Ver en la tienda</a>")
        await self.api("sendPhoto", {"chat_id": chat, "caption": caption, "parse_mode": "HTML",
                                     "reply_markup": alert_buttons(row["rid"])},
                       files={"photo": ("historial.png", png, "image/png")})
