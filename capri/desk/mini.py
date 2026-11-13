"""Her mini form (her naps, "Go mini" and "Always mini" from the menu). With the mini art (assets/desk/mini, by
xilia_world00) she blinks, talks with her mouth, sleeps with her eyes shut (and mumbles back if you talk to her),
gets annoyed when you poke her, looks toward your cursor, and lies down on the taskbar or on top of your windows.
Without it she's the older nap sprite. Drag her anywhere; click to talk; double-click (or the menu) for full size.
"""
from __future__ import annotations

import math
import random
import re
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

MINT = QColor(52, 245, 160)
MINT_HI = QColor(150, 255, 205)
VIOLET = QColor(110, 50, 255)
PINK = QColor(255, 30, 110)
INK = QColor(22, 10, 40)
CYAN = QColor(120, 250, 255)
WHITE = QColor(255, 255, 255)

SIDE = 96  # logical px at her default size (240)


def paint_chip(p: QPainter, side: float, t: float, glitch: bool = False, zs: bool = True) -> None:
    """Her mini form when the mini art is missing: her idle mini look if there is one, else just the z's (the old
    code-drawn nap sprite and nap.png are gone)."""
    art = mini_art()
    img = art.get("idle") if art.ok else None
    if img is not None:
        return _paint_art(p, img, side, t, glitch, zs)
    if zs:
        _zs(p, 0.6 * side, 0.4 * side, side / 100, t)


def _zs(p: QPainter, x: float, y: float, u: float, t: float) -> None:
    """z's drifting up and fading."""
    f = QFont(["Consolas", "DejaVu Sans Mono", "Noto Sans Mono"])
    f.setBold(True)
    for i in range(3):
        k = ((t * 0.45) + i / 3) % 1.0
        f.setPixelSize(max(6, round((8 + 7 * k) * u)))
        p.setFont(f)
        c = QColor(CYAN)
        c.setAlphaF(math.sin(math.pi * k) * 0.9)
        p.setPen(c)
        p.drawText(QPointF(x + 10 * k * u + math.sin(k * 6) * 2 * u, y - 26 * k * u), "z")


def _paint_art(p: QPainter, art: QImage, side: float, t: float, glitch: bool, zs: bool, lay: bool = False,
               flip: bool = False) -> None:
    u = side / 100
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    bob = 0.0 if lay else math.sin(t * 1.9) * 2.5 * u  # lying on something: no hovering
    breathe = math.sin(t * (1.2 if lay else 1.9) + 0.6)
    cx, floor = 50 * u, (95.5 if lay else 93) * u
    size = (96 if lay else 84) * u
    aspect = art.height() / max(1, art.width())  # keep the art's own shape (the nap screen is wide)
    w, h = size * (1 - 0.025 * breathe), size * aspect * (1 + 0.03 * breathe)  # breathing squish
    rect = QRectF(cx - w / 2, floor - 3 * u - h + bob, w, h)
    if flip:  # facing the other way (lying on the left end of a window)
        p.save()
        p.translate(2 * cx, 0)
        p.scale(-1, 1)
    if glitch:  # misprint: shifted cyan / pink copies for a frame or two
        for dx, a in ((-3 * u, 0.45), (3 * u, 0.45)):
            p.setOpacity(a)
            p.drawImage(rect.translated(dx, 0), art)
        p.setOpacity(1.0)
    p.drawImage(rect, art)
    if glitch:
        for _ in range(2):
            y = random.uniform(rect.top(), rect.bottom())
            p.fillRect(QRectF(rect.left() + random.uniform(-6, 10) * u, y, random.uniform(20, 50) * u, random.uniform(1, 2.5) * u),
                       QColor(120, 250, 255, 170) if random.random() < 0.5 else QColor(255, 30, 110, 170))
    if flip:
        p.restore()
    if zs:
        _zs(p, rect.right() - 6 * u, rect.top() + 2 * u, u, t)


MINI_DIR = Path(__file__).resolve().parents[2] / "assets" / "desk" / "mini"
# her mini looks, each one PNG in assets/desk/mini. A missing one borrows the next look in its row.
LOOKS = {
    "idle": (),
    "blink": ("sleep", "idle"),
    "talk": ("idle",),
    "sleep": ("blink", "idle"),
    "sleep_talk": ("talk", "sleep"),
    "annoyed": ("idle",),
    "annoyed_talk": ("talk", "annoyed"),
    "annoyed_blink": ("blink", "annoyed"),
    "look_left": ("idle",),
    "look_right": ("idle",),
    "lay": ("sleep", "idle"),
    "lay_awake": ("lay", "idle"),
    "lay_talk": ("talk",),
    "talk2": ("talk",),  # a second talking frame: her mouth flaps talk -> talk2 -> shut
    "lay_talk2": ("lay_talk",),
    "glitch": (),  # her glitch frame: hops, shrinking, and "loading" while her brain thinks (none = drawn glitch)
}


class MiniArt:
    """The mini art, loaded once: state -> QImage. ok is False when the folder has no art (old nap sprite then)."""

    def __init__(self, folder: Path = MINI_DIR, only: Path | None = None):
        self.images: dict[str, QImage] = {}
        self.single = only is not None  # one still picture (the watcher's eyes): every look is that picture
        if only is not None:
            img = QImage(str(only))
            if not img.isNull():
                self.images["idle"] = img
        else:
            for name in LOOKS:
                img = QImage(str(folder / f"{name}.png"))
                if not img.isNull():
                    self.images[name] = img
        self.ok = "idle" in self.images

    def get(self, state: str) -> QImage | None:
        seen = set()
        todo = [state]
        while todo:
            s = todo.pop(0)
            if s in seen:
                continue
            seen.add(s)
            if s in self.images:
                return self.images[s]
            todo += list(LOOKS.get(s, ())) + ["idle"]
        return None


_arts: dict[str, MiniArt] = {}
_use = [""]  # the skin's own mini picture, "" = Capri's mini art


def use(picture: str = "") -> None:
    """Which mini art is live: Capri's (""), or a skin's own picture in assets/desk/mini (the watcher's eyes)."""
    _use[0] = picture


def mini_art() -> MiniArt:
    key = _use[0]
    if key not in _arts:
        art = MiniArt(only=MINI_DIR / key) if key else MiniArt()
        _arts[key] = art if art.ok or not key else mini_art_default()  # a missing picture: Capri's art
    return _arts[key]


def mini_art_default() -> MiniArt:
    if "" not in _arts:
        _arts[""] = MiniArt()
    return _arts[""]


ANNOYED = re.compile(r"\b(?:ugh+|tch|hmph|seriously|rude|annoying|stop (?:it|that|poking)|leave me alone|cut it out|"
                     r"bruh|whatever|go away|knock it off|en serio|para ya|déjame|qué pesado|sério|para com isso|"
                     r"sérieux|arrête|laisse-moi|ça suffit|серьёзно|отстань|хватит|бесишь|sul serio|smettila|basta|"
                     r"ernsthaft|lass das|hör auf|nervig|nervst|serio\?|przestań|daj mi spokój|wkurzasz)\b", re.I)


def sounds_annoyed(text: str) -> bool:
    """Her line sounds fed up: her mini face says so while she says it."""
    return bool(ANNOYED.search(text or ""))


def chip_image(side: float, dpr: float) -> QImage:
    img = QImage(round(side * dpr), round(side * dpr), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    img.setDevicePixelRatio(dpr)
    p = QPainter(img)
    art = mini_art()
    if art.ok:
        _paint_art(p, art.get("idle"), side, 0.0, False, False)
    else:
        paint_chip(p, side, 0.0, zs=False)
    p.end()
    return img


def sample_chip(side: float, dpr: float, fx: float, fy: float, step: float):
    """The chip's opaque pixels on a grid, placed with its floor point (bottom middle) at (fx, fy)."""
    img = chip_image(side, dpr)
    s = max(1, round(step * dpr))
    ox, oy = fx - side * 0.5, fy - side * 0.92
    out = []
    for y in range(s // 2, img.height(), s):
        for x in range(s // 2, img.width(), s):
            c = img.pixelColor(x, y)
            if c.alpha() > 200:
                out.append((ox + x / dpr, oy + y / dpr, QColor(c.red(), c.green(), c.blue())))
    return out


class Chip(QWidget):
    """Her mini form on screen. Its floor point (bottom middle) is where it sits."""

    woke = Signal()  # double-click (or a click while she naps): back to full size
    clicked = Signal()  # a single click while she's awake and mini: talk to her
    moved = Signal()
    menu = Signal()  # right-clicked: her menu opens in the door beside it

    def __init__(self, scale: float):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.t0 = time.monotonic()
        self.glitch_until = 0.0
        self.press = None
        self.dragged = False
        self.zs = True  # asleep: eyes shut and sleepy z's (off in mini mode, where she's awake, just small)
        self.talking = False  # her mouth moves (she talks as mini, awake or half asleep)
        self.annoyed_until = 0.0  # her fed-up face, until then
        self.laying = False  # lying on the taskbar or on a window's top edge (no hovering)
        self.flip = False
        self.thinking = False  # her brain is working on a reply: she glitches now and then, like she's loading
        self.look = 0  # -1 = looking left, 1 = right, 0 = at you (follows your cursor)
        self.blink_at = time.monotonic() + random.uniform(2, 5)
        self.blink_until = 0.0
        self.pokes: list[float] = []
        self.on_poke = None  # set by Capri: called when you poke her a few times quickly
        self.art = mini_art()
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        self._click = QTimer(self)
        self._click.setSingleShot(True)
        self._click.setInterval(QApplication.doubleClickInterval() + 20)
        self._click.timeout.connect(self.clicked.emit)
        self.set_scale(scale)

    def set_scale(self, scale: float):
        """Her size changed: the mini form follows (she was stuck at the size she started at)."""
        fp = self.floor_point() if self.isVisible() else None
        self.side = SIDE * scale
        self.setFixedSize(round(self.side), round(self.side))
        if fp is not None:
            self.put(fp.x(), fp.y())
        self.update()

    def floor_point(self) -> QPointF:
        return QPointF(self.x() + self.side * 0.5, self.y() + self.side * 0.92)

    def put(self, fx: float, fy: float) -> None:
        self.move(round(fx - self.side * 0.5), round(fy - self.side * 0.92))

    def annoy(self, seconds: float = 2.5):
        self.annoyed_until = max(self.annoyed_until, time.monotonic() + seconds)

    def state(self, now: float | None = None) -> str:
        """Which mini look to show right now."""
        now = time.monotonic() if now is None else now
        if "glitch" in self.art.images and (now < self.glitch_until or
                                            (self.thinking and not self.talking and int(now * 5) % 4 == 0)):
            return "glitch"
        step = int(now * 7) % 3
        mouth_open = self.talking and step != 2  # flaps open/shut while she talks
        two = step == 1  # the middle beat uses the second talking frame, when there is one
        if self.zs:  # napping: eyes stay shut, she mumbles in her sleep
            if self.laying:
                return "lay_talk" if mouth_open else "lay"
            return "sleep_talk" if mouth_open else "sleep"
        blinking = now < self.blink_until
        if now < self.annoyed_until:
            if mouth_open:
                return "annoyed_talk"
            return "annoyed_blink" if blinking else "annoyed"
        if self.laying:
            if mouth_open:
                return "lay_talk2" if two and "lay_talk2" in self.art.images else "lay_talk"
            return "lay" if blinking else "lay_awake"
        if mouth_open:
            return "talk2" if two and "talk2" in self.art.images else "talk"
        if blinking:
            return "blink"
        if self.look:
            return "look_left" if self.look < 0 else "look_right"
        return "idle"

    def tick(self):
        now = time.monotonic()
        if now > self.glitch_until + 3 and random.random() < 0.004:
            self.glitch_until = now + 0.12
        if now > self.blink_at:  # a blink every few seconds (two quick ones now and then)
            self.blink_until = now + 0.14
            self.blink_at = now + (0.3 if random.random() < 0.15 else random.uniform(2.5, 6.0))
        if not self.talking and self.press is None:  # eyes follow your cursor left and right
            from PySide6.QtGui import QCursor

            dx = QCursor.pos().x() - (self.x() + self.side / 2)
            self.look = 0 if abs(dx) < self.side * 1.2 else (-1 if dx < 0 else 1)
        self.update()

    def face(self) -> QImage | None:
        """Her mini face right now, for the chat box portrait (None = no art: the box keeps her last face)."""
        if not self.art.ok:
            return None
        return self.art.get(self.state())

    def paintEvent(self, _):
        p = QPainter(self)
        now = time.monotonic()
        glitch = now < self.glitch_until
        if self.art.ok:
            st = self.state(now)
            _paint_art(p, self.art.get(st), self.side, now - self.t0, glitch and st != "glitch",
                       self.zs and not self.talking and not self.art.single, lay=st.startswith("lay") or (st == "glitch" and self.laying),
                       flip=self.flip)
        else:
            paint_chip(p, self.side, now - self.t0, glitch=glitch, zs=self.zs and not self.talking)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.press = e.globalPosition() - QPointF(self.pos())
            self.dragged = False

    def mouseMoveEvent(self, e):
        if self.press is not None:
            to = e.globalPosition() - self.press
            if (to - QPointF(self.pos())).manhattanLength() > 3:
                self.dragged = True
            self.move(round(to.x()), round(to.y()))

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self.press is None:
            return
        self.press = None
        if self.dragged:
            self.moved.emit()
            return
        now = time.monotonic()
        self.pokes = [t for t in self.pokes if now - t < 2.5] + [now]
        if len(self.pokes) >= 3:  # poke poke poke: she's had enough
            self.pokes = []
            self._click.stop()
            self.annoy(3.0)
            if self.on_poke:
                self.on_poke()
            return
        self._click.start()  # a click, unless a second one makes it a double-click

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._click.stop()
            self.pokes = []
            self.woke.emit()

    def contextMenuEvent(self, e):
        self.menu.emit()
