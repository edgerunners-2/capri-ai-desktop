"""Her entrance: a flipbook (assets/desk/splash/1.png ... N.png, HD) that fills the whole screen, then, as her pieces
pull together in the last frames, zips down onto the spot she lands on and gets sucked into her as she rebuilds.
The frames are loaded and pre-scaled to the screen in threads (sharp, and no stall at startup), and crossfade into
each other on every tick, so it plays smoothly at any refresh rate."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QWidget

from ..config import ASSETS

FRAME = 0.033  # seconds per frame while it fills the screen (43 frames now, twice the old 23: ~30 fps, same length)
FAST = 0.025  # seconds per frame once it shrinks onto her (the sparkle ending she lands in)
SHRINK_FROM = 24  # from this frame (0-based) on it shrinks from fullscreen down to her size
BLEND = 0.4  # the last this-much of each frame crossfades into the next
FADE_IN = 0.12
FADE_OUT = 0.28  # after the last frame: it keeps shrinking into her and fades while she rebuilds
END = (530 / 640, 370 / 480)  # where the animation ends, as a fraction of the frame: her body goes here
END_H = 170 / 480  # how tall the end shape is, as a fraction of the frame; it ends up about her height


def _smooth(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


class Entrance(QWidget):
    """aim(screen, floor_y) -> the x she lands at; start() once .ready; step() each tick. .landed turns True when
    she should appear (it then shrinks into her and fades on its own), .done once it's gone."""

    def __init__(self, size: int, screen: QRect, dpr: float):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.S = size
        self.frames: list[tuple[QImage, QRectF]] = []  # each frame cropped to its content + where that sits (0..1)
        self.fw, self.fh = 640, 480  # the frames' shape (overwritten once loaded)
        self.k_full = 1.0
        self.end_full = self.end_her = QPointF()
        self.last, self.t = 0.0, 0.0
        self.started = self.landed = self.done = False
        self.ready = False
        # cover size of this screen in device pixels: frames are scaled to exactly that once, so fullscreen is 1:1
        self._cover = (screen.width() * dpr, screen.height() * dpr)
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        files = sorted((ASSETS / "desk" / "splash").glob("*.png"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0)
        with ThreadPoolExecutor(4) as pool:  # PNG decoding is most of the time, and it runs in parallel: ~3x faster
            out = [f for f in pool.map(self._load_one, files) if f is not None]
        self.frames = out
        self.done = not out
        self.ready = True

    def _load_one(self, f) -> tuple[QImage, QRectF] | None:
        img = QImage(str(f))
        if img.isNull():
            return None
        self.fw, self.fh = img.width(), img.height()
        k = max(self._cover[0] / img.width(), self._cover[1] / img.height())
        img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        img = img.scaled(round(img.width() * k), round(img.height() * k), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        box = _content(img)
        W, H = img.width(), img.height()
        return img.copy(box), QRectF(box.x() / W, box.y() / H, box.width() / W, box.height() / H)

    @property
    def n(self) -> int:
        return len(self.frames)

    def start_of(self, i: int) -> float:
        """When frame i starts."""
        return min(i, SHRINK_FROM) * FRAME + max(0, i - SHRINK_FROM) * FAST

    @property
    def length(self) -> float:
        return self.start_of(self.n)

    def aim(self, screen: QRectF, floor_y: float) -> float:
        """Covers this screen (whatever its size or shape); returns the x her feet land at: under where the
        animation ends, on the floor."""
        self.setGeometry(screen.toRect())
        w, h = screen.width(), screen.height()
        self.k_full = max(w / self.fw, h / self.fh)  # cover: no bars, no stretching
        fw, fh = self.fw * self.k_full, self.fh * self.k_full
        self.end_full = QPointF((w - fw) / 2 + END[0] * fw, (h - fh) / 2 + END[1] * fh)  # widget coords
        x = screen.left() + self.end_full.x() + 0.05 * self.S  # her center() is 0.05 S left of her feet
        x = max(screen.left() + 0.5 * self.S, min(screen.right() - 0.6 * self.S, x))
        self.end_her = QPointF(x - 0.05 * self.S - screen.left(), floor_y - 0.5 * self.S - screen.top())
        return x

    def start(self):
        self.last, self.t = time.monotonic(), 0.0
        self.started = True
        self.landed = self.done = not self.frames
        if self.frames:
            self.show()
            self.raise_()

    def step(self):
        if self.done or not self.started:
            return
        now = time.monotonic()
        self.t += min(0.05, now - self.last)  # a hitch (her brain loading at startup) pauses it instead of skipping
        self.last = now
        if self.t >= self.length:
            self.landed = True
        if self.t >= self.length + FADE_OUT:
            self.done = True
            self.hide()
            return
        self.update()

    def _frame_at(self, t: float) -> tuple[int, float]:
        """(frame index, 0..1 crossfade into the next one) at time t."""
        for i in range(self.n - 1, -1, -1):
            if t >= self.start_of(i):
                if i == self.n - 1:
                    return i, 0.0
                d = self.start_of(i + 1) - self.start_of(i)
                return i, _smooth(((t - self.start_of(i)) / d - (1 - BLEND)) / BLEND)
        return 0, 0.0

    def paintEvent(self, e):
        if not self.frames:
            return
        t = self.t
        i, mix = self._frame_at(t)
        s0, s1 = self.start_of(SHRINK_FROM), self.length
        z = _smooth((t - s0) / (s1 - s0)) if t < s1 else 1.0  # 0 fullscreen .. 1 her size, onto her
        k_end = 0.8 * self.S / (END_H * self.fh)
        k = self.k_full * (k_end / self.k_full) ** z
        alpha = _smooth(t / FADE_IN)
        if t > s1:  # landed: it keeps shrinking into her body and fades
            u = _smooth((t - s1) / FADE_OUT)
            k *= 1 - 0.55 * u
            alpha *= 1 - u
        end = self.end_full + (self.end_her - self.end_full) * z
        fw, fh = self.fw * k, self.fh * k
        x0, y0 = end.x() - END[0] * fw, end.y() - END[1] * fh
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, z > 0)  # fullscreen it's drawn 1:1, no resampling
        for j, a in ((i, 1 - mix), (i + 1, mix)):
            if a <= 0 or j >= self.n:
                continue
            img, r = self.frames[j]
            p.setOpacity(alpha * a)
            p.drawImage(QRectF(x0 + r.x() * fw, y0 + r.y() * fh, r.width() * fw, r.height() * fh), img)
        p.end()


def _content(img: QImage) -> QRect:
    """The part of the image that isn't fully transparent (whole image if it's all content)."""
    alpha = img.convertToFormat(QImage.Format_Alpha8)
    a = np.frombuffer(alpha.constBits(), np.uint8).reshape(alpha.height(), alpha.bytesPerLine())[:, :alpha.width()]
    ys, xs = np.nonzero(a.any(1))[0], np.nonzero(a.any(0))[0]
    if not len(ys):
        return img.rect()
    return QRect(int(xs[0]), int(ys[0]), int(xs[-1] - xs[0] + 1), int(ys[-1] - ys[0] + 1))
