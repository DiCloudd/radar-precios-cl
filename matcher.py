"""Agrupa el mismo modelo entre tiendas normalizando nombres.

"Celular Samsung Galaxy A57 5G 128GB - Navy"   -> "samsung galaxy a57 128gb"
"Smartphone Galaxy A57 5G 128 GB Negro."       -> "samsung galaxy a57 128gb"   (marca desde el campo brand)
"IPhone 13 128GB Reacondicionado Excelente"    -> "apple iphone 13 128gb reacond"
"""
from __future__ import annotations

import re
import unicodedata

BRANDS = {
    "samsung": "samsung", "galaxy": "samsung", "apple": "apple", "iphone": "apple", "xiaomi": "xiaomi",
    "redmi": "xiaomi", "poco": "xiaomi", "motorola": "motorola", "moto": "motorola", "honor": "honor",
    "huawei": "huawei", "oppo": "oppo", "realme": "realme", "vivo": "vivo", "zte": "zte", "nubia": "zte",
    "tcl": "tcl", "nokia": "nokia", "google": "google", "pixel": "google", "infinix": "infinix",
    "tecno": "tecno", "alcatel": "alcatel", "oneplus": "oneplus", "sony": "sony", "mlab": "mlab",
}
# palabras que no identifican el modelo
NOISE = set("""
celular celulares smartphone telefono movil liberado liberada equipo nuevo nueva pack bundle con de y la el
dual sim esim ram rom 4g 5g lte gb tb pulgadas pantalla excelente muy bueno bueno version global incluye
negro negra blanco blanca azul celeste verde rojo roja morado lila rosado rosa dorado plateado plata gris
grafito titanio natural desierto amarillo naranjo naranja violeta purpura purple black white blue green
gray grey silver gold pink navy charcoal midnight starlight awesome cosmic phantom mint lavender cream
ice lime graphite burdeo crema ocean sky marine jade obsidian porcelain titanium desert
""".split())
REFURB = re.compile(r"reacondicionad|refurb|seminuevo|open box|usado", re.I)


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def group_key(name: str, brand: str | None = None) -> str:
    raw = _ascii(name or "").lower()
    refurb = bool(REFURB.search(raw))
    raw = re.sub(r"(?<=[a-z0-9])\+(?=\s|$|\d)", " plus ", raw)   # "Pro+ 512GB" -> "pro plus"
    raw = raw.split("+")[0]                                       # "Liberado+Gamepad", " + bundle"
    raw = re.sub(r"\(.*?\)", " ", raw)
    caps = re.findall(r"(\d+)\s*(gb|tb)\b", raw)
    storage = None
    for num, unit in caps:                                    # el almacenamiento es la mayor capacidad >= 32GB
        gb = int(num) * (1024 if unit == "tb" else 1)
        if gb >= 32 and (storage is None or gb > storage):
            storage = gb
    raw = re.sub(r"\d+\s*(gb|tb)\b", " ", raw)
    raw = re.sub(r"\d+[.,]\d+\s*(\"|''|pulgadas|in\b)?", " ", raw)   # tamaños de pantalla 6,88" / 6.9''
    raw = re.sub(r"[^a-z0-9 ]+", " ", raw)
    # números de hasta 4 dígitos son parte del modelo (iPhone 17, Honor 600, Nokia 3310); los SKU largos no
    tokens = [t for t in raw.split()
              if t not in NOISE and (not t.isdigit() or len(t) <= 4)]
    b = _ascii((brand or "").lower()).strip()
    b = BRANDS.get(b, b) if b else next((BRANDS[t] for t in tokens if t in BRANDS), "")
    tokens = [t for t in tokens if t != b and t not in ("reacondicionado", "reacondicionada")]
    if b == "apple":
        tokens = [t for t in tokens if t != "apple"]
    model = " ".join(list(dict.fromkeys(tokens))[:6])         # sin duplicados, máx 6 tokens
    parts = [b, model, f"{storage}gb" if storage else "", "reacond" if refurb else ""]
    return " ".join(p for p in parts if p).strip()


def comparable(key: str | None) -> bool:
    """Solo se comparan entre tiendas los grupos con almacenamiento conocido (evita mezclar 128 GB con 512 GB)."""
    return bool(key) and bool(re.search(r"\b\d+gb\b", key)) and len(key.split()) >= 3
