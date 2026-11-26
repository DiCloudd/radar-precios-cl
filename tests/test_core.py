"""Precios, agrupación entre tiendas, historial compacto y reglas de alerta."""
from datetime import datetime, timedelta

import pytest

import config
from analysis import evaluate, fake_discount, predict
from fetchers import looks_blocked
from matcher import comparable, group_key
from models import Product
from storage import PriceStore
from utils import parse_price


@pytest.mark.parametrize("raw,expected", [
    ("$  329.990", 329990), ("329990", 329990), (329990.0, 329990), (["1.469.990"], 1469990),
    ("449990.000000", 449990), ("499989.999", 499990), ("99990.0", 99990), ("$1.299,50", 1299), ("", None),
    (None, None), ("gratis", None), (0, None),
])
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected


@pytest.mark.parametrize("a,b", [
    (("Celular Samsung Galaxy A57 5G 128GB - Navy", None), ("Smartphone Galaxy A57 5G 128 GB Negro.", "Samsung")),
    (("Apple iPhone 17 Pro Max 256GB Plata", None), ("iPhone 17 Pro Max 256 GB Naranjo Liberado", "APPLE")),
    (("Celular ZTE Nubia Neo 5GT 256GB/8GB 5G Negro Liberado+Gamepad", None), ("ZTE Nubia Neo 5GT 256GB", "ZTE")),
    (("HONOR 600 Smart 256GB", "Honor"), ("Smartphone Honor 600 Smart / 256 GB / Negro / Liberado", "Honor")),
])
def test_group_key_matches(a, b):
    assert group_key(*a) == group_key(*b)


def test_group_key_separates():
    assert group_key("IPhone 13 128GB Reacondicionado") != group_key("iPhone 13 128GB", "Apple")
    assert group_key("Galaxy S26 256GB", "Samsung") != group_key("Galaxy S26 512GB", "Samsung")
    assert group_key("Redmi Note 17 Pro+ 512GB", "Xiaomi") != group_key("Redmi Note 17 Pro 512GB", "Xiaomi")
    assert group_key("Xiaomi Redmi A7 4G 64 GB 6,88\" Negro") == "xiaomi redmi a7 64gb"
    assert not comparable(group_key("Samsung Galaxy S26"))


def test_block_detection():
    assert looks_blocked(403, "x", "https://a.cl")
    assert looks_blocked(200, "<title>Robot or human?</title>", "https://www.lider.cl")
    assert looks_blocked(200, "", "https://www.mercadolibre.cl/gz/account-verification?go=x")
    assert not looks_blocked(200, "<html>" + "captcha " * 10 + "x" * 300_000, "https://a.cl")


@pytest.fixture
def store(tmp_path):
    s = PriceStore(tmp_path / "t.db")
    yield s
    s.close()


def P(price, list_price=None, key="t:1", name="Galaxy A57 128GB"):
    return Product(key, "t", name, "https://t.cl/1", price, list_price)


def ts(hours_ago):
    return (datetime.now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def test_history_only_on_change(store):
    for h, price in [(5, 100), (4, 100), (3, 100), (2, 90)]:
        store.observe(P(price), "g", "src", ts(h))
    assert [r["price"] for r in store.history("t:1")] == [100, 90]


def test_godzilla(store):
    store.observe(P(500_000), "g", "src", ts(10))
    obs = store.observe(P(200_000), "g", "src", ts(0))
    kinds = [a.kind for a in evaluate(obs, store, watched=False)]
    assert "godzilla" in kinds


def test_historic_low_needs_history(store, monkeypatch):
    monkeypatch.setattr(config, "MIN_HISTORY_HOURS", 336)
    store.observe(P(100_000), "g", "src", ts(10))
    obs = store.observe(P(88_000), "g", "src", ts(0))
    kinds = [a.kind for a in evaluate(obs, store, watched=False)]
    assert "historic_low" not in kinds and "drop" in kinds          # solo 10 h de historia
    store.observe(P(100_000, key="t:2"), "g", "src", ts(400))          # 14+ días de historia y -12 %
    obs = store.observe(P(88_000, key="t:2"), "g", "src", ts(0))
    assert "historic_low" in [a.kind for a in evaluate(obs, store, watched=False)]


def test_price_error_vs_other_stores(store):
    gk = "samsung galaxy a57 128gb"
    for i, price in enumerate([400_000, 410_000, 390_000]):
        store.observe(P(price, key=f"o:{i}"), gk, "src", ts(5))
    obs = store.observe(P(99_990, key="x:1"), gk, "src", ts(0))
    assert "price_error" in [a.kind for a in evaluate(obs, store, watched=False)]


def test_fake_discount_inflated_list(store):
    store.observe(P(300_000, 320_000), "g", "src", ts(24 * 10))
    obs = store.observe(P(300_000, 600_000), "g", "src", ts(0))
    assert "precio 'normal'" in (fake_discount(obs, store) or "")


def test_target_alert(store, monkeypatch):
    monkeypatch.setattr(config, "TARGETS", [config.Target("A57 barato", 350_000, ("galaxy", "a57"))])
    store.observe(P(380_000), "g", "src", ts(5))
    obs = store.observe(P(345_000), "g", "src", ts(0))
    assert "target" in [a.kind for a in evaluate(obs, store, watched=True)]


def test_predict_shapes():
    assert predict([])["label"] == "datos insuficientes"
    hist = [(ts(24 * d), p) for d, p in [(40, 500), (30, 450), (20, 480), (10, 430), (1, 420)]]
    out = predict(hist)
    assert out["prob_drop_7d"] > 0 and out["best_weekday"]


# ---------------------------------------------------------------- errores de precio y categorías
from analysis import too_cheap
from scrapers import SCRAPERS


@pytest.mark.parametrize("name,cat,price,hit", [
    ("Consola PlayStation 5 Slim Digital", "consolas", 49990, True),
    ("Consola Nintendo Switch 2", "consolas", 389990, False),
    ("Consola De Videojuegos Portátil 400 En 1", "consolas", 12990, False),      # retro barata: no es error
    ("Control Joystick PS5 DualSense", "consolas", 29990, False),                 # accesorio
    ("Notebook Lenovo IdeaPad Slim 3 Core i5 16GB", "notebooks", 99990, True),
    ("Lamina protectora anti luz azul notebook", "notebooks", 11290, False),
    ('Smart TV Samsung 65" Crystal UHD', "televisores", 59990, True),
    ('Smart TV TCL 32" HD', "televisores", 59990, False),                         # TV chica: precio normal
    ("Apple iPhone 17 Pro Max 256GB", "celulares", 99990, True),
    ("Funda iPhone 17 Pro Max", "celulares", 9990, False),
])
def test_too_cheap(name, cat, price, hit):
    assert bool(too_cheap(name, cat, price)) is hit


def test_big_discount_error_even_if_new(store):
    p = Product("t:9", "t", "Notebook Lenovo Legion 5", "https://t.cl/9", 129990, 1299990)
    obs = store.observe(p, "g", "src", ts(0), "notebooks")
    assert "price_error" in [a.kind for a in evaluate(obs, store, watched=False, category="notebooks")]


def test_model_code_grouping():
    a = group_key('Smart TV Samsung 55" Crystal UHD 4K UN55DU7000GXZS', "Samsung", "televisores")
    b = group_key("Televisor Samsung UN55DU7000GXZS 55 Pulgadas", None, "televisores")
    assert a == b == "un55du7000gxzs" and comparable(a)
    assert not comparable(group_key("Lamina protectora notebook", None, "notebooks"))


def test_price_sorted_urls_keep_sort_on_every_page():
    fal = SCRAPERS["falabella"]().page_url("https://www.falabella.com/x/cat1/Y?sortBy=derived.price.search%2Casc", 2)
    assert "sortBy=derived.price.search%2Casc" in fal and "page=2" in fal
    sf = SCRAPERS["hites"]().page_url("https://www.hites.com/tv/?srule=price-low-to-high", 2)
    assert "srule=price-low-to-high" in sf and "start=36" in sf
    pcf = SCRAPERS["pcfactory"]().page_url("https://www.pcfactory.cl/categoria/x/notebooks?categorias=425&sort=precio,asc", 1)
    assert "categorias=425" in pcf and "sort=precio%2Casc" in pcf and "page=0" in pcf


# ---------------------------------------------------------------- confusiones del comparador (reportadas en el iPhone)
@pytest.mark.parametrize("a,b,cat", [
    ("Fabric Band Para Galaxy Watch9 Watch8 Mocha", "Samsung Galaxy Watch9 40mm Black", "smartwatches"),
    ("Ideapad Slim 3 Intel Core i7-13620H 16GB 512GB", "Notebook V14 G5 Intel Core i7-13620H 16GB 512GB", "notebooks"),
])
def test_no_false_groups(a, b, cat):
    ka, kb = group_key(a, None, cat), group_key(b, None, cat)
    assert not (comparable(ka) and ka == kb)


def test_strap_is_accessory():
    from analysis import is_accessory
    assert is_accessory("Fabric Band Para Galaxy Watch9", "smartwatches")
    assert is_accessory("Correa Apple Watch 42mm", "smartwatches")
    assert not is_accessory("Samsung Galaxy Watch9 40mm Black", "smartwatches")


# ---------------------------------------------------------------- 📈 precios inflados (Black Friday 2026, auditado)
def _series(store, key, prices_hours):
    obs = None
    for price, h in prices_hours:
        obs = store.observe(P(price, key=key), "g", "src", ts(h))
    return obs


def test_raise_then_lower_back_is_not_a_drop(store):
    # Abc: refrigerador Hisense $169.990 -> sube a $289.990 -> "baja" a $169.990 (-41 %): NO es una bajada
    obs = _series(store, "abc:1", [(169_990, 90), (289_990, 50), (169_990, 0)])
    kinds = [a.kind for a in evaluate(obs, store, watched=False, targets=[])]
    assert not {"drop", "historic_low", "godzilla"} & set(kinds)


def test_real_drop_below_usual_price_still_alerts(store):
    obs = _series(store, "f:1", [(500_000, 400), (500_000, 50), (420_000, 0)])
    assert "historic_low" in [a.kind for a in evaluate(obs, store, watched=False, targets=[])]


def test_godzilla_needs_real_fall_vs_usual_price(store):
    obs = _series(store, "h:1", [(129_990, 300), (169_990, 30), (89_990, 0)])   # lavadora Sindelen
    a = [x for x in evaluate(obs, store, watched=False, targets=[]) if x.kind == "godzilla"]
    assert a and "habitual" in " ".join(a[0].lines)                          # avisa, pero con el ahorro REAL
    obs = _series(store, "h:2", [(179_990, 300), (299_990, 30), (179_990, 0)])  # subió y volvió: sin Godzilla
    assert "godzilla" not in [x.kind for x in evaluate(obs, store, watched=False, targets=[])]


def test_short_history_is_not_historic_low(store):
    obs = _series(store, "w:1", [(279_990, 90), (259_990, 0)])               # Watch8: 4 días de historia
    assert "historic_low" not in [a.kind for a in evaluate(obs, store, watched=False, targets=[])]


# ---------------------------------------------------------------- 📚 segunda opinión con SoloTodo (sin red: checker falso)
import asyncio  # noqa: E402

import inflation  # noqa: E402
from analysis import Alert  # noqa: E402
from pricecheck import History  # noqa: E402


class _FakeChecker:
    def __init__(self, h):
        self.h = h

    async def history(self, store, p):
        return self.h


def _vet(store, alert, h):
    from monitor import Monitor
    mon = Monitor.__new__(Monitor)
    mon.checker = _FakeChecker(h)
    return asyncio.run(mon._vet(store, [alert]))


def _obs(store, key, before, now, name="Galaxy Watch8 40mm"):
    store.observe(Product(key, "hites", name, "u", before), "g", "src", ts(400))
    return store.observe(Product(key, "hites", name, "u", now), "g", "src", ts(0))


def test_vet_discards_false_historic_low(store):
    obs = _obs(store, "h:1", 279_990, 249_990)        # Watch8 Hites: en octubre estuvo a $179.990
    out = _vet(store, Alert("historic_low", obs, "🏆", [], 2), History("Watch8", 239_990, 179_990, "2026-10-07"))
    assert out == [] and inflation.load(store)[0]["kind"] == "falso mínimo"


def test_vet_rewrites_inflated_before(store):
    obs = _obs(store, "f:2", 169_990, 89_990, "Lavadora Sindelen LS-7100BL")
    out = _vet(store, Alert("godzilla", obs, "🦖", [], 0, 169_990), History("Sindelen", 129_990, 125_990, "2026-11-03"))
    assert out and any("inflado" in l and "habitual $129.990" in l for l in out[0].lines)
    assert inflation.load(store)[0]["kind"] == "antes inflado"


def test_vet_drops_small_real_saving(store):
    obs = _obs(store, "h:3", 349_990, 309_990, "Galaxy Tab S10 Lite")   # -11 % "anunciado", -6 % real
    assert _vet(store, Alert("drop", obs, "📉", [], 3), History("Tab", 329_990, 249_990, "2026-10-07")) == []


def test_vet_keeps_alert_without_history(store):
    obs = _obs(store, "h:4", 300_000, 200_000)
    a = Alert("drop", obs, "📉", [], 3)
    assert _vet(store, a, None) == [a]


def test_daily_scan_logs_raise_and_lower(store):
    for price, h in [(169_990, 200), (289_990, 100), (169_990, 10)]:
        store.observe(Product("abc:9", "abc", "Refrigerador Hisense RB215NR", "u", price), "g", "src", ts(h))
    for price, h in [(500_000, 200), (520_000, 100), (450_000, 10)]:                # bajada real: no es inflado
        store.observe(Product("f:9", "falabella", "TV", "u", price), "g", "src", ts(h))
    assert inflation.scan(store) == 1 and inflation.load(store)[0]["key"] == "abc:9"
    assert inflation.scan(store) == 0                                              # 1 vez por semana


def test_black_friday_starts_the_night_before():
    from datetime import datetime as D

    import calendar_cl as c
    assert c.active_event(D(2026, 11, 26, 20, 59)) is None
    assert c.active_event(D(2026, 11, 26, 21, 0)).name == "Black Friday"
    assert c.active_event(D(2026, 11, 28, 3, 0)).name == "Black Friday"
    assert c.active_event(D(2026, 12, 2, 0, 0)) is None


def test_watch_block_is_per_domain():
    import config
    from monitor import Monitor
    ripley = config.Source("x", "https://simple.ripley.cl/p/1", "watch")
    fala = config.Source("y", "https://www.falabella.com/falabella-cl/product/2", "watch")
    assert Monitor._block_key(ripley) != Monitor._block_key(fala)
    assert Monitor._block_key(config.Source("z", "https://x", "paris")) == "block:paris"


def test_bad_photo_host_falls_back_to_text_once(monkeypatch):
    import notifier as N
    n = N.TelegramNotifier(token="t", chat_id="c")
    calls = []
    monkeypatch.setattr(n, "_post", lambda m, d, files=None: calls.append(m) or m == "sendMessage")
    monkeypatch.setattr(N.time, "sleep", lambda s: None)
    N._BAD_IMAGE_HOSTS.clear()
    assert n.send_photo("https://img.tienda.cl/a.jpg", "hola")
    assert n.send_photo("https://img.tienda.cl/b.jpg", "hola")
    assert calls == ["sendPhoto", "sendMessage", "sendMessage"]
