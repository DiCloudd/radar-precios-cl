"""Utilidades: normalización de precios, JSON-LD, URLs."""
from __future__ import annotations

import html
import json
import re
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

_NUM_RE = re.compile(r"\d[\d.,]*")


def parse_price(value: Any) -> int | None:
    """Normaliza "$  329.990", "329990", 329990.0, "449990.000000", ["329.990"] -> 329990 (CLP)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple)):
        return parse_price(value[0]) if value else None
    if isinstance(value, (int, float)):
        return int(round(value)) if value > 0 else None
    s = str(value).strip()
    # Número "de máquina" con decimales: 449990.000000 / 499989.999
    if re.fullmatch(r"\d+\.\d+", s) and len(s.split(".")[1]) != 3:
        n = int(round(float(s)))
        return n if n > 0 else None
    if re.fullmatch(r"\d+\.\d{3}", s) and len(s.split(".")[0]) > 3:  # 499989.999
        n = int(round(float(s)))
        return n if n > 0 else None
    m = _NUM_RE.search(s)
    if not m:
        return None
    raw = m.group(0)
    if "," in raw:                  # formato chileno: coma = decimales
        raw = raw.split(",")[0]
    digits = raw.replace(".", "")
    if not digits.isdigit():
        return None
    n = int(digits)
    return n if n > 0 else None


def prices_in_text(text: str) -> list[int]:
    return [p for p in (parse_price(m) for m in re.findall(r"\$\s*[\d.]+", text or "")) if p]


def with_query(url: str, **params: Any) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update({k: str(v) for k, v in params.items()})
    return urlunsplit(parts._replace(query=urlencode(query)))


def absolute(base: str, href: str | None) -> str:
    return urljoin(base, href or "")


def unescape(s: str | None) -> str:
    return html.unescape(s or "").strip()


# ---------------------------------------------------------------- JSON embebido
_LD_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)
_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def json_ld_nodes(page_html: str) -> list[dict]:
    out: list[dict] = []
    for raw in _LD_RE.findall(page_html):
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict):
                out.append(node)
                out.extend(n for n in node.get("@graph", []) if isinstance(n, dict))
    return out


def next_data(page_html: str) -> dict | None:
    m = _NEXT_RE.search(page_html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def ld_item_list(nodes: Iterable[dict]) -> list[dict]:
    """Productos de un ItemList (directo o como WebPage.mainEntity)."""
    for node in nodes:
        for cand in (node, node.get("mainEntity") or {}):
            if isinstance(cand, dict) and cand.get("@type") == "ItemList":
                items = []
                for el in cand.get("itemListElement", []):
                    item = el.get("item") if isinstance(el, dict) else None
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        items.append(item)
                if items:
                    return items
    return []


def ld_offer_prices(item: dict) -> list[int]:
    offers = item.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]
    out = []
    for o in offers:
        if isinstance(o, dict):
            for k in ("price", "lowPrice"):
                p = parse_price(o.get(k))
                if p:
                    out.append(p)
                    break
    return out


def ld_in_stock(item: dict) -> bool | None:
    offers = item.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]
    for o in offers:
        av = str((o or {}).get("availability", ""))
        if av:
            return "InStock" in av or "LimitedAvailability" in av
    return None


def dedupe(products: list) -> list:
    seen: dict[str, Any] = {}
    for p in products:
        if p.price and p.key not in seen:
            seen[p.key] = p
    return list(seen.values())
