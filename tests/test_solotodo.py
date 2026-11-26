"""📚 SoloTodo: extractor de las tiendas que bloquean a la nube (Ripley, Paris, Lider, MercadoLibre, Tottus)."""
import json
from datetime import datetime, timezone

from matcher import group_key
from scrapers.solotodo import SoloTodoScraper

NOW = datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc)
URL = "https://api.solotodo.com/entities/?categories=6&stores=18&stores=11&is_available=1&page_size=200"


def ent(eid, store, name, normal, offer=None, ts="2026-11-25T19:27:34Z", **kw):
    return {"id": eid, "store": store, "name": "listado", "external_url": f"https://x/{eid}", "condition":
            kw.get("condition", "https://schema.org/NewCondition"), "product": {"id": 1, "name": name},
            "cell_plan": kw.get("cell_plan"), "seller": kw.get("seller"), "picture_urls": ["https://img/1.jpg"],
            "active_registry": {"timestamp": ts, "is_available": kw.get("available", True),
                                "normal_price": f"{normal}.00", "offer_price": f"{offer or normal}.00"}}


def parse(*ents, count=None, url=URL):
    return SoloTodoScraper().parse(json.dumps({"count": count or len(ents), "results": list(ents)}), url, NOW)


def test_parse_ripley_iphone():
    res = parse(ent(1377172, 18, "Apple iPhone 15 (128 GB / Black)", 729990, 699990), count=540)
    p = res.products[0]
    assert (p.key, p.site, p.price, p.card_price) == ("ripley:st1377172", "ripley", 729990, 699990)
    assert p.name == "Apple iPhone 15 128 GB Black" and group_key(p.name, p.brand) == "apple iphone 15 128gb"
    assert "vía SoloTodo" in p.badges and p.age_h < 1 and res.total_pages == 3


def test_skips_plans_unknown_stores_unavailable_and_stale():
    res = parse(ent(1, 18, "Apple iPhone 15 (128 GB / Black)", 9990, cell_plan={"id": 3}),     # con plan
                ent(2, 999, "Apple iPhone 15 (128 GB / Black)", 729990),                      # tienda que no usamos
                ent(3, 11, "Apple iPhone 15 (128 GB / Black)", 729990, available=False),
                ent(4, 43, "Apple iPhone 15 (128 GB / Black)", 729990, ts="2026-11-20T00:00:00Z"))  # > 72 h
    assert res.products == []


def test_marketplace_and_refurb_flags():
    res = parse(ent(4, 260, "Samsung Galaxy S25 (256 GB / Navy)", 499990),       # MercadoLibre general: revendedores
                ent(5, 225, "Samsung Galaxy S25 (256 GB / Navy)", 599990),       # tienda OFICIAL Samsung en ML
                ent(6, 11, "Apple iPhone 13 (128 GB / Black)", 399990, condition="https://schema.org/RefurbishedCondition"),
                ent(7, 18, "Apple iPhone 15 (128 GB / Black)", 699990, seller="Tienda X"))
    ml, refurb, mkt = res.products
    assert ml.site == "mercadolibre" and ml.price == 599990                     # solo la oficial entra
    assert not any(b.startswith("Vendedor: ") for b in ml.badges) and any("oficial" in b for b in ml.badges)
    assert refurb.name.endswith("(reacondicionado)")
    assert any(b.startswith("Vendedor: ") for b in mkt.badges)                 # vendedor externo: no es referencia


def test_category_sanity_filters_accessories_and_miscategorized():
    url = URL.replace("categories=6", "categories=11")
    res = parse(ent(7, 18, 'SOPORTE LED ECOTEK PGW055F 25"-55"', 10990),
                ent(8, 11, "Petrizzio Set Body Mist Carnival 200 ml", 10790),
                ent(9, 11, "Samsung QN55Q60D", 449990), url=url)
    assert [p.name for p in res.products] == ["Samsung QN55Q60D"]


def test_page_urls():
    s = SoloTodoScraper()
    assert s.page_url(URL, 1) == URL and s.page_url(URL, 3) == URL + "&page=3"


def test_verifier_marks_solotodo_unconfirmed():
    import asyncio
    from models import Product
    from verifier import Verifier
    p = Product("ripley:st1", "ripley", "x", "u", 1000, stock_text="vía SoloTodo · hace 2 h")
    v = asyncio.run(Verifier(None).verify(p, delay=0))
    assert v.status == "unconfirmed" and "SoloTodo" in v.note


def test_backfill_imports_only_older_changes(tmp_path):
    from backfill import _candidates, import_history
    from models import Product
    from storage import PriceStore
    s = PriceStore(tmp_path / "b.db")
    s.observe(Product("pcfactory:50677", "pcfactory", "Redmi 13C", "u", 99_990), "g", "src", "2026-11-20T10:00:00")
    hist = [{"timestamp": f"2026-10-{d:02d}T12:00:00Z", "is_available": True, "normal_price": p, "offer_price": p}
            for d, p in [(1, "119990.00"), (2, "119990.00"), (10, "109990.00"), (11, "109990.00")]]
    hist.append({"timestamp": "2026-11-21T12:00:00Z", "is_available": True, "normal_price": "1.00", "offer_price": "1.00"})
    assert import_history(s, "pcfactory:50677", hist) == 2                   # 2 cambios, nada posterior a nuestro 1er dato
    assert [r[0] for r in s.conn.execute("SELECT price FROM prices ORDER BY ts")] == [119_990, 109_990, 99_990]
    assert s.conn.execute("SELECT first_seen FROM products").fetchone()[0].startswith("2026-10-01")
    assert _candidates("pcfactory", {"store": 12, "id": 1, "sku": "50677",
                                     "external_url": "https://www.pcfactory.cl/producto/50677-xiaomi"})[0] == "pcfactory:50677"
    assert _candidates("", {"store": 18, "id": 1377172, "sku": "x", "external_url": ""}) == ["ripley:st1377172"]
    s.close()
