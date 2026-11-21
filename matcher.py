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


_CODE = re.compile(r"\b(?=[a-z0-9-]*\d)(?=[a-z0-9-]*[a-z])[a-z0-9]+(?:-[a-z0-9]+)*\b")
_NOT_CODE = re.compile(r"^(\d+(gb|tb|hz|w|mah|mp|cm|k|pulgadas)|\d+x\d+|ps\d|4k|8k|5g|4g|wifi\d*|usb\w*|"
                       r"i[3579]|r[3579]|ddr\d|hdmi\d*|rtx\d+|gtx\d+|\d+(th|gen)|core\w*|ultra\d*)$")


def model_code(name: str) -> str | None:
    """Código de modelo (UN55DU7000, 82X1004GLM, CFI-2015): el identificador más fiable en TV, notebooks y consolas."""
    cands = [t for t in _CODE.findall(_ascii(name or "").lower()) if len(t) >= 6 and not _NOT_CODE.match(t)]
    return max(cands, key=len) if cands else None


def group_key(name: str, brand: str | None = None, category: str = "celulares") -> str:
    if category != "celulares":
        code = model_code(name)
        words = _ascii(name or "").lower().split()
        if code:
            return code                                       # el código ya es único; la marca varía entre tiendas
        return f"{category}: " + " ".join(words[:6])          # sin código de modelo -> no comparable
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
    """Se comparan entre tiendas: celulares con almacenamiento conocido (evita mezclar 128 GB con 512 GB)
    y productos de otras categorías identificados por código de modelo ("samsung un55du7000gxzs")."""
    if not key or ":" in key:
        return False
    if re.search(r"\b\d+gb\b", key) and len(key.split()) >= 3:
        return True
    parts = key.split()
    return len(parts) <= 2 and bool(_CODE.fullmatch(parts[-1])) and len(parts[-1]) >= 6 and not _NOT_CODE.match(parts[-1])
