"""⭐ Productos prioritarios: aviso inmediato si caen a un precio ridículo o hay un posible error."""
import re
from datetime import datetime, timedelta

import priority
from models import Product
from storage import PriceStore


def ts(h):
    return (datetime.now() - timedelta(hours=h)).isoformat(timespec="seconds")


PRI = [priority.Priority("DJI Osmo Pocket 4P Standard Combo", re.compile("osmo pocket 4p"), re.compile("creator"), 769_990),
       priority.Priority("DJI Mic Mini 2S (2 TX)", re.compile(r"mic mini 2s.*2\s*tx"), None, 199_990),
       priority.Priority("Samsung Galaxy S26 Ultra", re.compile("galaxy s26 ultra"), None, None)]


def _check(tmp_path, name, before, now, cat="camaras", badges=()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    s = PriceStore(tmp_path / "p.db")
    s.observe(Product("f:1", "falabella", name, "u", before, badges=list(badges)), "g", "src", ts(5))
    obs = s.observe(Product("f:1", "falabella", name, "u", now, badges=list(badges)), "g", "src", ts(0))
    out = priority.check(s, obs, cat, PRI)
    s.close()
    return [a.kind for a in out]


def test_ridiculous_price_and_error(tmp_path):
    assert _check(tmp_path, "DJI Osmo Pocket 4P Standard Combo", 769_990, 629_990) == ["priority"]      # -18 %
    assert _check(tmp_path / "b", "DJI Osmo Pocket 4P", 769_990, 399_990) == ["price_error"]           # -48 %


def test_small_drop_and_exclusions(tmp_path):
    assert _check(tmp_path, "DJI Osmo Pocket 4P Standard Combo", 769_990, 739_990) == []              # -4 %: normal
    assert _check(tmp_path / "b", "DJI Osmo Pocket 4P Creator Combo", 999_990, 600_000) == []          # excluido
    assert _check(tmp_path / "c", "Funda para DJI Osmo Pocket 4P", 29_990, 9_990) == []                 # accesorio
    assert _check(tmp_path / "d", "DJI Osmo Pocket 4P", 769_990, 500_000, badges=["Vendedor: X"]) == []  # revendedor
    assert _check(tmp_path / "e", "DJI Osmo Pocket 4P Reacondicionado", 769_990, 400_000) == []          # reacond.


def test_reference_from_catalog(tmp_path):
    s = PriceStore(tmp_path / "c.db")
    for i, price in enumerate([1_299_990, 1_349_990, 1_399_990]):
        s.observe(Product(f"x:{i}", "paris", "Samsung Galaxy S26 Ultra 256GB", "u", price), "g", "src", ts(10))
    s.observe(Product("y:1", "hites", "Samsung Galaxy S26 Ultra 256GB", "u", 1_299_990), "g", "src", ts(5))
    obs = s.observe(Product("y:1", "hites", "Samsung Galaxy S26 Ultra 256GB", "u", 990_000), "g", "src", ts(0))
    priority._refs.clear()
    assert [a.kind for a in priority.check(s, obs, "celulares", PRI)] == ["priority"]                   # -27 % vs catálogo
    s.close()
