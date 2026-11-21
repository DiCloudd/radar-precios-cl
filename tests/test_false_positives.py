"""🛡️ Falsos positivos REALES encontrados (captura del usuario + auditoría con 3.723 productos) y errores que
SÍ deben detectarse. Cada caso viene de datos reales de nov-2026."""
import pytest

from analysis import is_accessory, price_error_reasons
from models import Product


def P(name, price, list_price=None, card=None, badges=None, store_category=None):
    return Product("x:1", "x", name, "https://x.cl/1", price, list_price, card, badges=badges or [],
                   store_category=store_category)


# (nombre, categoría, precio, normal, tarjeta, categoría de la tienda) -> NO debe alertar como producto nuevo
NOT_ERRORS = [
    ("Sport Band Para Galaxy Watch9 Watch8 Blue", "smartwatches", 34990, 49990, None, None),      # captura del usuario
    ("Fabric Band Para Galaxy Watch9 Watch8 Mocha", "smartwatches", 34990, 49990, None, None),
    ("Galaxy Watch7 Fabric Band Blue White", "smartwatches", 29990, 69990, None, None),
    ("Anillo Titanio Moda Acero Inoxidable", "smartwatches", 9990, None, None, "Smartwatch > Pulseras > Smartwatch"),
    ("Silent Hill: Homecoming - Playstation 3", "consolas", 59990, None, None, None),            # juego, no consola
    ("Consola De Videojuegos Portátil 400 En 1", "consolas", 12990, None, None, "Controles > CONTROLES"),
    ("14T Pro 5G 512GB Seminuevo", "celulares", 199990, 829990, None, None),                    # Entel: normal = nuevo
    ("Nubia Neo 2 5G 256GB", "celulares", 99990, 339990, None, None),                           # "normal" inflado
    ("Xiaomi 17 Ultra Photography Kit Pro (desde)", "celulares", 249990, None, None, None),
    ("Lavadora Carga Superior 4,5 kg Blanco LC-4550BL", "electro", 89990, 149990, None, None),
    ("Mini Lavadora Portatil Plegable Hogar", "electro", 91990, 105990, None, None),
    ("Refrigerador Viaje Insulina Hormona Inalámbrico Portátil", "electro", 89990, 129990, None, None),
    ("Lavadora Secadora 10 6Kg WD1I1041BW", "electro", 290490, 449990, None, None),
    ("Smart TV QLED FHD 43 TCL Roku TV S5LR", "televisores", 199990, 349990, None, None),
    ("Smart TV QLED 50 TCL FHD Roku TV 50S5LR", "televisores", 249990, 449990, 199990, None),
    ("Lenovo Tab Play MTK G85 4GB 64GB 10.1 KIDS Bumper Case", "tablets", 159990, 399990, 109990, None),
    ("Soundbar MLAB Flaming Smoke 9341", "televisores", 29990, None, None, "Sales Catalog > Tecnología > Audio > Soundbar"),
    ("PlayStation Plus Essential 12 meses", "consolas", 39990, None, None, None),
    ("Funda iPhone 17 Pro Max MagSafe", "celulares", 19990, 39990, None, None),
    ("Smart TV Samsung 55 compatible con Alexa", "televisores", 329990, 399990, None, None),
]


@pytest.mark.parametrize("name,cat,price,lp,card,sc", NOT_ERRORS)
def test_real_false_positives_stay_silent(name, cat, price, lp, card, sc):
    assert price_error_reasons(P(name, price, lp, card, store_category=sc), cat, is_new=True) == []


# errores que SÍ deben detectarse aunque el producto sea nuevo
REAL_ERRORS = [
    ("Apple iPhone 18 Pro Max 256GB", "celulares", 169990, None),
    ("Celular Galaxy S26 Ultra 512GB", "celulares", 189990, None),
    ("MacBook Pro 14 XDR (Chip M5 Pro 24GB RAM) 1TB SSD", "notebooks", 354990, None),
    ("Notebook Gamer Legion 5 i9-14900HX 32GB RAM 1TB SSD RTX 5070", "notebooks", 253990, None),
    ("Consola PlayStation 5 Slim Digital", "consolas", 49990, 549990),
    ('Smart TV Samsung 65" Crystal UHD', "televisores", 59990, None),
    ('Smart TV 32" HD Roku', "televisores", 15990, None),
    ("Refrigerador No Frost Samsung 400L", "electro", 49990, None),
    ("Refrigerador Frío Directo Mademsa 173 L", "electro", 18990, None),
    ("IPhone 15 Pro Max 256GB Reacondicionado", "celulares", 81990, None),        # reacondicionado también
    ("Apple Watch Series 11 GPS 42mm", "smartwatches", 29990, None),
    ("Audífonos Sony WH-1000XM6", "audifonos", 19990, None),
    ("Notebook HP Victus 15 Ryzen 5 16GB 512GB SSD teclado retroiluminado", "notebooks", 89990, None),
]


@pytest.mark.parametrize("name,cat,price,lp", REAL_ERRORS)
def test_real_errors_still_detected(name, cat, price, lp):
    assert price_error_reasons(P(name, price, lp), cat, is_new=True)


def test_extreme_discount_on_new_listing_but_not_marketplace():
    assert price_error_reasons(P("Notebook Asus Vivobook 15", 89990, 899990), "notebooks", is_new=True)
    mk = P("Notebook Asus Vivobook 15", 89990, 899990, badges=["Vendedor: Electronic Deals"])
    assert price_error_reasons(mk, "notebooks", is_new=True)        # igual cae por "demasiado barato"
    mk2 = P("Smartphone Senior 4G", 19990, 499990, badges=["Vendedor: Phone Box"])
    assert price_error_reasons(mk2, "celulares", is_new=True) == []  # marketplace con "normal" inflado: silencio


def test_store_category_rules_are_category_aware():
    # Hites pone TODOS los audífonos bajo "ACC.ELECT.MENOR" y las consolas bajo "VideoJuegos": no son accesorios
    assert not is_accessory("Audífonos Sony WH-1000XM6", "audifonos", "Audífonos > Marcas > ACC.ELECT.MENOR")
    assert not is_accessory("Consola PlayStation 5", "consolas", "Consolas > VideoJuegos > Marcas > SONY")
    assert is_accessory("Control DualSense", "consolas", "Consolas > VideoJuegos > Marcas > CONTROLES")


@pytest.mark.parametrize("name,price", [
    ("AUDIFONOS TWS AIR BUDS PRO 3.0 09309 BLANCO", 12990),          # catálogo completo (9.805 productos)
    ("Audifonos Bluetooth Ergonomic Aud·buds Pro 3.0", 14990),
    ("Audifonos Comfort Earbuds Pro Tws 631w Bluetooth Negro", 19980),
    ("Audífonos Airbuds Pro", 36990),
    ("Audífonos tipo AirPods Pro réplica", 9990),
])
def test_generic_earbuds_are_not_premium(name, price):
    assert price_error_reasons(P(name, price), "audifonos", is_new=True) == []


@pytest.mark.parametrize("name", ["Apple AirPods Pro 3", "Samsung Galaxy Buds3 Pro", "Audífonos Sony WH-1000XM6",
                                  "Audífonos Bose QuietComfort Ultra"])
def test_branded_premium_earbuds_still_detected(name):
    assert price_error_reasons(P(name, 9990), "audifonos", is_new=True)
