"""Her entrance, "signal lock": corner brackets snap together at the top right of your screen and lock on, a tilted
little screen flicks on inside them like an old CRT with her face on it, she stamps a "hey" sticker beside it, then
the screen switches off (down to a line, down to a dot) and she pours out of that dot as pixels onto your desktop."""
from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QWidget

from .. import i18n
from .bubble import mono, sans

PINK = QColor(255, 30, 110)
CYAN = QColor(120, 250, 255)
INK = QColor(12, 6, 20)
PAPER = QColor(250, 246, 240)

# the timeline, in seconds from the start (the app syncs its own steps to these)
LOCK = 0.42      # brackets locked on
ON = 0.72        # the screen is on
STAMP = 1.15     # the sticker stamps out
OFF0 = 2.05      # the screen starts switching off
DOT = 2.4        # down to a dot
BOOT = LOCK      # (names the app uses) the hum starts
FACE = 2.45      # she pours out of the dot
OUT = 2.55       # the brackets let go
OFF = 2.9        # gone

WORDS = ("hey.", "yo!", "hi :)", "sup")  # the English fallback; i18n.tlist has the same sticker words in all eight
BYE_WORDS = ("cya.", "later!", "brb", "bye :)")
HOLD = 1.3  # leaving: how long the locked brackets wait (empty) while her pixels fly up into them


def _ease_out(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def _ease_back(x: float) -> float:
    x = max(0.0, min(1.0, x))
    c1, c3 = 1.9, 2.9
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


class Portal(QWidget):
    """start() it; it runs its own show and hides itself at the end. face(t) -> QPixmap: her face at time t."""

    def __init__(self, scale: float, face):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.k = k = max(0.75, scale)
        self.face = face
        self.sw, self.sh = 150 * k, 172 * k  # the little screen
        self.setFixedSize(round(330 * k), round(250 * k))
        self.t0 = 0.0
        self.word = random.choice(i18n.tlist("portal.words", list(WORDS)))
        self.leaving = False

    def start(self, work, leaving: bool = False) -> None:
        """leaving: the outro. Same show, but the brackets hold empty for a beat while she streams up into them."""
        self.move(round(work.right() - self.width() - 8 * self.k), round(work.top() + 10 * self.k))
        self.leaving = leaving
        self.word = random.choice(i18n.tlist("portal.bye" if leaving else "portal.words",
                                             list(BYE_WORDS if leaving else WORDS)))
        self.t0 = time.monotonic()
        self.show()
        self.raise_()

    @property
    def real_t(self) -> float:
        return time.monotonic() - self.t0

    @property
    def t(self) -> float:
        """Show time: real time, except that the outro pauses at the lock while she flies in."""
        t = self.real_t
        if self.leaving and t > LOCK:
            t = max(LOCK, t - HOLD)
        return t

    @property
    def done(self) -> bool:
        return self.t >= OFF

    def _center(self) -> QPointF:
        return QPointF(self.width() - 40 * self.k - self.sw / 2, 30 * self.k + self.sh / 2)

    def screen_rect(self) -> QRectF:
        """The screen in global coords (at the dot moment it's just the dot in the middle of it)."""
        c = self._center()
        r = QRectF(c.x() - self.sw / 2, c.y() - self.sh / 2, self.sw, self.sh)
        return r.translated(self.x(), self.y())

    def step(self) -> None:
        self.update()

    # -- drawing ----------------------------------------------------------------------------------------------------
    def paintEvent(self, _):
        t, k = self.t, self.k
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        c = self._center()
        self._brackets(p, t, c)
        if ON - 0.3 < t < DOT + 0.12:
            self._screen(p, t, c)
        if STAMP < t < OFF0 + 0.2:
            self._sticker(p, t, c)
        if DOT - 0.05 < t < FACE + 0.25:  # the last dot of light, then a pop as she leaves through it
            q = (t - DOT) / (FACE + 0.25 - DOT)
            r = (3 + 10 * max(0.0, q) ** 0.5) * k
            a = max(0.0, 1 - max(0.0, q))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, round(255 * a)))
            p.drawEllipse(c, r, r)
            p.setBrush(QColor(255, 30, 110, round(90 * a)))
            p.drawEllipse(c, r * 2.4, r * 2.4)
        p.end()

    def _brackets(self, p: QPainter, t: float, c: QPointF):
        """Four corner brackets: they fly in from wide, snap onto the screen, and let go at the end."""
        k = self.k
        if t < LOCK:
            spread = 1 - _ease_back(t / LOCK)
            a = min(1.0, t / 0.15)
        elif t < OUT:
            spread = 0.0
            a = 1.0
        else:
            q = (t - OUT) / (OFF - OUT)
            spread = -0.25 * _ease_out(q)
            a = 1 - q
        if a <= 0:
            return
        hw, hh = self.sw / 2 + 12 * k, self.sh / 2 + 12 * k
        hw, hh = hw * (1 + 0.55 * spread), hh * (1 + 0.55 * spread)
        arm = 20 * k
        locked = LOCK <= t < LOCK + 0.18
        col = QColor(PAPER if locked else PINK)
        col.setAlphaF(a)
        p.setPen(QPen(col, 3 * k, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        p.setBrush(Qt.NoBrush)
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            x, y = c.x() + sx * hw, c.y() + sy * hh
            path = QPainterPath(QPointF(x, y - sy * arm))
            path.lineTo(QPointF(x, y))
            path.lineTo(QPointF(x - sx * arm, y))
            p.drawPath(path)
        if t < LOCK + 0.5:  # a tiny crosshair in the middle while it locks on
            q = min(1.0, t / LOCK)
            cc = QColor(CYAN)
            cc.setAlphaF(a * (1 - max(0.0, (t - LOCK) / 0.5)))
            p.setPen(QPen(cc, 1.6 * k))
            r = (26 - 14 * q) * k
            p.drawLine(QPointF(c.x() - r, c.y()), QPointF(c.x() + r, c.y()))
            p.drawLine(QPointF(c.x(), c.y() - r), QPointF(c.x(), c.y() + r))
        p.setFont(mono(9 * k))
        tag = QColor(PAPER)
        tag.setAlphaF(a * 0.75)
        p.setPen(tag)
        p.drawText(QPointF(c.x() - hw, c.y() + hh + 15 * k), "capri" if t < LOCK else "capri  //  locked")

    def _screen(self, p: QPainter, t: float, c: QPointF):
        """The tilted CRT: on (a line opening up), her face, off (squashed to a line, then a dot)."""
        k = self.k
        w, h = self.sw, self.sh
        if t < ON:  # switching on: a bright line opens up into the picture
            q = _ease_out((t - (ON - 0.3)) / 0.3)
            sx, sy = min(1.0, 0.15 + q * 1.6), max(0.012, (q - 0.35) / 0.65) if q > 0.35 else 0.012
        elif t < OFF0:  # a little bounce as it lands on
            q = (t - ON) / 0.35
            sx = sy = 1 + (0.09 * math.sin(q * math.pi * 2) * (1 - q) if q < 1 else 0)
        else:  # switching off: squash to a line, then the line to a dot
            q = (t - OFF0) / (DOT - OFF0)
            sy = max(0.012, 1 - _ease_out(q / 0.55))
            sx = 1.0 if q < 0.55 else max(0.02, 1 - _ease_out((q - 0.55) / 0.45))
        if sx <= 0 or sy <= 0:
            return
        p.save()
        p.translate(c)
        p.setTransform(QTransform().shear(-0.12, 0), True)  # leaning, like it was taped up in a hurry
        p.scale(sx, sy)
        rect = QRectF(-w / 2, -h / 2, w, h)
        bright = sy < 0.2  # nearly a line: just light
        if bright:
            p.fillRect(rect, QColor(255, 255, 255))
            p.restore()
            return
        # misprinted frame: a cyan copy a few px off, then the pink one
        jit = (2.5 + (4 if random.random() < 0.06 else 0)) * k
        p.setPen(QPen(CYAN, 3 * k))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect.translated(-jit, jit * 0.6))
        p.setPen(Qt.NoPen)
        p.setBrush(INK)
        p.drawRect(rect)
        p.save()
        p.setClipRect(rect.adjusted(5 * k, 5 * k, -5 * k, -5 * k))
        self._halftone(p, rect)
        face = self.face(t)
        if face is not None:
            whole = QRectF(0, 0, face.width(), face.height())
            side = h * 1.05
            fr = QRectF(-side / 2 + 4 * k, -side * 0.72, side, side)  # her eyes in the middle, not her hair
            settle = max(0.0, 1 - (t - ON) / 0.45)
            off = (settle * 9 + (3 if random.random() < 0.05 else 0)) * k
            if off > 0.5:  # colour fringes that settle as the picture tunes in
                p.setOpacity(0.55)
                p.drawPixmap(fr.translated(-off, 0), face, whole)
                p.setCompositionMode(QPainter.CompositionMode_Plus)
                p.drawPixmap(fr.translated(off, 0), face, whole)
                p.setCompositionMode(QPainter.CompositionMode_SourceOver)
                p.setOpacity(1.0)
            p.drawPixmap(fr, face, whole)
        # scanlines and one slow bright band
        p.setPen(QPen(QColor(0, 0, 0, 70), 1))
        y = rect.top()
        while y < rect.bottom():
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += 3 * k
        band = rect.top() + ((t * 0.9) % 1.0) * h
        p.fillRect(QRectF(rect.left(), band, w, 10 * k), QColor(255, 255, 255, 16))
        if ON <= t < ON + 0.06:  # one bright frame as it pops on
            p.fillRect(rect, QColor(255, 255, 255, 200))
        if t > OFF0 - 0.1:  # it whitens as it shuts off
            p.fillRect(rect, QColor(255, 255, 255, round(255 * min(1.0, (t - OFF0 + 0.1) / 0.25))))
        p.restore()
        p.setPen(QPen(PINK, 3 * k))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect)
        p.restore()

    def _halftone(self, p: QPainter, rect: QRectF):
        """Pink halftone dots, heavier toward the bottom corner, behind her."""
        k = self.k
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 30, 110, 70))
        g = 9 * k
        y = rect.top()
        row = 0
        while y < rect.bottom():
            x = rect.left() + (g / 2 if row % 2 else 0)
            while x < rect.right():
                d = ((x - rect.left()) / rect.width() * 0.4 + (y - rect.top()) / rect.height() * 0.6)
                r = 0.4 * k + 2.6 * k * d
                p.drawEllipse(QPointF(x, y), r, r)
                x += g
            y += g
            row += 1

    def _sticker(self, p: QPainter, t: float, c: QPointF):
        """A paper sticker with her word on it, stamped out to the left of the screen with a couple of motion slashes."""
        k = self.k
        q = (t - STAMP) / 0.22
        s = 1.6 - 0.6 * _ease_back(q) if q < 1 else 1.0
        a = min(1.0, q * 3)
        if t > OFF0:  # peeled away with the screen
            a *= max(0.0, 1 - (t - OFF0) / 0.2)
        if a <= 0:
            return
        p.save()
        p.setOpacity(a)
        p.translate(c.x() - self.sw / 2 - 18 * k, c.y() + self.sh * 0.18)
        p.rotate(-7)
        p.scale(s, s)
        f = sans(26 * k, QFont.Black)
        p.setFont(f)
        tw = p.fontMetrics().horizontalAdvance(self.word)
        # her words get longer in some languages ("was geht"): the sticker shrinks so it never runs off the window
        fit = min(1.0, (c.x() - self.sw / 2 - 24 * k) / (tw + 24 * k))
        p.scale(s * fit, s * fit)
        box = QRectF(-tw - 22 * k, -20 * k, tw + 24 * k, 38 * k)
        p.setPen(Qt.NoPen)
        p.setBrush(PINK)
        p.drawRect(box.translated(4 * k, 4 * k))
        p.setBrush(PAPER)
        p.drawRect(box)
        p.setPen(INK)
        p.drawText(box, Qt.AlignCenter, self.word)
        if q < 2.2:  # motion slashes behind it, only while it lands
            p.setOpacity(a * max(0.0, 1 - q / 2.2))
            p.setPen(QPen(PAPER, 2.4 * k, Qt.SolidLine, Qt.RoundCap))
            for i, ln in enumerate((34, 22, 28)):
                y = box.top() + (8 + 11 * i) * k
                p.drawLine(QPointF(box.left() - 8 * k, y), QPointF(box.left() - (8 + ln) * k, y + 6 * k))
        p.restore()
