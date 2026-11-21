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
    monkeypatch.setattr(config, "MIN_HISTORY_HOURS", 72)
    store.observe(P(100_000), "g", "src", ts(10))
    obs = store.observe(P(95_000), "g", "src", ts(0))
    kinds = [a.kind for a in evaluate(obs, store, watched=False)]
    assert "historic_low" not in kinds and "drop" in kinds          # solo 10 h de historia
    store.observe(P(100_000, key="t:2"), "g", "src", ts(100))
    obs = store.observe(P(95_000, key="t:2"), "g", "src", ts(0))
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
