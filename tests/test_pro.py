"""Verificación instantánea, modo caza, salón de la fama, ranking, fotos y categorías nuevas."""
import asyncio
import json

import pytest

import config
import verifier as V
from analysis import evaluate, is_accessory, too_cheap
from models import PageResult, Product
from storage import PriceStore


# ---------------------------------------------------------------- utilidades de prueba
class FakeHttp:
    """Responde fichas de producto (JSON-LD) desde un diccionario url -> (precio, stock)."""
    def __init__(self, pdp=None):
        self.pdp = pdp or {}

    async def get(self, url, **kw):
        if url not in self.pdp:
            raise RuntimeError("404")
        price, stock = self.pdp[url]
        av = "https://schema.org/InStock" if stock else "https://schema.org/OutOfStock"
        ld = {"@type": "Product", "name": "X", "offers": {"price": str(price), "availability": av}}
        return f'<script type="application/ld+json">{json.dumps(ld)}</script>'


class FakeCtx:
    def __init__(self, pdp=None):
        self.http = FakeHttp(pdp)


def listing(monkeypatch, prices):
    """Simula la relectura del listado: prices = lista de precios a devolver en cada lectura (None = no aparece)."""
    seq = iter(prices)

    class FakeScraper:
        async def fetch(self, ctx, url):
            price = next(seq, prices[-1])
            if price is None:
                return PageResult([], "x")
            return PageResult([Product(f"{site}:1", site, "PS5", "https://h.cl/ps5", price) for site in ("hites", "falabella")], "x")
    monkeypatch.setitem(V.SCRAPERS, "hites", FakeScraper)
    monkeypatch.setitem(V.SCRAPERS, "falabella", FakeScraper)


def P(site="hites", price=49990, url="https://h.cl/ps5"):
    return Product(f"{site}:1", site, "Consola PlayStation 5 Slim", url, price, found_at="https://h.cl/list")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "pro.db")
    PriceStore(config.DB_PATH).close()
    return config.DB_PATH


# ---------------------------------------------------------------- ✅ verificación
@pytest.mark.asyncio
async def test_verify_confirmed_listing_and_pdp(monkeypatch):
    listing(monkeypatch, [49990])
    v = await V.Verifier(FakeCtx({"https://h.cl/ps5": (49990, True)})).verify(P(), delay=0)
    assert v.status == "confirmed" and "ficha" in v.note


@pytest.mark.asyncio
async def test_verify_confirmed_listing_only_when_pdp_blocked(monkeypatch):
    listing(monkeypatch, [49990])                          # Falabella: ficha bloqueada -> solo listado
    v = await V.Verifier(FakeCtx()).verify(P("falabella"), delay=0)
    assert v.status == "confirmed" and "2ª lectura" in v.note


@pytest.mark.asyncio
async def test_verify_corrected_in_seconds(monkeypatch):
    listing(monkeypatch, [499990])
    v = await V.Verifier(FakeCtx()).verify(P(), delay=0)
    assert v.status == "corrected"


@pytest.mark.asyncio
async def test_verify_listing_vs_pdp_mismatch_and_no_stock(monkeypatch):
    listing(monkeypatch, [49990])
    v = await V.Verifier(FakeCtx({"https://h.cl/ps5": (499990, True)})).verify(P(), delay=0)
    assert v.status == "unconfirmed" and "ficha dice" in v.note
    v = await V.Verifier(FakeCtx({"https://h.cl/ps5": (49990, False)})).verify(P(), delay=0)
    assert v.status == "unconfirmed" and "SIN STOCK" in v.note


@pytest.mark.asyncio
async def test_verify_unreadable(monkeypatch):
    listing(monkeypatch, [None])
    v = await V.Verifier(FakeCtx()).verify(P("falabella"), delay=0)
    assert v.status == "unconfirmed"


# ---------------------------------------------------------------- 🎯 modo caza
async def run_hunt(db, monkeypatch, prices, pdp=None, minutes=0.05):
    listing(monkeypatch, prices)
    monkeypatch.setattr(V, "jittered", lambda s: 0)
    sent = []
    p = P("falabella")
    with PriceStore(db) as s:
        eid = s.add_error(p, "price_error", 499990, "2026-11-21T10:00:00", "confirmed", "ok", "consolas")
    out = await V.Verifier(FakeCtx(pdp)).hunt(p, eid, 499990, sent.append, minutes=minutes, every=0)
    with PriceStore(db) as s:
        row = s.errors(1)[0]
    return out, sent, row


@pytest.mark.asyncio
async def test_hunt_detects_correction(db, monkeypatch):
    out, sent, row = await run_hunt(db, monkeypatch, [49990, 49990, 489990])
    assert out == "corregido" and "Corrigieron" in sent[-1] and row["outcome"] == "corregido" and row["last_price"] == 489990


@pytest.mark.asyncio
async def test_hunt_detects_removal(db, monkeypatch):
    out, sent, row = await run_hunt(db, monkeypatch, [None, None, None])
    assert out == "retirado" and "retiraron" in sent[-1]


@pytest.mark.asyncio
async def test_hunt_still_active(db, monkeypatch):
    out, sent, row = await run_hunt(db, monkeypatch, [49990], minutes=0.0005)
    assert "Sigue activo" in sent[-1] and row["outcome"] == "seguía activo"


# ---------------------------------------------------------------- 💥 reglas por categoría
@pytest.mark.parametrize("name,cat,price,hit", [
    ("Notebook HP Victus 15 Ryzen 5 16GB 512GB SSD teclado retroiluminado", "notebooks", 89990, True),  # bug corregido
    ("Teclado Logitech para notebook", "notebooks", 19990, False),
    ("Apple iPad Air 11 M3 128GB", "tablets", 59990, True),
    ("Funda iPad Air 11", "tablets", 9990, False),
    ("Audífonos Sony WH-1000XM5 Negro", "audifonos", 19990, True),
    ("Estuche de carga AirPods Pro", "audifonos", 9990, False),
    ("Apple Watch Series 11 GPS 42mm", "smartwatches", 29990, True),
    ("Correa Apple Watch 42mm", "smartwatches", 4990, False),
    ("Refrigerador No Frost Samsung 400L", "electro", 49990, True),
    ("Frigobar 90L Mademsa", "electro", 79990, False),
])
def test_too_cheap_new_categories(name, cat, price, hit):
    assert bool(too_cheap(name, cat, price)) is hit


def test_accessory_only_by_title_start():
    assert is_accessory("Lámina protectora notebook") and not is_accessory("Notebook Lenovo 16GB 512GB SSD")
    assert not is_accessory("Audífonos Bose QuietComfort", "audifonos") and is_accessory("Audífonos Bose QC")


# ---------------------------------------------------------------- 🏆 salón de la fama + 🤡 ranking + 🖼️ foto
def test_dashboard_hall_and_ranking(db):
    from dashboard import build_data
    with PriceStore(db) as s:
        s.observe(Product("a:1", "abc", "TV 55", "https://a.cl/1", 300000, 600000), "g", "src", "2026-11-21T09:00:00", "televisores")
        s.observe(Product("a:2", "abc", "TV 65", "https://a.cl/2", 500000), "g2", "src", "2026-11-21T09:00:00", "televisores")
        eid = s.add_error(Product("a:1", "abc", "TV 55", "https://a.cl/1", 30000), "price_error", 600000,
                          "2026-11-21T10:00:00", "confirmed", "ok", "televisores")
        s.close_error(eid, "corregido", 600000, "2026-11-21T10:07:00", 7)
        s.commit()
        d = build_data(s)
    assert d["hall"]["count"] == 1 and d["hall"]["confirmed"] == 1 and d["hall"]["median_minutes"] == 7
    assert d["errors"][0]["disc"] == 95 and d["errors"][0]["saving"] == 570000
    abc = next(r for r in d["ranking"] if r["site"] == "abc")
    assert abc["tachado_pct"] == 50 and abc["avg_disc"] == 50.0 and abc["index"] == 40


def test_photo_falls_back_to_text(monkeypatch):
    from notifier import TelegramNotifier
    n = TelegramNotifier(token="T", chat_id="1")
    calls = []
    monkeypatch.setattr(n, "_post", lambda m, d, files=None: calls.append(m) or m == "sendMessage")
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert n.send_photo("https://x.cl/img.webp", "hola") and calls == ["sendPhoto", "sendMessage"]


def test_fast_every_rotation():
    src = config.Source("x", "https://x.cl", "abc", fast_every=2)
    picks = [not (not src.fast or n % src.fast_every) for n in range(1, 7)]
    assert picks == [False, True, False, True, False, True]


# ---------------------------------------------------------------- integración: error -> verificación -> aviso -> caza
@pytest.mark.asyncio
async def test_monitor_error_flow_end_to_end(db, monkeypatch):
    from analysis import Alert
    from monitor import CycleReport, Monitor
    mon = Monitor(dry_run=True, hunting=True)
    sent, hunts = [], []

    async def fake_verify(p, delay=None):
        return V.Verdict("confirmed" if p.key == "hites:1" else "corrected", "CONFIRMADO: test")

    async def fake_hunt(p, eid, ref, notify, **kw):
        hunts.append((p.key, eid, ref))
        return "corregido"

    monkeypatch.setattr(mon.verifier, "verify", fake_verify)
    monkeypatch.setattr(mon.verifier, "hunt", fake_hunt)
    monkeypatch.setattr(mon.notifier, "send_animation", lambda gif, text, buttons=None: sent.append(("gif", text, buttons)))
    monkeypatch.setattr(mon.notifier, "send_photo", lambda img, text, buttons=None: sent.append(("photo", text, buttons)))
    monkeypatch.setattr(mon.notifier, "send", lambda text, buttons=None: sent.append(("text", text, buttons)))
    with PriceStore(db) as s:
        real = Product("hites:1", "hites", "Consola PlayStation 5 Slim", "https://h.cl/ps5", 49990, image="https://h.cl/i.jpg")
        fixed = Product("hites:2", "hites", "Consola Xbox Series X", "https://h.cl/xbox", 39990)
        drop = Product("hites:3", "hites", "Smart TV 55", "https://h.cl/tv", 300000, image="https://h.cl/tv.jpg")
        o1 = s.observe(real, "g1", "src", "2026-11-21T10:00:00", "consolas")
        o2 = s.observe(fixed, "g2", "src", "2026-11-21T10:00:00", "consolas")
        o3 = s.observe(drop, "g3", "src", "2026-11-21T10:00:00", "televisores")
        fresh = [(Alert("price_error", o1, "💥 ¿ERROR DE PRECIO?", ["x"], 0, 499990), "💥 ¿ERROR DE PRECIO?\nPS5"),
                 (Alert("price_error", o2, "💥 ¿ERROR DE PRECIO?", ["x"], 0, 599990), "💥 ¿ERROR DE PRECIO?\nXbox"),
                 (Alert("drop", o3, "📉 Bajó de precio", [], 3), "📉 Bajó de precio\nTV")]
        await mon._notify(fresh, CycleReport("fast"), s)
        await asyncio.gather(*mon.hunts)
        errs = {e["key"]: dict(e) for e in s.errors()}
    gifs = [t for kind, t, _ in sent if kind == "gif"]
    assert len(gifs) == 1 and "✅" in gifs[0] and "PS5" in gifs[0]           # el corregido NO se avisa
    assert [k for k, *_ in sent].count("photo") == 1                          # la bajada va con foto
    assert all(b and b["inline_keyboard"] for _, _, b in sent)                # todos con botones 📊🎯🔕
    assert errs["hites:2"]["outcome"] == "corregido antes del aviso" and errs["hites:1"]["verdict"] == "confirmed"
    assert hunts == [("hites:1", errs["hites:1"]["id"], 499990)]
    await mon.aclose()


def test_stock_any_variant():
    from utils import ld_in_stock
    assert ld_in_stock({"offers": [{"availability": "https://schema.org/OutOfStock"},
                                   {"availability": "https://schema.org/InStock"}]}) is True
    assert ld_in_stock({"offers": [{"availability": "https://schema.org/OutOfStock"}]}) is False
    assert ld_in_stock({"offers": []}) is None
