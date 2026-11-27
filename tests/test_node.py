"""🇨🇱 Nodo de Chile: el PC exporta lo que leyó y la nube lo integra con las mismas reglas."""
import asyncio
import gzip
import json

import config
import node
from models import Product
from storage import PriceStore, now_iso


def test_export_import_roundtrip(tmp_path, monkeypatch):
    src = next(s for s in config.SOURCES if s.site == "paris")
    local = tmp_path / "local.db"
    with PriceStore(local) as s:
        s.observe(Product("paris:1", "paris", "Samsung Galaxy A57 5G 128GB", "https://paris/1", 399_990), "g", src.name)
        s.observe(Product("falabella:9", "falabella", "x", "u", 1), "g", "otra")
        s.commit()
    out = tmp_path / "node.json.gz"
    assert node.export(local, out, {"paris"}) == 1                       # solo las tiendas del nodo
    data = json.loads(gzip.decompress(out.read_bytes()))

    cloud = tmp_path / "cloud.db"
    monkeypatch.setattr(config, "DB_PATH", cloud)
    from monitor import Monitor
    mon = Monitor(dry_run=True)
    assert asyncio.run(node.import_into(mon, data)) == 1 and mon.node_sites == {"paris"}
    with PriceStore(cloud) as s:
        assert s.conn.execute("SELECT price FROM products WHERE key='paris:1'").fetchone()[0] == 399_990
        assert s.recent_runs(src.name, 1)[0]["mode"] == "node"
    fresh_mon = Monitor(dry_run=True)                                    # ventana nueva, misma foto del nodo:
    assert asyncio.run(node.import_into(fresh_mon, data)) == 0            # no se integra 2 veces…
    assert fresh_mon.node_sites == {"paris"}                              # …pero la nube igual no pide Paris (403)
    asyncio.run(fresh_mon.aclose())
    old = dict(data, ts="2020-01-01T00:00:00")
    assert asyncio.run(node.import_into(mon, old)) == 0 and mon.node_sites == set()   # nodo apagado: la nube reintenta
    asyncio.run(mon.aclose())


def test_node_mode_filters_sources(monkeypatch):
    import importlib
    monkeypatch.setenv("NODE_MODE", "1")
    monkeypatch.setenv("NODE_SITES", "paris")
    cfg = importlib.reload(config)
    try:
        assert cfg.SOURCES and {s.site for s in cfg.SOURCES} == {"paris"}
    finally:
        monkeypatch.delenv("NODE_MODE")
        importlib.reload(config)
    _ = now_iso
