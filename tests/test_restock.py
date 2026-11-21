"""🔥 Gangas, 🚨 vigilancia de reposición y 🔕 umbral de bajadas (A5)."""
from datetime import datetime, timedelta

import pytest

import config
import restock
from analysis import bargain, evaluate
from matcher import group_key
from models import Product
from storage import PriceStore
from scrapers.telcos import EntelScraper


def ts(hours_ago: float) -> str:
    return (datetime.now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


@pytest.fixture
def store(tmp_path):
    s = PriceStore(tmp_path / "t.db")
    yield s
    s.close()


SRC = config.Source("Entel · Celulares", "https://x", "entel", category="celulares")


def P(name, price, key, in_stock=True, list_price=None, site="entel"):
    return Product(key, site, name, f"https://x/{key}", price, list_price=list_price, brand="Xiaomi",
                   in_stock=in_stock, found_at="https://x/listado")


def seed_new(store):
    for i, price in enumerate([799_990, 819_990]):
        p = P("Xiaomi 14 Ultra 5G 512GB", price, f"n:{i}")
        store.observe(p, group_key(p.name, p.brand), "src", ts(50))


def test_seminuevo_key_matches_new_model():
    assert group_key("14 Ultra 5G 512GB Seminuevo", "Xiaomi") == "xiaomi 14 ultra 512gb reacond"


def test_bargain_refurb_vs_new(store):
    seed_new(store)
    cheap = P("Xiaomi 14 Ultra 5G 512GB Seminuevo", 299_990, "e:1", list_price=1_393_990)
    assert bargain(store, cheap, group_key(cheap.name, "Xiaomi"), "celulares")
    normal = P("Xiaomi 14 Ultra 5G 512GB Seminuevo", 499_990, "e:2")      # 61 % del nuevo: seminuevo normal
    assert not bargain(store, normal, group_key(normal.name, "Xiaomi"), "celulares")


def test_bargain_ignores_accessories_and_cheap_models(store):
    seed_new(store)
    case = P("Carcasa Xiaomi 14 Ultra 512GB", 9_990, "e:3")
    assert not bargain(store, case, group_key(case.name, "Xiaomi"), "celulares")
    for i in range(2):
        p = P("Xiaomi Redmi A5 128GB", 99_990, f"r:{i}")
        store.observe(p, group_key(p.name, p.brand), "src", ts(50))
    tiny = P("Xiaomi Redmi A5 128GB Seminuevo", 29_990, "r:9")
    assert not bargain(store, tiny, group_key(tiny.name, "Xiaomi"), "celulares")   # ref < $150k


def _ingest(store, p, watch, hours=0):
    gk = group_key(p.name, p.brand)
    obs = store.observe(p, gk, SRC.name, ts(hours))
    return restock.check(store, obs, gk, "celulares", SRC, watch)


def test_out_of_stock_bargain_is_watched_then_restock_alert(store):
    seed_new(store)
    watch = {}
    a = _ingest(store, P("Xiaomi 14 Ultra 5G 512GB Seminuevo", 299_990, "e:1", in_stock=False), watch, 1)
    assert [x.kind for x in a] == ["restock_watch"] and "e:1" in watch
    assert _ingest(store, P("Xiaomi 14 Ultra 5G 512GB Seminuevo", 299_990, "e:1", in_stock=False), watch) == []
    a = _ingest(store, P("Xiaomi 14 Ultra 5G 512GB Seminuevo", 299_990, "e:1", in_stock=True), watch)
    assert [x.kind for x in a] == ["restock"] and a[0].priority == 0 and "e:1" not in watch


def test_in_stock_bargain_alerts_immediately(store):
    seed_new(store)
    a = _ingest(store, P("Xiaomi 14 Ultra 5G 512GB Seminuevo", 299_990, "e:5"), {})
    assert [x.kind for x in a] == ["ganga"]


def test_watch_dropped_when_price_rises(store):
    seed_new(store)
    watch = {}
    _ingest(store, P("Xiaomi 14 Ultra 5G 512GB Seminuevo", 299_990, "e:1", in_stock=False), watch, 1)
    assert _ingest(store, P("Xiaomi 14 Ultra 5G 512GB Seminuevo", 699_990, "e:1", in_stock=False), watch) == []
    assert "e:1" not in watch


def test_quiet_first_sweep_still_watches(store):
    seed_new(store)
    watch = {}
    p = P("Xiaomi 14 Ultra 5G 512GB Seminuevo", 299_990, "e:7", in_stock=False)
    gk = group_key(p.name, p.brand)
    obs = store.observe(p, gk, SRC.name, ts(0))
    assert restock.check(store, obs, gk, "celulares", SRC, watch, quiet=True) == [] and "e:7" in watch


def test_seed_finds_existing_out_of_stock_bargains(store):
    seed_new(store)
    p = P("Xiaomi 14 Ultra 5G 512GB Seminuevo", 299_990, "e:8", in_stock=False)
    store.observe(p, group_key(p.name, p.brand), SRC.name, ts(5))
    added = restock.seed(store)
    assert [e["price"] for e in added] == [299_990] and "e:8" in restock.load(store)
    assert "Vigilando 1" in restock.seed_message(added)


def test_prune_caps_and_expires():
    old = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
    now = datetime.now().isoformat(timespec="seconds")
    w = {"a": {"since": old, "price": 1, "ref": 10}, "b": {"since": now, "price": 1, "ref": 10}}
    assert list(restock.prune(w)) == ["b"]


# ---------------------------------------------------------------- 🔕 A5: menos ruido en Telegram
def _drop(store, before, after, watched=False, key="d:1"):
    p0 = Product(key, "falabella", "Refrigerador No Frost 300L", "https://x", before)
    store.observe(p0, "g", "src", ts(10))
    obs = store.observe(Product(key, "falabella", "Refrigerador No Frost 300L", "https://x", after), "g", "src", ts(0))
    return [a.kind for a in evaluate(obs, store, watched=watched, targets=[])]


def test_small_drops_are_silent(store):
    assert "drop" not in _drop(store, 500_000, 485_000)                  # -3 %: ruido de anoche
    assert "drop" not in _drop(store, 40_000, 20_000, key="d:2")         # producto < $50.000
    assert "drop" in _drop(store, 500_000, 440_000, key="d:3")           # -12 % y $60.000 menos


def test_followed_products_keep_sensitive_threshold(store):
    assert "drop" in _drop(store, 500_000, 485_000, watched=True)


def test_entel_name_gets_brand():
    rec = ('{"@class": "com.endeca.infront.cartridge.model.Record", "attributes": {"productId": ["prod1"], '
           '"priceFormatted": ["$299.990"], "referencePrice": ["1393990"], "brand": ["Xiaomi"], '
           '"displayName": ["14 Ultra 5G 512GB Seminuevo"], "inventoryStatus": ["OUT_OF_STOCK"], "seoUrl": ["/p/1"]}}')
    p = EntelScraper().parse(rec, "https://miportal.entel.cl/x").products[0]
    assert p.name == "Xiaomi 14 Ultra 5G 512GB Seminuevo" and p.in_stock is False


# ---------------------------------------------------------------- 🖥️📷🖨️ categorías nuevas (B1, B2, B10, B12)
from analysis import is_accessory, too_cheap  # noqa: E402


def test_new_category_floors():
    assert too_cheap('Monitor Samsung Odyssey G5 27" QHD 180Hz', "monitores", 30_000)
    assert not too_cheap("Monitor Pantalla 7 Pulgadas Lcd Auto Retroceso", "monitores", 9_000)   # genérico barato real
    assert too_cheap('Monitor LG UltraGear OLED 27" 240Hz', "monitores", 90_000)                 # gama alta: piso mayor
    assert too_cheap("Camara Eos R50 18-45 Is Stm", "camaras", 150_000)
    assert not too_cheap("Dron 4k Ultra Hd Doble Cámara Plegable", "camaras", 20_000)           # dron de juguete
    assert too_cheap("Impresora 3D Bambu Lab A1 mini", "impresion3d", 40_000)
    assert not too_cheap("Curadora Creality para impresión 3D en resina", "impresion3d", 40_000)


def test_camera_kit_is_not_accessory():
    assert not is_accessory("Kit Cámara Canon EOS R50 18-45", "camaras")
    assert is_accessory("Lente Canon RF 50mm f/1.8", "camaras")


def test_maconline_watch_variants_collapse():
    from scrapers.platforms import MacOnlineScraper
    node = ('<script type="application/ld+json">{"@type": "Product", "name": "Apple Watch Series 11 con GPS - Caja de '
            'aluminio negro de 42 mm - Correa deportiva negra - Talla S/M", "offers": {"@type": "Offer", "price": 429990, '
            '"availability": "InStock"}}</script>')
    p = MacOnlineScraper().parse(node, "https://www.maconline.com/products/apple-watch-series-11").products[0]
    assert p.name == "Apple Watch Series 11 con GPS aluminio 42 mm" and not is_accessory(p.name, "smartwatches")


def test_dashboard_marks_gangas():
    from dashboard import _mark_gangas
    mk = lambda key, name, price, group: {"key": key, "name": name, "price": price, "group": group, "cat": "celulares",
                                          "missing": None, "badges": []}
    items = [mk("a", "Xiaomi 14 Ultra 512GB", 799_990, "xiaomi 14 ultra 512gb"),
             mk("b", "Xiaomi 14 Ultra 512GB", 819_990, "xiaomi 14 ultra 512gb"),
             mk("c", "Xiaomi 14 Ultra 512GB Seminuevo", 299_990, "xiaomi 14 ultra 512gb reacond"),
             mk("d", "Xiaomi 14 Ultra 512GB Seminuevo", 549_990, "xiaomi 14 ultra 512gb reacond")]
    _mark_gangas(items)
    assert [bool(i["ganga"]) for i in items] == [False, False, True, False]


# ---------------------------------------------------------------- 💻 Lenovo (API JSON de su tienda)
def test_lenovo_parse_and_page_url():
    import json as _json
    from scrapers.lenovo import LenovoScraper
    api = {"data": {"pageCount": 6, "data": [{"products": [
        {"productCode": "83JE00UWCL", "productName": "NB LOQ 15IRX10 I5 16G 512G 11S",
         "summary": 'Lenovo LOQ Gen 10 (15" Intel) GeForce RTX 5060', "finalPrice": "1399993.00",
         "webPrice": "2235345.00", "inventoryStatus": 1, "purchaseFlag": True, "marketingStatus": "Available",
         "url": "/p/notebooks/loq-laptops/lenovo-loq-15irx10/83je00uwcl",
         "media": {"thumbnail": {"imageAddress": "//p1-ofp.static.pub/x.png"}}},
        {"productCode": "82XQ005VCL", "productName": "NB IP Slim 3 15AMN8 R3 8G 512G 11S",
         "summary": 'IdeaPad Slim 3 Gen 8 (15" AMD)', "finalPrice": "499991.00", "webPrice": "499991.00",
         "inventoryStatus": 0, "purchaseFlag": False, "url": "/p/x/82xq005vcl", "media": {}}]}]}}
    res = LenovoScraper().parse(_json.dumps(api), "u")
    a, b = res.products
    assert res.total_pages == 6
    assert a.key == "lenovo:83JE00UWCL" and a.price == 1_399_993 and a.list_price == 2_235_345 and a.in_stock
    assert a.name.startswith("Lenovo LOQ Gen 10") and "83JE00UWCL" in a.name and a.image.startswith("https://")
    assert b.name.startswith("Lenovo IdeaPad") and b.list_price is None and b.in_stock is False
    url = LenovoScraper().page_url("https://www.lenovo.com/cl/es/notebooks/#7ca29953", 2)
    assert "pageFilterId=7ca29953" in url and "%2522page%2522%253A%25222%2522" in url
    assert url.endswith("#https://www.lenovo.com/cl/es/notebooks/")


# ---------------------------------------------------------------- 🔥 falsos positivos de gangas (auditados con datos reales)
def _mk(key, name, price, group, cat="celulares", badges=(), lst=None):
    return {"key": key, "name": name, "price": price, "group": group, "cat": cat, "missing": None,
            "badges": list(badges), "list": lst}


def test_marketplace_resellers_do_not_inflate_reference():
    from dashboard import _mark_gangas
    items = [_mk("pc", 'Monitor Caixun 24", FHD, IPS, 100Hz, 5ms, C24X3F', 79_990, "c24x3f", "monitores", lst=149_990),
             _mk("f1", "Monitor De Oficina 24 Pulgadas C24X3F", 219_990, "c24x3f", "monitores", ["Vendedor: Delirius Spa"]),
             _mk("f2", "Monitor Oficina 24 FHD C24X3F", 289_990, "c24x3f", "monitores", ["Vendedor: 2vision"])]
    _mark_gangas(items)
    assert not items[0]["ganga"]


def test_own_list_price_below_reference_is_not_ganga():
    from analysis import bargain_vs
    assert bargain_vs(79_990, [219_990, 289_990], False) and not bargain_vs(79_990, [219_990, 289_990], False, 149_990)


def test_bundles_are_not_reference():
    from analysis import ganga_ref_ok
    assert not ganga_ref_ok("Xiaomi Redmi Note 14 Pro 5G 256GB + Sound Pocket", None)
    assert ganga_ref_ok("Redmi Note 17 Pro+ 512GB", None)              # "Pro+" es el modelo, no un pack


def test_refurb_uses_cheapest_new_and_refurb_peers():
    from dashboard import _mark_gangas
    items = [_mk("e", "Samsung Galaxy S23 5G 128GB", 1_199_990, "samsung galaxy s23 128gb"),          # listado viejo
             _mk("m", "Galaxy S23 128GB Negro", 439_990, "samsung galaxy s23 128gb", badges=["Vendedor: Tamia"]),
             _mk("h", "Samsung Galaxy S23 128gb Black - Reacondicionado", 359_990, "samsung galaxy s23 128gb reacond")]
    _mark_gangas(items)
    assert not items[2]["ganga"]                                        # 82 % del nuevo más barato: precio normal
    items = [_mk("n", "Xiaomi 14T Pro 512GB", 569_990, "xiaomi 14t pro 512gb"),
             _mk("r", "Xiaomi 14T Pro 512GB Seminuevo", 199_990, "xiaomi 14t pro 512gb reacond"),
             _mk("r2", "14T Pro 512GB Reacondicionado", 549_990, "xiaomi 14t pro 512gb reacond", badges=["Vendedor: X"])]
    _mark_gangas(items)
    assert items[1]["ganga"]                                            # el caso real de Entel sigue detectándose


def test_app_gangas_are_announced_once(store):
    seed_new(store)
    p = P("Xiaomi 14 Ultra 5G 512GB Seminuevo", 299_990, "e:9", in_stock=True)
    store.observe(p, group_key(p.name, p.brand), SRC.name, ts(1))
    store.set_meta("gangas:app", {"e:9": {"why": "63% más barato", "ref": 799_990}})
    a = restock.pending_from_app(store)
    assert [x.kind for x in a] == ["ganga"]
    store.add_alert("e:9", "ganga", 299_990, "x", ts(0))
    assert restock.pending_from_app(store) == []                        # ya avisada: no se repite


def test_app_gangas_sold_out_go_to_watch(store):
    seed_new(store)
    p = P("Xiaomi 14 Ultra 5G 512GB Seminuevo", 299_990, "e:10", in_stock=False)
    store.observe(p, group_key(p.name, p.brand), SRC.name, ts(1))
    store.set_meta("gangas:app", {"e:10": {"why": "63% más barato", "ref": 799_990}})
    assert [x.kind for x in restock.pending_from_app(store)] == ["restock_watch"] and "e:10" in restock.load(store)
