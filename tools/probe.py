"""Diagnóstico de tiendas: ¿responde?, ¿robots.txt lo permite?, ¿bloquea?, ¿qué datos trae?

    python tools/probe.py URL [URL ...] [--dump carpeta]

Corre igual en local y en GitHub Actions (workflow "probe"), para ver cómo responde cada tienda a las IPs de la nube.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "es-CL,es;q=0.9", "Accept": "text/html,application/json,*/*;q=0.8"}
MARKERS = ["__NEXT_DATA__", "self.__next_f", "__NUXT__", "__PRELOADED_STATE__", "vtex", "Shopify", "cnstrc",
           "_px", "cf-chl", "Just a moment", "captcha", "Access Denied", "incapsula", "Robot or human"]


def robots_ok(client: httpx.Client, url: str) -> bool | str:
    base = "{0.scheme}://{0.netloc}".format(urlsplit(url))
    try:
        r = client.get(base + "/robots.txt")
        if r.status_code >= 400:
            return True
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp.can_fetch("*", url)
    except httpx.HTTPError as exc:
        return f"err {type(exc).__name__}"


def probe(client: httpx.Client, url: str, dump: Path | None) -> dict:
    out: dict = {"url": url, "robots": robots_ok(client, url)}
    for attempt in range(3):
        try:
            r = client.get(url)
            break
        except httpx.HTTPError as exc:
            out["err"] = type(exc).__name__
            time.sleep(3)
    else:
        return out
    t = r.text
    out.update(status=r.status_code, final=str(r.url)[:120], len=len(t),
               title=(re.search(r"<title[^>]*>(.*?)</title>", t, re.S) or [None, ""])[1].strip()[:70],
               markers=[m for m in MARKERS if m.lower() in t.lower()],
               ld=re.findall(r'"@type"\s*:\s*"(Product|ItemList|Offer|AggregateOffer)"', t)[:6],
               prices=len(re.findall(r"\$\s?\d{1,3}(?:\.\d{3})+", t)))
    if "json" in r.headers.get("content-type", ""):
        out["json_head"] = t[:400]
    if dump:
        dump.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^a-z0-9]+", "_", url.lower())[8:90] + (".json" if "json" in r.headers.get("content-type", "") else ".html")
        (dump / name).write_text(t, encoding="utf-8")
    return out


def main() -> None:
    args = sys.argv[1:]
    dump = None
    if "--dump" in args:
        i = args.index("--dump")
        dump = Path(args[i + 1])
        del args[i:i + 2]
    headers = dict(H)
    if "--referer" in args:                     # algunas APIs exigen el Referer de su propia página (como un navegador)
        i = args.index("--referer")
        headers["Referer"] = args[i + 1]
        del args[i:i + 2]
    with httpx.Client(headers=headers, follow_redirects=True, timeout=30) as client:
        for url in args:
            print(json.dumps(probe(client, url, dump), ensure_ascii=False), flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
