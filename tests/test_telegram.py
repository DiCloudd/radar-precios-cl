"""Bot de Telegram con una API simulada: dueño, comandos, botones y gráfico."""
import pytest

import config
import userprefs
from charts import price_chart
from models import Product
from storage import PriceStore
from telegram_bot import TelegramBot


class FakeBot(TelegramBot):
    def __init__(self):
        super().__init__(token="TEST")
        self.calls = []

    async def api(self, method, data=None, files=None):
        self.calls.append((method, data or {}, files))
        return {"ok": True, "result": []}

    def texts(self):
        return [d.get("text", d.get("caption", "")) for m, d, _ in self.calls if m in ("sendMessage", "sendPhoto")]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "bot.db")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    with PriceStore(config.DB_PATH) as s:
        s.observe(Product("paris:A57", "paris", "Samsung Galaxy A57 128GB", "https://www.paris.cl/a57.html", 399990),
                  "samsung galaxy a57 128gb", "src", "2026-11-01T10:00:00")
        s.observe(Product("hites:A57", "hites", "Galaxy A57 / 128 GB", "https://www.hites.com/a57.html", 389990,
                          brand="Samsung"), "samsung galaxy a57 128gb", "src", "2026-11-01T10:00:00")
        s.commit()
    return config.DB_PATH


def msg(text, chat=111):
    return {"update_id": 1, "message": {"chat": {"id": chat}, "text": text}}


@pytest.mark.asyncio
async def test_owner_registration_and_privacy(db):
    bot = FakeBot()
    await bot.handle(msg("/precio a57"))                    # antes de /start
    assert "Envía /start" in bot.texts()[-1]
    await bot.handle(msg("/start"))
    with PriceStore(db) as s:
        assert userprefs.owner_chat(s) == "111"
    await bot.handle(msg("/precio a57", chat=999))         # un extraño
    assert "privado" in bot.texts()[-1]


@pytest.mark.asyncio
async def test_precio_comparar_objetivo_seguir(db):
    bot = FakeBot()
    await bot.handle(msg("/start"))
    await bot.handle(msg("/precio galaxy a57"))
    assert "$389.990" in bot.texts()[-1] and "Hites" in bot.texts()[-1]
    await bot.handle(msg("/comparar galaxy a57"))
    assert "ahorras $10.000" in bot.texts()[-1]
    await bot.handle(msg("/objetivo 350.000 galaxy a57"))
    with PriceStore(db) as s:
        t = userprefs.all_targets(s)[-1]
        assert t.target_price == 350000 and t.matches("x:1", "Samsung Galaxy A57 5G", "paris")
    await bot.handle(msg("/seguir https://www.falabella.com/falabella-cl/product/1/x"))
    with PriceStore(db) as s:
        assert userprefs.all_watches(s)[-1].url.endswith("/product/1/x")
    await bot.handle(msg("/borrar 1"))
    assert "Borrado" in bot.texts()[-1]


@pytest.mark.asyncio
async def test_buttons_target_mute_and_chart(db):
    bot = FakeBot()
    await bot.handle(msg("/start"))
    with PriceStore(db) as s:
        rid = s.conn.execute("SELECT rowid FROM products WHERE key='paris:A57'").fetchone()[0]
    cb = lambda data: {"update_id": 2, "callback_query": {"id": "q", "data": data, "message": {"chat": {"id": 111}}}}
    await bot.handle(cb(f"t:{rid}"))
    await bot.handle(cb(f"m:{rid}"))
    await bot.handle(cb(f"h:{rid}"))
    with PriceStore(db) as s:
        t = userprefs.all_targets(s)[-1]
        assert (t.product_key, t.target_price) == ("paris:A57", int(399990 * 0.95))
        assert userprefs.is_muted(s, "paris:A57")
    photo = [f for m, _, f in bot.calls if m == "sendPhoto"][0]["photo"][1]
    assert photo[:4] == b"\x89PNG"


def test_chart_png_without_history():
    assert price_chart("Producto", "Tienda", [], 100000)[:4] == b"\x89PNG"
