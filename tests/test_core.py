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
