"""Telegram (API REST, parse_mode=HTML). Sin token configurado, todo se imprime en consola."""
from __future__ import annotations

import html
import json
import logging
import time
from pathlib import Path

import httpx

import config
from analysis import Alert, clp

log = logging.getLogger("notifier")
TG_LIMIT = 4000


def _pct(x: float) -> str:
    return f"{x:+.1f}%".replace(".", ",")


def format_alert(a: Alert, site_labels: dict[str, str], fake: str | None = None) -> str:
    if a.obs is None:
        return f"<b>{a.title}</b>\n" + "\n".join(a.lines)
    o, p = a.obs, a.obs.product
    out = [f"<b>{a.title}</b>", f"<b>{html.escape(p.name)}</b>", f"🏬 {site_labels.get(p.site, p.site)}"]
    if o.previous is not None and o.delta:
        out.append(f"💰 <b>{clp(p.price)}</b>  (antes {clp(o.previous)}, {_pct(o.pct)})")
    else:
        out.append(f"💰 <b>{clp(p.price)}</b>")
    extra = []
    if p.card_price:
        extra.append(f"💳 tarjeta tienda {clp(p.card_price)}")
    if p.list_price:
        extra.append(f"normal <s>{clp(p.list_price)}</s>")
    if p.installments:
        extra.append(f"{p.installments} cuotas")
    if extra:
        out.append("   " + " · ".join(extra))
    out += [f"   {line}" for line in a.lines]
    if fake:
        out.append(f"🤡 <i>Oferta sospechosa: {html.escape(fake)}</i>")
    if p.badges:
        out.append("🏷️ " + html.escape(" · ".join(p.badges[:3])))
    out.append(f'🔗 <a href="{html.escape(p.url, quote=True)}">Ver producto</a>')
    return "\n".join(out)


def chunk(blocks: list[str], header: str) -> list[str]:
    msgs, cur = [], header
    for b in blocks:
        if len(cur) + len(b) + 2 > TG_LIMIT:
            msgs.append(cur)
            cur = header + " (cont.)"
        cur += "\n\n" + b
    if cur not in (header, header + " (cont.)"):
        msgs.append(cur)
    return msgs


class TelegramNotifier:
    def __init__(self, token: str = config.TELEGRAM_BOT_TOKEN, chat_id: str | None = None,
                 dry_run: bool = False) -> None:
        self.token, self._chat, self.dry_run = token, chat_id, dry_run

    @property
    def chat_id(self) -> str | None:
        """Chat fijo (.env/secret) o el que se registró enviando /start al bot (guardado en la BD)."""
        if self._chat or config.TELEGRAM_CHAT_ID:
            return self._chat or config.TELEGRAM_CHAT_ID
        try:
            from storage import PriceStore
            with PriceStore(config.DB_PATH) as store:
                return store.get_meta("tg:chat")
        except Exception:  # noqa: BLE001
            return None

    @property
    def live(self) -> bool:
        return bool(self.token and self.chat_id) and not self.dry_run

    def _post(self, method: str, data: dict, files: dict | None = None) -> bool:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        for attempt in range(3):
            try:
                r = httpx.post(url, data=data, files=files, timeout=30)
                if r.status_code == 429:
                    time.sleep(r.json().get("parameters", {}).get("retry_after", 5) + 1)
                    continue
                if r.is_success:
                    return True
                log.error("Telegram %s -> %s: %s", method, r.status_code, r.text[:300])
                return False
            except httpx.HTTPError as exc:
                log.error("Error Telegram: %s", exc)
                time.sleep(2 * (attempt + 1))
        return False

    def send(self, text: str, buttons: dict | None = None) -> bool:
        if not self.live:
            print("\n--- [consola] mensaje Telegram ---\n" + text + "\n--- fin ---")
            return True
        data = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if buttons:
            data["reply_markup"] = json.dumps(buttons)
        ok = self._post("sendMessage", data)
        time.sleep(1.1)  # ~1 msg/s por chat
        return ok

    def send_animation(self, gif: Path, caption: str, buttons: dict | None = None) -> bool:
        if not self.live:
            print(f"\n--- [consola] GIF {gif.name} + mensaje ---\n{caption}\n--- fin ---")
            return True
        data = {"chat_id": self.chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
        if buttons:
            data["reply_markup"] = json.dumps(buttons)
        with open(gif, "rb") as fh:
            ok = self._post("sendAnimation", data, files={"animation": (gif.name, fh, "image/gif")})
        time.sleep(1.1)
        return ok or self.send(caption, buttons)

    def send_many(self, messages: list[str]) -> int:
        return sum(self.send(m) for m in messages)
