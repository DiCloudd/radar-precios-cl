"""Reglas de alerta: bajadas, mínimos reales, ofertas falsas, precio objetivo, stock y 🦖 Godzilla."""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import config
from matcher import comparable
from storage import Observation, PriceStore, ago


@dataclass
class Alert:
    kind: str                      # godzilla | price_error | ganga | restock | restock_watch | target | historic_low | drop | rise | stock_* | missing | back
    obs: Observation | None
    title: str
    lines: list[str] = field(default_factory=list)
    priority: int = 5              # menor = más importante
    ref: int | None = None         # precio de referencia (para saber cuándo corrigieron un error)
    st_followup: bool = False      # 📚 la opinión de SoloTodo va en un 2º mensaje (no retrasa el aviso)

    @property
    def key(self) -> str | None:
        return self.obs.product.key if self.obs else None

    @property
    def price(self) -> int | None:
        return self.obs.product.price if self.obs else None


def _hours_since(iso: str) -> float:
    return (datetime.now() - datetime.fromisoformat(iso)).total_seconds() / 3600


def fake_discount(obs: Observation, store: PriceStore) -> str | None:
    """🤡 Detecta descuentos inflados. Devuelve el motivo o None."""
    p = obs.product
    if not p.list_price or not p.discount_pct or p.discount_pct < 10:
        return None
    hist = store.history(p.key, since=ago(days=30))
    lists = [r["list_price"] for r in hist if r["list_price"]]
    if lists and min(lists) * (1 + config.FAKE_LIST_RAISE_PCT / 100) <= p.list_price:
        raise_pct = (p.list_price / min(lists) - 1) * 100
        return f"subieron el precio 'normal' {raise_pct:.0f}% en 30 días para inflar el descuento"
    if _hours_since(obs.first_seen) >= 24 * 30 and len(hist) <= 1 and p.discount_pct >= 20:
        return f"el '-{p.discount_pct:.0f}%' lleva 30+ días sin cambiar: ese es el precio real"
    return None


def is_real_historic_low(obs: Observation) -> bool:
    return (obs.historic_min is not None and obs.product.price < obs.historic_min
            and _hours_since(obs.first_seen) >= config.MIN_HISTORY_HOURS)


# ============================================================================================
#  🛡️ ANTI FALSOS POSITIVOS. Un "error de precio" solo se avisa si el producto ES lo que parece.
#  Capa 1: tipo al inicio del título      ("Funda…", "Sport Band…", "Control…", "Lámina…")
#  Capa 2: señales en cualquier parte      ("…para iPhone 17", "compatible", "gift card", "12 meses", "repuesto")
#  Capa 3: compatibilidad multi-modelo     ("Watch9 Watch8", "iPhone 15/16", "S24 S25")
#  Capa 4: categoría que informa la tienda ("Correas", "Accesorios", "Videojuegos")
#  Capa 5: precio normal de la tienda      (si su "normal" ya es barato, no es un producto caro mal etiquetado)
#  Capa 6: vendedores del marketplace       (sus "precios normales" inflados no cuentan como descuento real)
# ============================================================================================
ACCESSORY = re.compile(
    r"l[aá]mina|funda|carcasa|\bcases?\b|\bcover\b|estuche|protector|\bmica\b|hidrogel|vidrio|templad|\bfilm\b|"
    r"cargador|cable|adaptador|convertidor|\bhub\b|\bdock|soporte|\bbase\b|\bstand\b|tr[ií]pode|selfie|holder|"
    r"correa|\bbands?\b|malla|pulsera|strap|mochila|bolso|malet[ií]n|sleeve|teclado|mouse|alfombrilla|mousepad|"
    r"aud[ií]fonos?|parlante|control|joystick|\bmando|volante|joy-?con|dualsense|cooler|ventilador|disipador|"
    r"skin|sticker|vinilo|\bkit\b|limpia|l[aá]piz|stylus|pencil|puntero|repuesto|bater[ií]a|power ?bank|"
    r"memoria|tarjeta|pendrive|disco|\bssd\b|antena|decodificador|\brack\b|mueble|repisa|cubre|organizador|"
    r"filtro|manguera|bandeja|frigobar|minibar|accesorio|\bjuegos?\b|videojuego|pel[ií]cula|amiibo|figura|"
    r"peluche|\bcartas?\b|gift|retro|tetris|lente|repelente|extensi[oó]n|regleta|enchufe|transformador|"
    r"pantalla de repuesto|\btapa\b|servicio|instalaci[oó]n", re.I)
NON_PRODUCT = re.compile(
    r"\bpara (el |la |tu |los |las )?(iphone|ipad|galaxy|samsung|xiaomi|redmi|poco|motorola|moto|huawei|honor|oppo|"
    r"vivo|realme|pixel|macbook|apple|airpods|watch|ps[45]|playstation|xbox|nintendo|switch|tv|televisor|smart ?tv|"
    r"notebook|laptop|tablet|refrigerador|lavadora|smartwatch|reloj|celular|consola)|"
    r"compatible (con|para) (el |la )?(iphone|ipad|galaxy|samsung|apple|airpods|ps[45]|xbox|switch|watch|macbook)|"
    r"\bfor (iphone|samsung|galaxy|apple|ipad|airpods|ps5|xbox|switch)|repuesto|gift ?card|tarjeta de regalo|"
    r"c[oó]digo digital|\bdigital code\b|suscripci[oó]n|membres[ií]a|garant[ií]a extendida|seguro de|"
    r"(plus|pass|ultimate|essential|extra|premium|online)\s*\d+\s*meses|\d+\s*meses de (suscrip|membres|"
    r"playstation|xbox|nintendo|game)|solo caja|caja vac[ií]a|dummy|maqueta|de exhibici[oó]n|game ?pass|"
    r"photography kit|kit (pro|fotogr[aá]fico|de c[aá]mara)\b|"
    r"\btipo (airpods|apple|iphone|samsung|galaxy)|r[eé]plica|\b1:1\b|\bclon\b|gen[eé]rico|"
    # correas en cualquier parte del nombre ("Galaxy Watch7 Fabric Band", "Sport Loop", "Correa de silicona")
    r"\b(fabric|sport|silicone|silicona|nylon|milanese|milanesa|link|leather|cuero|metal|trail|alpine|ocean|solo|"
    r"braided|magn[eé]tica)\s+(band|loop|correa|malla)\b|\b(band|loop) (de|para)\b|"
    # juegos con formato "Título - Playstation 3" y plataformas antiguas (no son consolas actuales)
    r"\s[-–]\s*(playstation|ps[1-5]|xbox( one| 360| series [xs])?|nintendo( switch)?|switch)\s*\d?\s*$|"
    r"\b(ps[123]|playstation [123]|psp|ps vita|xbox 360|wii u?|nintendo (ds|3ds|wii))\b", re.I)
REFURB = re.compile(r"reacondicionad|seminuev|refurb|open ?box|usad[oa]|outlet|caja abierta|exhibici[oó]n|"
                    r"grado [abc]\b|\bcpo\b", re.I)
_GEN = re.compile(r"\b(watch|iphone|ipad|galaxy s|s|a|z ?flip|z ?fold|pixel|airpods|buds|fit|series)\s?(\d{1,2})\b", re.I)
_GEN_SLASH = re.compile(r"\b(iphone|galaxy|watch|s|a|pixel)\s?\d{1,2}\s*/\s*\d{1,2}\b", re.I)
# Verificado con datos reales (nov-2026): Hites usa "Pulseras", "Anillos", "CONTROLES", "ACC.ELECT.MENOR";
# Abc mete "Soundbar" en Smart TV. OJO: las consolas de Hites van bajo "VideoJuegos" y TODOS sus audífonos
# bajo "ACC.ELECT.MENOR", por eso "juego" no cuenta y "acc./accesorio" no aplica a la categoría audífonos.
STORE_CAT_ACCESSORY = re.compile(r"correa|pulsera|anillo|funda|carcasa|protector|cargador|\bcables?\b|repuesto|"
                                 r"consumible|\bcontrol(es)?\b|joystick|gift|tarjeta|suscrip|mueble|soporte|"
                                 r"soundbar|\bacc\.|accesori", re.I)


# 📷 cámaras y drones (auditado nov-2026): kits de protección, filtros ND, lentes sueltos
_CAM_ACCESSORY = re.compile(r"protecci[oó]n|\bfilters?\b|\bnd-?(pl|\d)|\bnd ?filter|\bgimbal\b(?!.*\bc[aá]mara)|"
                            r"h[eé]lices?|propellers?|\bcarcasa\b|\bmontura\b|\bflash\b(?!.*c[aá]mara)", re.I)
_LENS = re.compile(r"\b\d{1,3}(-\d{1,3})?\s?mm\b.*\bf/?\d|\blente\b|\bobjetivo\b", re.I)
_CAM_BODY = re.compile(r"c[aá]mara|camera|\beos\b|\balpha\b|\ba6\d00\b|\ba7|nikon z|lumix|fujifilm x|powershot|"
                       r"\bkit\b", re.I)
# 🛴 accesorios de scooters que la tienda lista junto a los scooters
_MOBILITY_ACCESSORY = re.compile(r"\b(seat|asiento|casco|helmet|neum[aá]tico|tire|c[aá]mara de aire|guardabarro|"
                                 r"cargador|bater[ií]a externa)\b", re.I)


def _multi_generation(name: str) -> bool:
    """Un producto real es de UN modelo; los accesorios listan compatibilidades ("Watch9 Watch8", "iPhone 15/16")."""
    if _GEN_SLASH.search(name):
        return True
    fams: dict[str, set] = {}
    for fam, num in _GEN.findall(name):
        fams.setdefault(re.sub(r"\s", "", fam.lower()), set()).add(num)
    return any(len(nums) >= 2 for nums in fams.values())


def is_accessory(name: str, category: str = "", store_category: str | None = None) -> bool:
    name = name or ""
    head = " ".join(name.split()[:3])
    sc = store_category or ""
    if category == "audifonos":                          # ahí "audífonos" es el producto
        head = re.sub(r"aud[ií]fonos?", " ", head, flags=re.I)
        sc = re.sub(r"acc\.[\w.]*|accesori\w*", " ", sc, flags=re.I)
    if category == "camaras":
        if re.search(r"\bkit\s+(de\s+)?(c[aá]mara|canon|sony|nikon|fuji\w*|eos|alpha|lumix)", head, re.I):
            head = re.sub(r"\bkit\b", " ", head, flags=re.I)   # "Kit Cámara Canon R50": el kit ES la cámara
        if _CAM_ACCESSORY.search(name):
            return True
        # un lente suelto ("XC 13-33mm f3.5-6.3 OIS") no es una cámara; un kit cámara + lente sí
        if _LENS.search(name) and not _CAM_BODY.search(name):
            return True
    if category == "movilidad" and _MOBILITY_ACCESSORY.search(name):
        return True
    return bool(ACCESSORY.search(head) or NON_PRODUCT.search(name) or _multi_generation(name)
                or (sc and STORE_CAT_ACCESSORY.search(sc)))


# (patrón que confirma que ES el producto, precio por debajo del cual huele a error)
TOO_CHEAP = {
    # solo consolas actuales: las antiguas/retro sí son baratas
    "consolas": (re.compile(r"playstation 5|\bps5\b|xbox series|nintendo switch|switch (2|oled|lite)|steam deck|"
                            r"rog ally|legion go", re.I), 150_000),
    "notebooks": (re.compile(r"lenovo|\bhp\b|asus|acer|dell|macbook|galaxy book|\bmsi\b|huawei|gigabyte|lg gram|thinkpad|"
                             r"ideapad|vivobook|zenbook|aspire|inspiron|pavilion|victus|omen|legion|\btuf\b|\brog\b", re.I), 150_000),
    "televisores": (re.compile(r"\b(tv|televisor|smart ?tv)\b", re.I), 100_000),
    "celulares": (re.compile(r"iphone 1[5-9]|iphone air|galaxy s2\d|galaxy z|pixel \d|pro max|xiaomi 1[5-9]", re.I), 150_000),
    "tablets": (re.compile(r"ipad|galaxy tab s\d|\btab s\d|xiaomi pad|redmi pad pro|lenovo tab p\d|matepad", re.I), 100_000),
    # con MARCA: los genéricos "Air Buds Pro 3.0", "Earbuds Pro TWS" cuestan $13–37k de verdad (auditado)
    "audifonos": (re.compile(r"airpods (pro|max)|sony.*\b(wh|wf)-?1000|\b(wh|wf)-?1000x|galaxy buds\s?\d*\s?pro|"
                             r"bose (qc|quietcomfort|ultra)|beats (studio|fit|solo|powerbeats)|sennheiser momentum|"
                             r"jbl tour", re.I), 40_000),
    "smartwatches": (re.compile(r"apple watch|galaxy watch|pixel watch|garmin (fenix|forerunner|epix|venu)|"
                                r"huawei watch (gt|\d)|amazfit (gtr|balance|t-rex)", re.I), 50_000),
    "electro": (re.compile(r"refrigerador|lavadora|secadora|side by side|french door|no frost", re.I), 100_000),
    # solo marcas reales: los monitores genéricos de 7" para auto o de 19" cuestan $40–60k de verdad
    "monitores": (re.compile(r"\b(samsung|odyssey|lg|ultragear|asus|\brog\b|\btuf\b|aoc|dell|alienware|lenovo|msi|"
                             r"gigabyte|philips|viewsonic|benq|xiaomi|hp|acer|predator)\b", re.I), 40_000),
    # con MARCA siempre: "Drone Quadcopter Ls-xt6" no es una Fujifilm X-T (auditado)
    "camaras": (re.compile(r"canon.*\beos\b|\beos r\d|sony.*\b(alpha|a6\d00|a7)|\bilce-|\blumix\b|nikon z|"
                           r"fuji\w*.*\bx-?(t|s|h)\d|fujifilm x|gopro|\bhero ?1\d|dji|\bmavic\b|insta360|"
                           r"powershot", re.I), 100_000),
    "impresion3d": (re.compile(r"impresora 3d.*(bambu|creality|elegoo|anycubic|prusa|flashforge|ender|halot|k1|"
                               r"mars|saturn|kobra)|bambu lab", re.I), 60_000),
    "movilidad": (re.compile(r"scooter|monopat[ií]n|bicicleta el[eé]ctrica", re.I), 80_000),
}
# Mínimos escalonados por gama (el mínimo fijo de la categoría es bajo para equipos de $1,5–3 M).
TIERS = {
    "celulares": [(re.compile(r"pro max|\bultra\b|z ?fold|iphone 1[7-9] pro|iphone air", re.I), 400_000),
                  (re.compile(r"iphone 1[6-9]|galaxy s2[5-9]|z ?flip|pixel [89]|xiaomi 1[5-9]", re.I), 250_000)],
    "notebooks": [(re.compile(r"macbook pro|rtx ?50[789]0|rtx ?40[789]0|\bi9\b|i9-|ryzen 9|legion|rog strix|"
                              r"\bomen\b|zenbook (s|duo)|xps|alienware|ultra (x?9|7)|ultra \d-\d{3}h", re.I), 400_000),
                  (re.compile(r"macbook air|\brtx\b|gaming|gamer|\btuf\b|victus|nitro|loq", re.I), 300_000)],
    "consolas": [(re.compile(r"ps5 pro|playstation 5 pro|switch 2|rog ally x|legion go", re.I), 300_000)],
    # OJO: QLED/Mini LED de 43–55" cuestan ~$200k (auditado): solo 65"+ y OLED de verdad suben el mínimo
    "televisores": [(re.compile(r"\b(6[5-9]|[7-9]\d|1\d\d)\s*(\"|''|”|pulg)|\boled\b|neo ?qled", re.I), 200_000)],
    "electro": [(re.compile(r"french door|side by side", re.I), 300_000),
                (re.compile(r"lavadora secadora|lavaseca", re.I), 200_000)],   # auditado: lavaseca 10/6 kg ≈ $290k
    "tablets": [(re.compile(r"ipad pro|galaxy tab s\d+ ?ultra|tab s1\d", re.I), 300_000)],
    "monitores": [(re.compile(r"\boled\b|\b4k\b|\buhd\b|\b(3[2-9]|4\d)\s*(\"|''|”|pulg)|\b(2[4-9]\d|3\d\d|4\d\d) ?hz", re.I), 120_000)],
    "camaras": [(re.compile(r"\ba7\s?(iv|v|r|s|c)|eos r[1-8]\b|nikon z ?[5-9]\b|fuji\w*.*x-?t[45]|mavic \d|dji air|"
                            r"avata", re.I), 500_000),
                (re.compile(r"eos r(10|50|100)|\ba6\d00|nikon z ?(30|50|fc)|x-?s20|dji mini|osmo pocket", re.I), 250_000)],
}
WHAT = {"consolas": "una consola", "notebooks": "un notebook", "televisores": "un televisor", "celulares": "un celular",
        "tablets": "una tablet", "audifonos": "unos audífonos", "smartwatches": "un smartwatch",
        "electro": "un electrodoméstico", "monitores": "un monitor", "camaras": "una cámara/dron",
        "impresion3d": "una impresora 3D", "movilidad": "un scooter eléctrico"}


def category_floor(name: str, category: str, store_category: str | None = None) -> int | None:
    """Piso de precio del producto (categoría + gama) si es reconocible; None si no lo es o es accesorio."""
    rule = TOO_CHEAP.get(category)
    if not rule or is_accessory(name, category, store_category) or not rule[0].search(name or ""):
        return None
    return max([rule[1]] + [f for rx, f in TIERS.get(category, []) if rx.search(name or "")])


def too_cheap(name: str, category: str, price: int, list_price: int | None = None,
              store_category: str | None = None) -> str | None:
    """💥 Precio absurdo para lo que es (ej. PS5 a $49.990), aunque el producto acabe de aparecer y no tenga historial."""
    rule = TOO_CHEAP.get(category)
    if not rule or not price or is_accessory(name, category, store_category):
        return None
    pattern, floor = rule
    if not pattern.search(name or ""):
        return None
    floor = max([floor] + [f for rx, f in TIERS.get(category, []) if rx.search(name or "")])   # gama alta
    if price >= floor:
        return None
    if list_price and list_price < floor:               # capa 5: la propia tienda dice que es un producto barato
        return None
    if REFURB.search(name or ""):                       # reacondicionado/seminuevo: más barato, pero no TANTO
        floor *= 0.6
        if price >= floor:
            return None
    if category == "electro":                           # solo equipos de tamaño completo (no mini/portátil/semi)
        if re.search(r"port[aá]til|mini\b|semi ?autom|viaje|insulina|plegable|camping|auto\b|12v|cooler", name, re.I):
            return None
        liters = [int(x) for x in re.findall(r"(\d{3})\s*(?:l|lts?|litros)\b", name, re.I)]
        kg = [float(x.replace(",", ".")) for x in re.findall(r"(\d{1,2}(?:[.,]\d)?)\s*kg\b", name, re.I)]
        big = (re.search(r"side by side|french door|no frost|bottom freezer|carga frontal|lavadora secadora|lavaseca",
                         name, re.I) or any(l >= 250 for l in liters) or any(k >= 9 for k in kg))
        if not big and price >= 50_000:                 # refrigerador/lavadora chico: error solo bajo $50k
            return None
    if category == "televisores":                       # TVs de 43"+ bajo $100k; de 32–42" bajo $50k
        size = re.search(r"\b(\d{2})\s*(\"|''|”|pulg|pulgadas|\bpul\b)", name or "", re.I) or \
            re.search(r"(?:tv|televisor)\D{0,15}\b(\d{2})\b", name or "", re.I)
        inches = int(size.group(1)) if size else 0
        if inches < 32 or (inches < 43 and price >= 50_000):
            return None
    return f"{clp(price)} es demasiado barato para {WHAT.get(category, 'este producto')} así"


def family_key(gk: str | None, category: str) -> str | None:
    """Familia de modelo: el mismo celular en cualquier capacidad ("apple iphone 18 pro max"), o el código exacto
    en el resto de categorías. Reacondicionados y accesorios tienen su propio grupo y no entran."""
    if not gk or ":" in gk:
        return None
    if category == "celulares":                          # reacondicionados: familia propia ("… reacond")
        refurb = gk.endswith(" reacond")
        fam = re.sub(r"\s\d+gb$", "", gk.removesuffix(" reacond"))
        return (fam + (" reacond" if refurb else "")) if len(fam.split()) >= 3 else None
    return gk if comparable(gk) else None


def family_error(store: PriceStore, p, gk: str | None, category: str) -> tuple[str, int] | None:
    """(motivo, precio de referencia) si p.price < 45 % de la mediana de su familia en todo el catálogo."""
    fam = family_key(gk, category)
    if not fam:
        return None
    base, suffix = (fam.removesuffix(" reacond"), " reacond") if fam.endswith(" reacond") else (fam, "")
    rows = store.conn.execute(
        "SELECT price, group_key, name, badges FROM products WHERE key<>? AND missing_since IS NULL AND price>0 "
        "AND (group_key=? OR group_key LIKE ?)", (p.key, fam, base + " %")).fetchall()
    exact = re.compile(re.escape(base) + r"( \d+gb)?" + re.escape(suffix) + "$")
    # sin revendedores del marketplace ni packs: inflan la referencia (auditado: WF-1000XM5 "vale" $679.592, un
    # monitor Caixun $219.990, unos Philips TAT1109 $99.990)
    prices = [r[0] for r in rows if exact.match(r[1] or "") and ganga_ref_ok(r[2], r[3])]
    if not prices:
        return None
    ref = statistics.median(prices)
    if ref < config.FAMILY_ERROR_MIN_REF:
        return None                     # genéricos baratos (audífonos de $10–30k): mucho ruido, poco valor
    if p.list_price and p.list_price < ref * 0.8:
        return None                     # la propia tienda dice que su "normal" es mucho menor: referencia inflada
    ratio = config.PRICE_ERROR_RATIO if len(prices) >= 2 else config.PRICE_ERROR_RATIO * 0.75   # 1 sola ref: más estricto
    if p.price < ref * ratio:
        return f"{(1 - p.price / ref) * 100:.0f}% bajo lo que cuesta este modelo en el catálogo ({clp(int(ref))})", int(ref)
    return None


def bargain(store: PriceStore, p, gk: str | None, category: str) -> tuple[str, int] | None:
    """🔥 Ganga: muy por debajo de lo que cuesta el modelo NUEVO en el catálogo (sirve también para seminuevos,
    cuyo "precio normal" en tiendas como Entel es el del equipo nuevo al lanzarse y no sirve de referencia).
    Devuelve (motivo, precio de referencia) o None."""
    if not p.price or is_accessory(p.name, category, getattr(p, "store_category", None)) or _marketplace(p):
        return None
    fam = family_key(gk, category)
    if not fam:
        return None
    refurb = fam.endswith(" reacond")
    base = fam.removesuffix(" reacond")
    rows = store.conn.execute(
        "SELECT price, group_key, name, badges FROM products WHERE key<>? AND missing_since IS NULL AND price>0 "
        "AND (group_key=? OR group_key LIKE ?)", (p.key, base, base + " %")).fetchall()
    exact = re.compile(re.escape(base) + r"( \d+gb)?$")              # solo equipos NUEVOS de ese modelo
    # nuevo: mediana SIN revendedores del marketplace (inflan). Seminuevo: el nuevo más barato CON marketplace
    # (así es más estricto). En ambos casos sin packs/bundles.
    prices = [r[0] for r in rows if exact.match(r[1] or "") and ganga_ref_ok(r[2], r[3], marketplace_ok=refurb)]
    if not prices:
        return None
    peers_rx = re.compile(re.escape(base) + r"( \d+gb)? reacond$")
    peers = [r[0] for r in rows if refurb and peers_rx.match(r[1] or "") and ganga_ref_ok(r[2], r[3], True)]
    return bargain_vs(p.price, prices, refurb, p.list_price, peers)


# Referencias que NO sirven para decir "esto cuesta X": revendedores del marketplace (inflan: un monitor Caixun de
# $89.990 lo revenden a $219.990–289.990) y packs/bundles ("… + Sound Pocket"). Auditado con datos reales nov-2026.
_BUNDLE = re.compile(r"\s\+\s*[a-záéíóú]|\bcon regalo\b|\bpack\b|\bbundle\b|\bcombo\b", re.I)


def ganga_ref_ok(name: str | None, badges, marketplace_ok: bool = False) -> bool:
    if isinstance(badges, str):
        badges = json.loads(badges) if badges.startswith("[") else [badges]
    mkt = any(str(b).startswith("Vendedor: ") for b in badges or [])
    return (marketplace_ok or not mkt) and not _BUNDLE.search(name or "")


def bargain_vs(price: int, new_prices: list[int], refurb: bool, list_price: int | None = None,
               refurb_peers: list[int] | None = None) -> tuple[str, int] | None:
    """Regla pura de ganga (la usan las alertas y la app): precio vs mediana del modelo NUEVO."""
    if not new_prices or not price:
        return None
    # seminuevo: contra el equipo nuevo MÁS BARATO (los listados viejos guardan el precio de lanzamiento: un Galaxy S23
    # "nuevo" a $1.199.990 haría parecer ganga un reacondicionado normal de $359.990)
    ref = min(new_prices) if refurb else statistics.median(new_prices)
    if ref < config.GANGA_MIN_REF:
        return None
    if refurb and refurb_peers and price >= statistics.median(refurb_peers) * 0.77:
        return None              # otros seminuevos de ese modelo cuestan parecido: es su precio normal de reacondicionado
    if list_price and not refurb and list_price < ref * 0.8:
        return None              # la propia tienda dice que su precio normal es mucho menor: la referencia está inflada
    ratio = config.GANGA_REFURB_RATIO if refurb else config.GANGA_NEW_RATIO
    if len(new_prices) == 1:
        ratio *= 0.9                                                     # 1 sola referencia: más estricto
    if price <= ref * ratio:
        what = "el mismo modelo nuevo" if refurb else "este modelo en el resto del catálogo"
        return f"{(1 - price / ref) * 100:.0f}% más barato que {what} ({clp(int(ref))})", int(ref)
    return None


def _marketplace(p) -> bool:
    return any(b.startswith("Vendedor: ") for b in (p.badges or []))


def price_error_reasons(p, category: str, check_card: bool = True, is_new: bool = True,
                        drop_pct: float = 0.0) -> list[str]:
    """Reglas de error que NO necesitan historial. Lista de motivos.

    is_new=True (producto recién aparecido): solo "demasiado barato para lo que es". El "% de descuento sobre el
    precio normal" NO basta, porque muchas tiendas inflan el "normal" (auditado con datos reales: Entel, Abc...).
    is_new=False (un producto conocido que BAJÓ): también cuenta el descuento ≥70 % sobre el precio normal."""
    sc = getattr(p, "store_category", None)
    if is_accessory(p.name, category, sc):
        return []
    refurb = bool(REFURB.search(p.name or ""))          # su "precio normal" suele ser el de un equipo nuevo
    out = []
    cheap = too_cheap(p.name, category, p.price, p.list_price, sc)
    discount_ok = (not is_new and not refurb and drop_pct >= 30 and p.list_price and p.list_price >= config.ERROR_MIN_LIST
                   and p.discount_pct and p.discount_pct >= config.ERROR_DISCOUNT_PCT and not _marketplace(p))  # capa 6
    # descuento EXTREMO en un producto caro: ni las tiendas que inflan el "normal" llegan a -80 % (máx. visto: -76 %)
    # …y solo si el producto es reconocible y su precio está cerca del piso de su gama: un "normal" absurdo
    # (Hites: monitor Legion 25i a $179.990 "normal $999.990") no basta por sí solo.
    floor = category_floor(p.name, category, sc)
    discount_ok = discount_ok or bool(
        is_new and not refurb and not _marketplace(p) and p.list_price and p.list_price >= config.ERROR_EXTREME_LIST
        and p.discount_pct and p.discount_pct >= config.ERROR_EXTREME_PCT and floor and p.price < floor * 3)
    if discount_ok:
        out.append(f"-{p.discount_pct:.0f}% sobre su precio normal ({clp(p.list_price)})")
    elif cheap:
        out.append(cheap)
    if check_card and p.card_price and p.card_price < p.price and not out:
        ref = p.list_price or p.price
        disc = (1 - p.card_price / ref) * 100 if ref else 0
        card_cheap = too_cheap(p.name, category, p.card_price, p.list_price, sc)
        card_disc_ok = (not is_new and not refurb and drop_pct >= 30 and ref >= config.ERROR_MIN_LIST
                        and disc >= config.ERROR_DISCOUNT_PCT and not _marketplace(p))
        if card_disc_ok or card_cheap:
            out.append(f"💳 {clp(p.card_price)} pagando con la tarjeta de la tienda (-{disc:.0f}% vs {clp(ref)})")
    return out


def evaluate(obs: Observation, store: PriceStore, watched: bool,
             targets: list | None = None, category: str = "celulares") -> list[Alert]:
    p = obs.product
    alerts: list[Alert] = []
    accessory = is_accessory(p.name, category, getattr(p, "store_category", None))

    # 💥 errores de precio sin historial: precio público (si es nuevo o bajó) y con tarjeta (si es nuevo o bajó)
    price_moved = obs.is_new or obs.direction == "down"
    card_moved = bool(p.card_price) and (obs.previous_card is None or p.card_price < obs.previous_card)
    if price_moved or card_moved:
        drop = max(-obs.pct if obs.previous else 0,
                   (1 - p.card_price / obs.previous_card) * 100 if p.card_price and obs.previous_card else 0)
        for reason in price_error_reasons(p, category, check_card=card_moved, is_new=obs.is_new, drop_pct=drop):
            card = reason.startswith("💳")
            alerts.append(Alert("price_error", obs, "💥 ¿ERROR DE PRECIO CON TARJETA?" if card else "💥 ¿ERROR DE PRECIO?",
                                [reason], 0, p.list_price or (p.price if card else obs.previous)))

    # precio "de siempre": el mínimo de los últimos 30 días ANTES de esta lectura. Si la tienda lo subió y luego lo
    # "bajó" al precio de antes (auditado: Abc subió ~35 refrigeradores 15–70 % antes del Black Friday), no hay bajada.
    base = None if obs.is_new else store.baseline_min(p.key, config.BASELINE_DAYS)

    # 🦖 GODZILLA: caída brutal vs el máximo de las últimas 24 h… y también vs su precio de siempre
    if not obs.is_new:
        peak = store.max_price_since(p.key, ago(hours=24)) or obs.previous
        real = (1 - p.price / base) * 100 if base else 0
        if (peak and peak >= config.GODZILLA_MIN_PEAK and not accessory
                and p.price <= peak * (1 - config.GODZILLA_DROP_PCT / 100) and real >= config.GODZILLA_REAL_PCT):
            drop = (1 - p.price / peak) * 100
            lines = [f"Cayó <b>{drop:.0f}%</b> en 24 h (de {clp(peak)} a {clp(p.price)})"]
            if base and peak > base * 1.1:
                lines.append(f"⚠️ Antes lo habían subido: su precio habitual era {clp(base)} (ahorro real -{real:.0f}%)")
            alerts.append(Alert("godzilla", obs, "🦖🔥 ALERTA GODZILLA 🔥🦖", lines, 0, peak))

    # 💥 muy por debajo de lo que cuesta ESE modelo en el resto del catálogo (relativo: sirve para productos caros)
    if (obs.is_new or obs.direction == "down") and not accessory and not any(a.kind == "price_error" for a in alerts):
        hit = family_error(store, p, _group(store, p.key), category)
        if hit:
            alerts.append(Alert("price_error", obs, "💥 ¿ERROR DE PRECIO?", [hit[0]], 0, hit[1]))

    if obs.is_new:
        return alerts

    # 🎯 precio objetivo
    for t in (config.TARGETS if targets is None else targets):
        if t.matches(p.key, p.name, p.site) and p.price <= t.target_price and (
                obs.previous > t.target_price or obs.direction == "down"):
            alerts.append(Alert("target", obs, "🎯 ¡PRECIO OBJETIVO!", [f"{t.label} ({clp(t.target_price)})"], 1))
            break

    real_drop = obs.direction == "down" and (watched or real_vs(p.price, base))   # bajó bajo su precio de siempre
    if (obs.direction == "down" and base and obs.previous and obs.previous > base * 1.1 and not real_vs(p.price, base)
            and obs.previous >= config.DROP_ALERT_MIN_PRICE and not accessory):
        import inflation                     # 📈 lo subieron y lo "bajaron" al precio de siempre
        inflation.log(store, p, "subió y bajó", obs.previous, base, "historial",
                      f"lo subieron a {clp(obs.previous)} y volvió a {clp(p.price)} (precio habitual {clp(base)})")
    if is_real_historic_low(obs) and (abs(obs.pct) >= 1 if watched else notable_drop(obs)) and real_drop:
        alerts.append(Alert("historic_low", obs, "🏆 MÍNIMO HISTÓRICO", [f"Mínimo anterior: {clp(obs.historic_min)}"], 2))
    elif obs.direction == "down" and (abs(obs.pct) >= config.MIN_CHANGE_PCT if watched else notable_drop(obs)) and real_drop:
        alerts.append(Alert("drop", obs, "📉 Bajó de precio", [], 3))
    elif obs.direction == "up" and config.NOTIFY_ON_RISE and abs(obs.pct) >= config.MIN_CHANGE_PCT:
        alerts.append(Alert("rise", obs, "📈 Subió de precio", [], 6))
    elif (obs.previous_card and p.card_price and p.card_price < obs.previous_card
          and (1 - p.card_price / obs.previous_card) * 100 >= (config.MIN_CHANGE_PCT if watched else config.DROP_ALERT_PCT)
          and (watched or obs.previous_card - p.card_price >= config.DROP_ALERT_MIN_SAVING)):
        pct = (1 - p.card_price / obs.previous_card) * 100
        alerts.append(Alert("card_drop", obs, "💳 Bajó con tarjeta",
                            [f"💳 {clp(p.card_price)} con tarjeta de la tienda (antes {clp(obs.previous_card)}, -{pct:.0f}%)"], 3))

    if watched and obs.was_in_stock is not None and p.in_stock is not None and obs.was_in_stock != p.in_stock:
        alerts.append(Alert("stock_back" if p.in_stock else "stock_out", obs,
                            "✅ Volvió el stock" if p.in_stock else "⛔ Se agotó", [], 2 if p.in_stock else 4))
    if watched and obs.was_missing:
        alerts.append(Alert("back", obs, "🔁 Volvió a aparecer en la tienda", [], 3))
    return alerts


def real_vs(price: int, base: int | None) -> bool:
    """¿El precio quedó de verdad bajo su precio de siempre (mínimo 30 días)? Mismos umbrales que notable_drop."""
    if not base:
        return True                          # sin historial de 30 días no se puede saber: no se bloquea
    return (price < base * (1 - config.DROP_ALERT_PCT / 100) and base - price >= config.DROP_ALERT_MIN_SAVING)


def notable_drop(obs: Observation, half: bool = False) -> bool:
    """🔕 ¿Vale un aviso? (A5) >= 10 %, ahorro >= $10.000 y producto de $50.000+. half=True: mínimos históricos
    (ya son especiales) con la mitad del porcentaje."""
    if not obs.previous or obs.product.price >= obs.previous:
        return False
    pct = config.DROP_ALERT_PCT / (2 if half else 1)
    return (abs(obs.pct) >= pct and obs.previous >= config.DROP_ALERT_MIN_PRICE
            and obs.previous - obs.product.price >= config.DROP_ALERT_MIN_SAVING / (2 if half else 1))


def _group(store: PriceStore, key: str) -> str:
    row = store.conn.execute("SELECT group_key FROM products WHERE key=?", (key,)).fetchone()
    return row[0] if row else ""


def clp(n: int | float | None) -> str:
    return "-" if n is None else "$" + f"{int(n):,}".replace(",", ".")


# ---------------------------------------------------------------- predicción (dashboard)
def predict(history: list[tuple[str, int]]) -> dict:
    """Estadística simple sobre el historial de cambios. No es magia: necesita semanas de datos."""
    if len(history) < 4:
        return {"label": "datos insuficientes", "confidence": "baja"}
    pts = [(datetime.fromisoformat(ts), price) for ts, price in history]
    span_days = (pts[-1][0] - pts[0][0]).total_seconds() / 86400 or 1
    drops = [(pts[i][0], pts[i - 1][1], pts[i][1]) for i in range(1, len(pts)) if pts[i][1] < pts[i - 1][1]]
    weekdays = [d.weekday() for d, *_ in drops]
    names = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    best_day = names[max(set(weekdays), key=weekdays.count)] if weekdays else None
    avg_gap = span_days / len(drops) if drops else None
    avg_drop = statistics.mean((1 - new / old) * 100 for _, old, new in drops) if drops else 0
    cutoff = pts[-1][0] - timedelta(days=30)
    recent = [p for d, p in pts if d >= cutoff] or [pts[-1][1]]
    trend = (recent[-1] / recent[0] - 1) * 100 if recent[0] else 0
    prob7 = min(0.95, 7 / avg_gap) if avg_gap else 0.05
    lo, hi = min(p for _, p in pts), max(p for _, p in pts)
    pos = (pts[-1][1] - lo) / (hi - lo) if hi > lo else 0
    verdict = ("🟢 buen momento: está cerca de su mínimo" if pos <= 0.15 else
               "🟡 espera: suele bajar pronto" if prob7 >= 0.5 else "⚪ precio estable")
    return {"label": verdict, "prob_drop_7d": round(prob7, 2), "avg_days_between_drops": round(avg_gap, 1) if avg_gap else None,
            "avg_drop_pct": round(avg_drop, 1), "best_weekday": best_day, "trend_30d_pct": round(trend, 1),
            "position_in_range": round(pos, 2), "confidence": "media" if span_days >= 21 else "baja"}
