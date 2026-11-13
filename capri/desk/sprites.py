"""Capri's sprite sheets: horizontal strips of frames, pre-scaled to her on-screen size.

Her art (assets/desk/skins) is drawn for Capri by xilia_world00 on Discord.
Every strip is 384 px tall; standing clips are 240 px per frame, the seated ones 384.
"""
from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

SPRITES = Path(__file__).resolve().parents[2] / "assets" / "desk" / "skins" / "capri"  # her default look

# name: (frames, fps, drawn height as a multiple of her size)
CLIPS = {
    "idle": (16, 8, 1.11),
    "blink": (4, 8, 1.11),
    "blink_glitch": (4, 8, 1.11),
    "grin": (16, 8, 1.11),
    "glitch": (10, 15, 1.11),
    "dangle": (24, 10, 1.11),
    "sleep": (16, 6, 1.11),
    "wake": (13, 15, 1.11),
    "mouth": (5, 12, 1.11),
    "perch": (16, 8, 1.0),
    "perch_blink": (4, 8, 1.0),
    "perch_blink_glitch": (4, 8, 1.0),
    "perch_glitch": (10, 15, 1.0),
}


PERCH_HIP = (0.30, 0.71)  # where she sits (her seat on the window edge), as a fraction of a seated frame


class Clip:
    def __init__(self, name: str, size: int, dpr: float = 1.0, folder: Path = SPRITES):
        self.name = name
        self.count, self.fps, ratio = CLIPS[name]
        strip = QImage(str(folder / f"{name}.png")).convertToFormat(QImage.Format_ARGB32_Premultiplied)
        if strip.isNull():
            raise FileNotFoundError(f"missing sprite strip {name}.png in {folder}")
        fw = strip.width() // self.count
        self.h = round(size * ratio)
        self.w = round(self.h * fw / strip.height())
        self.dpr = dpr
        self.frames, self.images = [], []  # images: same frames as QImages, for sampling her pixels
        for i in range(self.count):
            img = strip.copy(i * fw, 0, fw, strip.height()).scaled(
                round(self.w * dpr), round(self.h * dpr), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(dpr)  # crisp on 125% / 150% displays
            self.frames.append(pm)
            self.images.append(img)
        self.anchor = PERCH_HIP if name.startswith("perch") else _feet(strip.copy(0, 0, fw, strip.height()))
        self._white: dict[int, QPixmap] = {}

    def sample(self, i: int, ax: float, ay: float, step: float) -> list[tuple[float, float, QColor]]:
        """Her opaque pixels on a `step`-px grid, placed in screen coords with her anchor at (ax, ay)."""
        img, d = self.images[i], self.dpr
        ox, oy = ax - self.anchor[0] * self.w, ay - self.anchor[1] * self.h
        out = []
        s = max(1, round(step * d))
        for y in range(s // 2, img.height(), s):
            for x in range(s // 2, img.width(), s):
                c = img.pixelColor(x, y)
                if c.alpha() > 200:
                    out.append((ox + x / d, oy + y / d, QColor(c.red(), c.green(), c.blue())))
        return out

    def white(self, i: int) -> QPixmap:
        """Her silhouette in solid white, for the charge-up / materialise flash."""
        if i not in self._white:
            pm = QPixmap(self.frames[i].size())
            pm.setDevicePixelRatio(self.frames[i].devicePixelRatio())
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.drawPixmap(0, 0, self.frames[i])
            p.setCompositionMode(QPainter.CompositionMode_SourceIn)
            p.fillRect(pm.rect(), Qt.white)
            p.end()
            self._white[i] = pm
        return self._white[i]


class Sprites:
    def __init__(self, size: int, dpr: float = 1.0, folder: Path = SPRITES):  # folder: her skin (capri/skins.py)
        self.size = size
        self.clips = {name: Clip(name, size, dpr, folder) for name in CLIPS}
        # same canvas as idle: no foot jitter (dangle is her held pose, drawn on the same canvas, so grabbing her
        # doesn't make her jump to wherever that pose's feet are)
        for name in ("blink", "blink_glitch", "grin", "glitch", "wake", "mouth", "dangle"):
            self.clips[name].anchor = self.clips["idle"].anchor
        self.palette = _palette(folder / "idle.png")

    def __getitem__(self, name: str) -> Clip:
        return self.clips[name]

    def color(self) -> QColor:
        """A random colour she's made of (weighted by how much of her is that colour)."""
        return QColor(random.choice(self.palette))


def _feet(frame: QImage) -> tuple[float, float]:
    """Where her feet touch the ground in a standing frame: mean x of the lowest opaque rows, bottom y."""
    a = frame.convertToFormat(QImage.Format_ARGB32)
    bottom = 0
    for y in range(a.height() - 1, -1, -1):
        if any(a.pixelColor(x, y).alpha() > 128 for x in range(0, a.width(), 2)):
            bottom = y
            break
    xs = [x for y in range(max(0, bottom - 12), bottom + 1) for x in range(a.width()) if a.pixelColor(x, y).alpha() > 128]
    return (sum(xs) / len(xs) / a.width() if xs else 0.5), (bottom + 1) / a.height()


def _palette(path: Path) -> list[QColor]:
    """Opaque colours from her first idle frame, bucketed, most common first. Debris uses these."""
    img = QImage(str(path)).convertToFormat(QImage.Format_ARGB32)
    counts: dict[tuple, int] = {}
    for y in range(0, img.height(), 3):
        for x in range(0, 240, 3):
            c = img.pixelColor(x, y)
            if c.alpha() < 230:
                continue
            key = (c.red() // 24, c.green() // 24, c.blue() // 24)
            counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:10]
    out: list[QColor] = []
    for (r, g, b), n in top:
        out += [QColor(min(255, r * 24 + 12), min(255, g * 24 + 12), min(255, b * 24 + 12))] * max(1, n // 40)
    return out or [QColor("#ff1460"), QColor("#2bff9a"), QColor("#1b0b2b")]
