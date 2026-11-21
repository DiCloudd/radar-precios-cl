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


# ---------------------------------------------------------------- 🏆 salón de la fama honesto y sin avisos repetidos
def _err_store(tmp_path):
    from models import Product
    from storage import PriceStore
    s = PriceStore(tmp_path / "e.db")
    band = Product("h:1", "hites", "Sport Band Para Galaxy Watch9 Watch8 Blue", "u", 34_990, list_price=49_990)
    s.observe(band, "accesorio: band", "src", category="smartwatches")
    s.add_error(band, "price_error", 49_990, "2026-11-21T01:59:09", "confirmed", "x", "smartwatches")
    real = Product("f:1", "falabella", "Apple iPhone 17 Pro Max 256GB", "u", 199_990, list_price=1_599_990)
    s.observe(real, "apple iphone 17 pro max 256gb", "src", category="celulares")
    s.add_error(real, "price_error", 1_599_990, "2026-11-21T02:00:00", "confirmed", "x", "celulares")
    return s


def test_recheck_discards_old_false_positives(tmp_path, monkeypatch):
    import maintenance as M
    monkeypatch.setattr("storage.ago", lambda **kw: "2000-01-01T00:00:00")
    s = _err_store(tmp_path)
    assert M.recheck_errors(s, days=3650) == 1
    assert [e["name"][:9] for e in s.errors()] == ["Apple iPh"]          # la Sport Band ya no aparece
    s.close()


def test_stale_hunts_are_closed(tmp_path):
    s = _err_store(tmp_path)
    assert s.close_stale_errors() == 2 and all(e["outcome"].startswith("seguía") for e in s.errors())
    s.close()


def test_same_godzilla_not_repeated_within_a_week(tmp_path):
    from storage import PriceStore, now_iso
    s = PriceStore(tmp_path / "g.db")
    s.add_alert("h:9", "godzilla", 169_990, "x", now_iso())
    assert s.alerted_at_or_below("h:9", "godzilla", 169_990, 7)          # mismo precio: no se repite
    assert not s.alerted_at_or_below("h:9", "godzilla", 149_990, 7)      # bajó MÁS: sí se avisa
    s.close()
