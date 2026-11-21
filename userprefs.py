"""Preferencias que el usuario cambia desde Telegram (se guardan en la BD, viajan con ella entre ventanas):
objetivos de precio, productos vigilados, silencios y el chat dueño del bot."""
from __future__ import annotations

import time

import config
from storage import PriceStore


# ---------------------------------------------------------------- chat dueño
def owner_chat(store: PriceStore) -> str | None:
    return config.TELEGRAM_CHAT_ID or store.get_meta("tg:chat")


def set_owner_chat(store: PriceStore, chat_id: str) -> None:
    store.set_meta("tg:chat", str(chat_id))


# ---------------------------------------------------------------- objetivos
def db_targets(store: PriceStore) -> list[dict]:
    return store.get_meta("tg:targets", [])


def all_targets(store: PriceStore) -> list[config.Target]:
    out = list(config.TARGETS)
    for t in db_targets(store):
        out.append(config.Target(t["label"], int(t["target_price"]), tuple(t.get("keywords", [])),
                                 t.get("product_key")))
    return out


def add_target(store: PriceStore, label: str, price: int, keywords=(), product_key: str | None = None) -> None:
    items = [t for t in db_targets(store)
             if not (product_key and t.get("product_key") == product_key)]   # uno por producto
    items.append({"label": label, "target_price": int(price), "keywords": list(keywords), "product_key": product_key})
    store.set_meta("tg:targets", items)


def remove_target(store: PriceStore, index: int) -> dict | None:
    items = db_targets(store)
    if 0 <= index < len(items):
        gone = items.pop(index)
        store.set_meta("tg:targets", items)
        return gone
    return None


# ---------------------------------------------------------------- productos vigilados (/seguir)
def db_watches(store: PriceStore) -> list[dict]:
    return store.get_meta("tg:watches", [])


def all_watches(store: PriceStore) -> list[config.Watch]:
    seen = {w.url for w in config.WATCHES}
    extra = [config.Watch(w["name"], w["url"]) for w in db_watches(store) if w["url"] not in seen]
    return list(config.WATCHES) + extra


def add_watch(store: PriceStore, url: str, name: str | None = None) -> bool:
    items = db_watches(store)
    if any(w["url"] == url for w in items):
        return False
    items.append({"name": name or url.split("/")[-1][:60] or url, "url": url})
    store.set_meta("tg:watches", items)
    return True


def remove_watch(store: PriceStore, index: int) -> dict | None:
    items = db_watches(store)
    if 0 <= index < len(items):
        gone = items.pop(index)
        store.set_meta("tg:watches", items)
        return gone
    return None


# ---------------------------------------------------------------- silencios
def mute(store: PriceStore, key: str, hours: float) -> None:
    store.set_meta(f"mute:{key}", time.time() + hours * 3600)


def is_muted(store: PriceStore, key: str | None) -> bool:
    return bool(key) and (store.get_meta(f"mute:{key}", 0) or 0) > time.time()
