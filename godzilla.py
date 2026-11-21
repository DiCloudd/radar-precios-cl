"""Genera assets/godzilla.gif: sirena + fuego + texto gigante. 100% original (Pillow), sin assets de terceros."""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSET = Path(__file__).resolve().parent / "assets" / "godzilla.gif"
W, H, FRAMES = 480, 270, 16


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _dino(d: ImageDraw.ImageDraw, x: int, y: int, s: float, color) -> None:
    """Silueta de kaiju hecha con polígonos (cuerpo, cola, cabeza, púas)."""
    pts = [(0, 60), (25, 20), (45, 0), (70, -5), (85, 5), (80, 18), (62, 22), (66, 40), (80, 52),
           (70, 58), (58, 48), (55, 75), (62, 100), (45, 100), (40, 80), (28, 100), (12, 100),
           (18, 75), (-20, 85), (-55, 80), (-20, 65)]
    d.polygon([(x + px * s, y + py * s) for px, py in pts], fill=color)
    for i in range(5):  # púas dorsales
        bx, by = x + (5 - i * 7) * s, y + (35 + i * 9) * s
        d.polygon([(bx, by), (bx - 14 * s, by - 6 * s), (bx - 2 * s, by - 16 * s)], fill=color)
    d.ellipse([x + 70 * s, y + 4 * s, x + 76 * s, y + 10 * s], fill=(255, 230, 0))  # ojo


def build(path: Path = ASSET) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(7)
    big, mid = _font(46), _font(22)
    frames = []
    for f in range(FRAMES):
        on = f % 4 < 2
        bg = (150, 0, 0) if on else (25, 0, 0)
        img = Image.new("RGB", (W, H), bg)
        d = ImageDraw.Draw(img)
        # rayos de sirena giratorios
        cx, cy = W // 2, 0
        for k in range(6):
            a = (f / FRAMES) * 2 * math.pi + k * math.pi / 3
            d.polygon([(cx, cy), (cx + 600 * math.cos(a - .15), cy + 600 * math.sin(a - .15)),
                       (cx + 600 * math.cos(a + .15), cy + 600 * math.sin(a + .15))],
                      fill=(255, 60, 0) if on else (90, 10, 0))
        # fuego
        for _ in range(70):
            x = rnd.randint(0, W)
            h = rnd.randint(20, 80)
            col = rnd.choice([(255, 90, 0), (255, 170, 0), (255, 230, 60)])
            d.polygon([(x - 12, H), (x + 12, H), (x + rnd.randint(-6, 6), H - h)], fill=col)
        _dino(d, 360 + (f % 2) * 3, 120, 1.0, (10, 10, 10))
        shake = (rnd.randint(-4, 4), rnd.randint(-3, 3))
        txt_col = (255, 255, 255) if on else (255, 220, 0)
        d.text((24 + shake[0], 40 + shake[1]), "ALERTA", font=big, fill=txt_col, stroke_width=3, stroke_fill=(0, 0, 0))
        d.text((24 + shake[0], 92 + shake[1]), "GODZILLA", font=big, fill=txt_col, stroke_width=3, stroke_fill=(0, 0, 0))
        d.text((26, 150), "PRECIO DESTRUIDO", font=mid, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))
        frames.append(img.convert("P", palette=Image.ADAPTIVE, colors=64))
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=90, loop=0, optimize=True)
    return path


def asset() -> Path:
    return ASSET if ASSET.exists() else build()


if __name__ == "__main__":
    print(build())
