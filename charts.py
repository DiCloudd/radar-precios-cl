"""Gráfico PNG del historial de precios (Pillow, sin dependencias pesadas) para Telegram."""
from __future__ import annotations

import io
from datetime import datetime

from PIL import Image, ImageDraw

from godzilla import _font

W, H = 900, 460
PAD_L, PAD_R, PAD_T, PAD_B = 120, 30, 70, 60
BG, GRID, TXT, MUTED = (18, 23, 37), (40, 48, 70), (232, 236, 246), (138, 150, 178)
LINE, GOOD, BAD = (129, 140, 248), (52, 211, 153), (248, 113, 113)


def clp(n: int) -> str:
    return "$" + f"{int(n):,}".replace(",", ".")


def price_chart(title: str, store: str, points: list[tuple[str, int]], current: int) -> bytes:
    """points: [(iso_ts, precio)] ordenados. Devuelve PNG en bytes."""
    now = datetime.now().isoformat(timespec="seconds")
    pts = [(datetime.fromisoformat(t), p) for t, p in points] or [(datetime.fromisoformat(now), current)]
    pts.append((datetime.fromisoformat(now), current))
    lo, hi = min(p for _, p in pts), max(p for _, p in pts)
    if hi == lo:
        lo, hi = lo * 0.95, hi * 1.05
    t0, t1 = pts[0][0].timestamp(), pts[-1][0].timestamp()
    span = (t1 - t0) or 1

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_title, f_sub, f_ax = _font(22), _font(16), _font(14)
    d.text((24, 16), title[:70], font=f_title, fill=TXT)
    d.text((24, 44), f"{store}  ·  actual {clp(current)}  ·  mín {clp(min(p for _, p in pts))}  ·  máx {clp(max(p for _, p in pts))}",
           font=f_sub, fill=MUTED)

    def xy(ts: float, price: float) -> tuple[float, float]:
        x = PAD_L + (ts - t0) / span * (W - PAD_L - PAD_R)
        y = PAD_T + (1 - (price - lo) / (hi - lo)) * (H - PAD_T - PAD_B)
        return x, y

    for i in range(5):                                   # grilla horizontal con precios
        v = lo + (hi - lo) * i / 4
        _, y = xy(t0, v)
        d.line([(PAD_L, y), (W - PAD_R, y)], fill=GRID, width=1)
        d.text((12, y - 8), clp(int(v)), font=f_ax, fill=MUTED)
    for frac in (0, 0.5, 1):                             # fechas en el eje X
        ts = t0 + span * frac
        x, _ = xy(ts, lo)
        label = datetime.fromtimestamp(ts).strftime("%d-%m %H:%M")
        d.text((min(x - 40, W - PAD_R - 90), H - PAD_B + 14), label, font=f_ax, fill=MUTED)

    line: list[tuple[float, float]] = []                 # línea escalonada (el precio rige hasta que cambia)
    for i, (t, p) in enumerate(pts):
        x, y = xy(t.timestamp(), p)
        if line:
            line.append((x, line[-1][1]))
        line.append((x, y))
    if len(line) > 1:
        poly = line + [(line[-1][0], H - PAD_B), (line[0][0], H - PAD_B)]
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(overlay).polygon(poly, fill=LINE + (40,))
        img.paste(overlay, (0, 0), overlay)
        d = ImageDraw.Draw(img)
        d.line(line, fill=LINE, width=4, joint="curve")
    color = GOOD if current <= pts[0][1] else BAD
    x, y = xy(t1, current)
    d.ellipse([x - 7, y - 7, x + 7, y + 7], fill=color, outline=TXT, width=2)

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
