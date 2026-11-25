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
    res = parse(ent(5, 260, "Samsung Galaxy S25 (256 GB / Navy)", 599990),
                ent(6, 11, "Apple iPhone 13 (128 GB / Black)", 399990, condition="https://schema.org/RefurbishedCondition"))
    ml, refurb = res.products
    assert any(b.startswith("Vendedor: ") for b in ml.badges)          # MercadoLibre no sirve de referencia
    assert refurb.name.endswith("(reacondicionado)")


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
