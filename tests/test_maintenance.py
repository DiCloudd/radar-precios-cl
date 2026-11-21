"""🛡️ Protecciones del plan gratis: cupo de builds, limpieza diaria, tamaños y presupuesto por tienda."""
import time

import pytest

import config
import maintenance as M
from models import Product
from storage import PriceStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "m.db")
    s = PriceStore(config.DB_PATH)
    yield s
    s.close()


def test_builds_per_hour_reserve_slot_for_final_publish(store, monkeypatch):
    monkeypatch.setattr(M, "MAX_BUILDS_PER_HOUR", 9)
    for _ in range(8):
        assert M.publish_allowed(store, essential=False)
        M.record_publish(store)
    assert not M.publish_allowed(store, essential=False)    # las periódicas paran en 8…
    assert M.publish_allowed(store, essential=True)         # …la final (guarda la BD) siempre pasa
    store.set_meta("pub:times", [time.time() - 4000] * 20)   # publicaciones de hace >1 h no cuentan
    assert M.publish_allowed(store, essential=False)


def test_daily_cleanup_removes_stale_products_once_a_day(store):
    store.observe(Product("a:old", "a", "Viejo", "https://a.cl/o", 1000), "g", "s", "2026-01-01T10:00:00")
    store.observe(Product("a:new", "a", "Nuevo", "https://a.cl/n", 1000), "g", "s", time.strftime("%Y-%m-%dT%H:%M:%S"))
    store.commit()
    stats = M.daily_cleanup(store)
    assert stats["products"] == 1 and stats.get("vacuum")
    keys = [r[0] for r in store.conn.execute("SELECT key FROM products")]
    assert keys == ["a:new"] and store.conn.execute("SELECT COUNT(*) FROM prices WHERE key='a:old'").fetchone()[0] == 0
    assert M.daily_cleanup(store) is None                    # una vez al día


def test_size_and_budget_warnings():
    assert M.size_warnings({"db_gz": 60, "data_json": 2, "products_json": 3, "db": 1}, 12) == [
        "La base comprimida pesa 60 MB (GitHub rechaza archivos > 100 MB)",
        "12 publicaciones en la última hora (GitHub Pages recomienda ≤ 10)"]
    budget = M.request_budget()
    assert budget["www.falabella.com"] <= M.REQ_PER_MIN_WARN      # la configuración actual está dentro del límite
    assert M.budget_warnings({"x.cl": 30}) and not M.budget_warnings(budget)
