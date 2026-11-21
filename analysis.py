"""Reglas de alerta: bajadas, mínimos reales, ofertas falsas, precio objetivo, stock y 🦖 Godzilla."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import config
from matcher import comparable
from storage import Observation, PriceStore, ago


@dataclass
class Alert:
    kind: str                      # godzilla | price_error | target | historic_low | drop | rise | stock_out | stock_back | missing | back
    obs: Observation | None
    title: str
    lines: list[str] = field(default_factory=list)
    priority: int = 5              # menor = más importante

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


def evaluate(obs: Observation, store: PriceStore, watched: bool,
             targets: list | None = None) -> list[Alert]:
    p = obs.product
    alerts: list[Alert] = []

    # 🦖 GODZILLA: caída brutal vs el máximo de las últimas 24 h
    if not obs.is_new:
        peak = store.max_price_since(p.key, ago(hours=24)) or obs.previous
        if peak and p.price <= peak * (1 - config.GODZILLA_DROP_PCT / 100):
            drop = (1 - p.price / peak) * 100
            alerts.append(Alert("godzilla", obs, "🦖🔥 ALERTA GODZILLA 🔥🦖",
                                [f"Cayó <b>{drop:.0f}%</b> en 24 h (de {clp(peak)} a {clp(p.price)})"], 0))

    # 💥 posible error de precio: muy por debajo del mismo modelo en otras tiendas
    gk = _group(store, p.key)
    if (obs.is_new or obs.direction == "down") and comparable(gk):
        others = store.group_prices(gk, p.key)
        if len(others) >= 2:
            med = statistics.median(others)
            if p.price < med * config.PRICE_ERROR_RATIO:
                alerts.append(Alert("price_error", obs, "💥 ¿ERROR DE PRECIO?",
                                    [f"{(1 - p.price / med) * 100:.0f}% bajo la mediana de otras tiendas ({clp(int(med))})"], 0))

    if obs.is_new:
        return alerts

    # 🎯 precio objetivo
    for t in (config.TARGETS if targets is None else targets):
        if t.matches(p.key, p.name, p.site) and p.price <= t.target_price and (
                obs.previous > t.target_price or obs.direction == "down"):
            alerts.append(Alert("target", obs, "🎯 ¡PRECIO OBJETIVO!", [f"{t.label} ({clp(t.target_price)})"], 1))
            break

    if is_real_historic_low(obs) and abs(obs.pct) >= 1:
        alerts.append(Alert("historic_low", obs, "🏆 MÍNIMO HISTÓRICO", [f"Mínimo anterior: {clp(obs.historic_min)}"], 2))
    elif obs.direction == "down" and abs(obs.pct) >= config.MIN_CHANGE_PCT:
        alerts.append(Alert("drop", obs, "📉 Bajó de precio", [], 3))
    elif obs.direction == "up" and config.NOTIFY_ON_RISE and abs(obs.pct) >= config.MIN_CHANGE_PCT:
        alerts.append(Alert("rise", obs, "📈 Subió de precio", [], 6))

    if watched and obs.was_in_stock is not None and p.in_stock is not None and obs.was_in_stock != p.in_stock:
        alerts.append(Alert("stock_back" if p.in_stock else "stock_out", obs,
                            "✅ Volvió el stock" if p.in_stock else "⛔ Se agotó", [], 2 if p.in_stock else 4))
    if watched and obs.was_missing:
        alerts.append(Alert("back", obs, "🔁 Volvió a aparecer en la tienda", [], 3))
    return alerts


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
