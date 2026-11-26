"""Eventos comerciales chilenos: durante estas ventanas el monitor revisa EVENT_SPEEDUP veces más rápido.

Las fechas se anuncian cada año (CCS / tiendas). Edita la lista cuando se confirmen;
las marcadas estimated=True son aproximaciones según años anteriores.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import config


@dataclass(frozen=True)
class Event:
    name: str
    start: date
    end: date
    estimated: bool = False
    start_hour: int = 0      # hora Chile en que parte el día `start` (las tiendas lanzan la noche anterior)

    def active(self, when: date | datetime) -> bool:
        if isinstance(when, datetime):
            if when.date() == self.start:
                return when.hour >= self.start_hour
            when = when.date()
        return self.start <= when <= self.end


EVENTS: list[Event] = [
    # las tiendas chilenas sueltan ofertas (y errores de precio) desde ~las 22:00 de la noche anterior
    Event("Black Friday", date(2026, 11, 26), date(2026, 11, 30), start_hour=21),
    Event("Cyber Monday (post Black Friday)", date(2026, 11, 30), date(2026, 12, 1)),
    Event("Navidad (última semana)", date(2026, 12, 17), date(2026, 12, 24)),
    Event("Liquidación de verano", date(2027, 1, 2), date(2027, 1, 15), estimated=True),
    Event("CyberDay Chile", date(2027, 6, 1), date(2027, 6, 3), estimated=True),
    Event("Día del Padre", date(2027, 6, 14), date(2027, 6, 20), estimated=True),
    Event("Cyber Monday Chile", date(2027, 10, 4), date(2027, 10, 6), estimated=True),
    Event("Black Friday", date(2027, 11, 26), date(2027, 11, 29), estimated=True),
]


def _now_cl() -> datetime:
    try:
        return datetime.now(ZoneInfo(config.TIMEZONE)).replace(tzinfo=None)
    except Exception:  # noqa: BLE001 - sin base de zonas horarias: hora local
        return datetime.now()


def active_event(now: datetime | None = None) -> Event | None:
    """`now` se interpreta como hora de Chile (sin zona); por defecto, la hora actual en Chile."""
    t = now or _now_cl()
    return next((e for e in EVENTS if e.active(t)), None)


def upcoming(now: datetime | None = None, limit: int = 4) -> list[Event]:
    d = (now or _now_cl()).date()
    return sorted((e for e in EVENTS if e.end >= d), key=lambda e: e.start)[:limit]
