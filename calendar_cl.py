"""Eventos comerciales chilenos: durante estas ventanas el monitor revisa EVENT_SPEEDUP veces más rápido.

Las fechas se anuncian cada año (CCS / tiendas). Edita la lista cuando se confirmen;
las marcadas estimated=True son aproximaciones según años anteriores.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Event:
    name: str
    start: date
    end: date
    estimated: bool = False

    def active(self, d: date) -> bool:
        return self.start <= d <= self.end


EVENTS: list[Event] = [
    Event("Black Friday", date(2026, 11, 27), date(2026, 11, 30)),
    Event("Cyber Monday (post Black Friday)", date(2026, 11, 30), date(2026, 12, 1)),
    Event("Navidad (última semana)", date(2026, 12, 17), date(2026, 12, 24)),
    Event("Liquidación de verano", date(2027, 1, 2), date(2027, 1, 15), estimated=True),
    Event("CyberDay Chile", date(2027, 6, 1), date(2027, 6, 3), estimated=True),
    Event("Día del Padre", date(2027, 6, 14), date(2027, 6, 20), estimated=True),
    Event("Cyber Monday Chile", date(2027, 10, 4), date(2027, 10, 6), estimated=True),
    Event("Black Friday", date(2027, 11, 26), date(2027, 11, 29), estimated=True),
]


def active_event(now: datetime | None = None) -> Event | None:
    d = (now or datetime.now()).date()
    return next((e for e in EVENTS if e.active(d)), None)


def upcoming(now: datetime | None = None, limit: int = 4) -> list[Event]:
    d = (now or datetime.now()).date()
    return sorted((e for e in EVENTS if e.end >= d), key=lambda e: e.start)[:limit]
