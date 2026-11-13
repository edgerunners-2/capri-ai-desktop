"""Capri as a desktop creature: she drops in from the top of the screen, stands on the taskbar, sits on
windows and icons, gets picked up and thrown, and every so often turns into a bolt of light and zaps
somewhere new.

Positions are her *anchor* in global logical pixels: the point between her feet when she stands, her
hip when she sits. Her window is placed so that point lands on a fixed spot of its canvas.
"""
from __future__ import annotations

import math
import random
import sys
import time

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from .. import i18n, skins
from . import ledges
from .bubble import Bubble
from . import sfx as sfx_mod
from .fx import CYAN, PINK, WHITE, Fx, FxLayer
from .doormenu import INSIDE, DoorMenu
from . import mini as mini_mod
from .mini import Chip, sample_chip, sounds_annoyed
from . import splash
from .portal import FACE as SCREEN_FACE, Portal
from .sprites import Sprites

# chat box portrait: crop size, then where her face sits relative to head() standing / sitting (units of her size)
FACE = (0.34, (0.03, 0.32), (0.08, 0.35))
STREAM_STEP = 6.5  # px between her pixels when she streams (bigger = fewer pixels = smoother)
# her canned lines. These English lists are the fallback and the shape her translations must keep: capri/i18n.py
# carries the same lists in all eight languages (i18n.tlist), picked in her interface language.
BYES = ["Delta-ing out. Don't flatline without me, choom.", "Jacking out. Keep the rig warm for me.",
        "Logging off. Try not to be a gonk while I'm gone.", "Powering down. Miss me a normal amount."]
GREETING = "Ow. Okay. Whose desktop is this? ...Nice. I'm staying."
GREETINGS = ["Hi there! ...Ooh, nice desktop. I'm staying.", "Breach complete. Hiii! I live here now.",
             "Hey hey! Whose desktop is this? ...Mine now.", "Ta-da! Missed me? Don't answer that.",
             "Hi there. Just call me Capri!"]
PEEKS = ["I see u.", "Whatcha doing?", "Don't mind me.", "Peekaboo.", "...", "Hi.", "Just checking on u.", None, None]
POP_OUTS = ["Boo!", "Ha. Got u.", "Okay okay, I'm out.", "Caught me."]
ARRIVALS = ["Comfy.", "This window's mine now.", "Don't mind me, choom.", "Better view from up here.",
            "Zap. Miss me?", "What're we working on?", None, None, None]
MINI_POKES = ["Hey! Quit poking me.", "Poke me one more time, choom. I dare you.", "Ow. Rude.", "I'm small, not a button."]
DEMO_REPLIES = ["Heard you loud and clear, choom.", "Ha. Say that again, I dare you.",
                "Hold on, I'm still settling into your desktop.", "Mm-hm. Noted. Filed under 'later'.",
                "You're lucky I like you."]


class LayArt:
    """Her lying-down pose (full size, like sitting on a window): lay_awake / lay (eyes shut) / lay_talk / lay_talk2
    in her skin's folder, drawn by xilia_world00. Scaled so the drawn part is about as long as she is tall."""

    LOOKS = ("lay_awake", "lay", "lay_talk", "lay_talk2")

    def __init__(self, folder, size: int, dpr: float):
        self.pm: dict[str, QPixmap] = {}
        self.img: dict = {}
        self.w = self.h = 0.0
        self.box = None  # the drawn part (x, y, w, h) in logical px of the scaled image
        from PySide6.QtGui import QImage

        raw = {n: QImage(str(folder / f"{n}.png")) for n in self.LOOKS}
        raw = {n: i for n, i in raw.items() if not i.isNull()}
        if not raw:
            return
        ref = raw.get("lay_awake") or next(iter(raw.values()))
        bx, by, bw, bh = _alpha_box(ref)
        bh = max(1, _rest_line(ref) - by)  # her body's underside, not the lowest hair spike: that's what rests on the surface
        k = 1.15 * size / max(1, bw)  # her lying length ~ her standing height
        self.w, self.h = ref.width() * k, ref.height() * k
        self.box = (bx * k, by * k, bw * k, bh * k)
        for n, img in raw.items():
            scaled = img.scaled(round(self.w * dpr), round(self.h * dpr), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            pm = QPixmap.fromImage(scaled)
            pm.setDevicePixelRatio(dpr)
            self.pm[n] = pm
            self.img[n] = scaled
        self.dpr = dpr

    @property
    def ok(self) -> bool:
        return bool(self.pm)

    def get(self, name: str) -> QPixmap:
        for n in (name, {"lay_talk2": "lay_talk", "lay_talk": "lay_awake", "lay": "lay_awake"}.get(name, ""), "lay_awake"):
            if n in self.pm:
                return self.pm[n]
        return next(iter(self.pm.values()))

    def top_left(self, ax: float, ay: float) -> QPointF:
        """Where the image goes so the drawn part's bottom middle sits on (ax, ay)."""
        bx, by, bw, bh = self.box
        return QPointF(ax - bx - bw / 2, ay - by - bh)

    def sample(self, name: str, ax: float, ay: float, step: float):
        """Her opaque pixels while lying, in screen coords (for streaming out of the pose)."""
        img = self.img.get(name) or next(iter(self.img.values()))
        tl = self.top_left(ax, ay)
        d = self.dpr
        s = max(1, round(step * d))
        out = []
        for y in range(s // 2, img.height(), s):
            for x in range(s // 2, img.width(), s):
                c = img.pixelColor(x, y)
                if c.alpha() > 200:
                    out.append((tl.x() + x / d, tl.y() + y / d, QColor(c.red(), c.green(), c.blue())))
        return out


def _rest_line(img) -> int:
    """Where her lying body rests: the y most drawn columns end at (their median bottom), so a few low hair spikes or
    a dangling hand don't lift her off the taskbar."""
    from PySide6.QtGui import QImage

    a = img.convertToFormat(QImage.Format_ARGB32)
    W, H = a.width(), a.height()
    step = max(1, min(W, H) // 200)
    ends = []
    for x in range(0, W, step):
        for y in range(H - 1, -1, -step):
            if a.pixelColor(x, y).alpha() > 40:
                ends.append(y)
                break
    if not ends:
        return H
    ends.sort()
    return ends[len(ends) // 2] + step


def _alpha_box(img) -> tuple[int, int, int, int]:
    """The drawn (non-transparent) part of an image: x, y, w, h."""
    from PySide6.QtGui import QImage

    a = img.convertToFormat(QImage.Format_ARGB32)
    W, H = a.width(), a.height()
    step = max(1, min(W, H) // 200)
    xs, ys = [], []
    for y in range(0, H, step):
        for x in range(0, W, step):
            if a.pixelColor(x, y).alpha() > 40:
                xs.append(x)
                ys.append(y)
    if not xs:
        return 0, 0, W, H
    return min(xs), min(ys), max(xs) - min(xs) + step, max(ys) - min(ys) + step


def _dot(x, y, floor, k):
    from .fx import P

    return P(x, y, 0.0, 0.0, 1.0, max(3, round(6 * k)), WHITE, "pixel", floor, 2200 * k, 0.6)


def ease_in_out(t):
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


class Body(QWidget):
    """Her sprite window. Fully transparent pixels don't take clicks, so only she is grabbable."""

    def __init__(self, ctl: "Capri", side: int):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.ctl = ctl
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(side, side)
        self.setMouseTracking(False)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        self.ctl.draw(p)
        p.end()

    def mousePressEvent(self, e):
        self.ctl.press(e)

    def mouseMoveEvent(self, e):
        self.ctl.drag_move(e)

    def mouseReleaseEvent(self, e):
        self.ctl.release(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.ctl.teleport()

    def contextMenuEvent(self, e):
        self.ctl.open_menu()


class Capri:
    def __init__(self, size: int = 240, roam: tuple[float, float] = (18, 40), intro: bool = True, start: bool = True,
                 skin: str = skins.DEFAULT):
        self.app = QApplication.instance()
        scr = QGuiApplication.primaryScreen()
        self.S = size
        self.k = size / 240  # effect scale
        self.dpr = scr.devicePixelRatio()
        self.skin = skins.get(skin)
        mini_mod.use(self.skin.mini)  # before her mini form is made: the watcher's mini is her eyes
        self.sp = Sprites(size, self.dpr, self.skin.folder)
        self.lay = LayArt(self.skin.folder, size, self.dpr)
        self.lay_until = self.lay_doze = self.lay_blink = 0.0
        self.sfx = sfx_mod.Mixer()
        self.fx = Fx(self.k)
        self.layers = [FxLayer(self.fx, s) for s in QGuiApplication.screens()]
        side = round(1.5 * size)
        self.A = QPointF(0.75 * size, 1.22 * size)  # canvas spot that sits on her anchor
        self.body = Body(self, side)
        self.bubble = Bubble(self.k)
        self.bubble.sent.connect(self.heard)
        self.bubble.typed.connect(self._typed)
        self.bubble.nap.connect(self.toggle_mini)
        self.bubble.portrait = self._portrait  # the chat box shows her live face, like a video call
        self.bubble.mini_rect = lambda: QRectF(self.chip.geometry()) if self.mode == "chip" else None  # mini: side box
        self._faces: dict = {}
        self.chip = Chip(self.k)
        self.chip.woke.connect(lambda: self.wake_chip())
        self.chip.clicked.connect(self._chip_click)
        self.chip.on_poke = self._chip_poked
        self.chip.moved.connect(self._chip_moved)
        self.chip.menu.connect(self.open_menu)
        self.mini = False  # True = she went mini on purpose (stays small, no snoring, no waking up by herself)
        self.lay_ok = True  # her mini form lies down on the taskbar / window tops (the app sets it from settings)
        self.chip_seat: ledges.Ledge | None = None  # the taskbar or window her mini form lies on (None = floating)
        self.mini_hop_at = 0.0
        self._seat_check = 0.0
        self.nap_from, self.nap_len, self.snore_at = 0.0, 60.0, 0.0
        self.on_full = None  # set by the app: she left mini mode (Back to full size / double-click)
        self.chip_home = None  # where the chip was before her menu pulled it beside the door
        self.door = DoorMenu(self.k, self.sfx)
        self.chip_pos = None  # where the chip naps; None = bottom-left corner
        self.blips = False  # the app turns this on when she "talks" in blips instead of a voice
        self.blip_n = 0
        self.on_user = None  # set by the app: what happens with a line typed to her (the brain)
        self.voice = None  # set by the app: voice(text, on_start(seconds), on_end) speaks a line out loud
        self.speaking = False
        self.on_settled = None  # called once the next time she comes to rest (the video script waits on it)
        self.quit_fn = self.app.quit  # the app swaps in one that also stops the voice server
        self.talk_open = False  # a streamed reply is still coming in
        self.talk_timer = None
        self.beat = None  # set by the app: () -> (pulse 0..1, music playing) from the ear on your speakers
        self.vibe = 0.0

        self.mode = "hidden"  # intro | stand | perch | held | fall | charge | travel | appear | sleep
        self.x, self.y = 0.0, 0.0
        self.vx = self.vy = 0.0
        self.seat: ledges.Ledge | None = None
        self.clip, self.clip_t = "idle", 0.0
        self.cycle = -1
        self.blink = None
        self.mouth = -1
        self.frame, self.shown = 0, "idle"
        self.sx = self.sy = 1.0
        self.angle = 0.0
        self.gaze = 0.0  # the watcher's lean toward your cursor, degrees
        self.white = 0.0
        self.squash_t = -1.0
        self.t0 = time.monotonic()
        self.now = 0.0
        self.st = 0.0  # time the current mode started
        self.ledge_list: list[ledges.Ledge] = []
        self.ledges_at = -9.0
        self.lost_since = -1.0
        self.roam = roam
        self.roaming = True
        self.next_roam = 0.0
        self.last_touch = 0.0
        self.press_at = None
        self.grab = QPointF()
        self.hist: list[tuple[float, float, float]] = []
        self.intro_only_floor = False
        self.portal = None  # the transmission card she leaves through (made when she quits)
        self.entrance = None  # her intro flipbook (splash.py); its frames start loading right away when she has an intro
        self.told_zoomed = False
        self.greeting = None  # set by the app: () -> her hello line (a recorded one when her voice is on)
        self._screen_faces: dict = {}
        self.target = None
        self.say_after = None
        self.resizing = False

        self._build_menu()
        self._build_tray()
        self.timer = QTimer()
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(15)
        self.last = time.monotonic()
        if sys.platform == "win32":
            self.top_timer = QTimer()
            self.top_timer.timeout.connect(self._topmost)
            self.top_timer.start(1500)
        if intro:
            self._new_entrance()
            QTimer.singleShot(400, self.drop_in)
        elif start:
            g = ledges.work_area(scr)
            self._land_floor(g.right() - 0.3 * g.width(), g.bottom() + 1, quiet=True)

    def set_size(self, size: int):
        """A new height for her, live: sprites rebuilt at that size, her window resized, then she drops onto
        whatever is under her."""
        if size == self.S or self.resizing or self.mode in ("hidden", "intro", "stream", "to_chip", "bye", "charge",
                                                            "appear"):
            return False
        self.resizing = True
        try:
            if self._peeking():
                self.pop_out()
            self.S = size
            self.k = size / 240
            self.sp = Sprites(size, self.dpr, self.skin.folder)
            self.lay = LayArt(self.skin.folder, size, self.dpr)
            self.A = QPointF(0.75 * size, 1.22 * size)
            side = round(1.5 * size)
            self.body.setFixedSize(side, side)
            self.fx.s = self.k  # her effects scale with her
            self._faces = {}
            self.bubble.set_scale(self.k)  # the chat box follows her size right away (it used to wait for a restart)
            self.door.set_scale(self.k)  # her menu door too
            self.chip.set_scale(min(1.3, max(0.8, self.k)))  # her mini form follows a little, never huge or tiny
            if self.mode == "chip":  # small right now: she comes back at the new size
                self.chip.put(*self.chip_spot())
                return True
            self.seat = None
            self.ledges_at = -9
            self.fall_from_here(0, -120 * self.k)
            return True
        finally:
            self.resizing = False

    @property
    def can_talk(self) -> bool:
        """False in a skin that only watches: no bubble, no chat box, no voice, nothing heard."""
        return self.skin.can_talk

    def set_skin(self, skin_id: str):
        """A new look, live, with a little glitch."""
        self.skin = skins.get(skin_id)
        self.sp = Sprites(self.S, self.dpr, self.skin.folder)
        self.lay = LayArt(self.skin.folder, self.S, self.dpr)
        mini_mod.use(self.skin.mini)  # her mini form changes with the skin too
        self.chip.art = mini_mod.mini_art()
        self.chip.talking = self.chip.thinking = False
        if self.mode == "chip":
            self.chip.glitch_until = time.monotonic() + 0.25
            self.chip.update()
        if self.mode == "lay" and not self.lay.ok:
            self._end_lay()
        self._faces, self._screen_faces = {}, {}
        self.talk_action.setVisible(self.can_talk)
        if not self.can_talk:
            self.speaking = self.talk_open = False
            self.bubble.close_bubble()
        if self.mode in ("stand", "perch", "sleep"):
            self.play("perch_glitch" if self.clip.startswith("perch") else "glitch")
            self.sfx.play("pop")

    # -- small helpers ------------------------------------------------------------
    def skip(self):
        return {int(w.winId()) for w in [self.body, self.bubble, self.chip] + self.layers}

    def refresh_ledges(self, force=False):
        if force or self.now - self.ledges_at > 0.3:
            self.ledges_at = self.now
            try:
                self.ledge_list = ledges.find(self.skip(), True, self.now)
            except Exception as e:  # never let a weird window kill her
                print("ledges:", e)
                self.ledge_list = ledges.floors()

    def screen_rect(self, x, y) -> QRect:
        scr = QGuiApplication.screenAt(QPointF(x, y).toPoint()) or QGuiApplication.primaryScreen()
        return ledges.work_area(scr)

    def cur(self):
        return self.sp[self.clip]

    def play(self, name):
        if name != self.clip:
            self.clip, self.clip_t = name, self.now
            self.cycle = -1

    def set_mode(self, m):
        self.mode, self.st = m, self.now

    def _peeking(self) -> bool:
        return self.seat is not None and self.seat.kind == "peek"

    def center(self):
        """Middle of her body, global coords (for effects)."""
        if self._peeking():
            return self.pc
        if self.mode == "lay":
            return self.x, self.y - 0.18 * self.S
        up = 0.38 if self.mode == "perch" else 0.5
        return self.x + (0.1 if self.mode == "perch" else -0.05) * self.S, self.y - up * self.S

    def head(self):
        if self.mode == "chip":  # mini: the chat box sits over her little head
            fp = self.chip.floor_point()
            return fp.x(), fp.y() - self.chip.side * 0.8
        if self.mode == "lay" and self.lay.ok:
            return self.x, self.y - self.lay.box[3] - 0.05 * self.S
        if self._peeking():  # her head is the part sticking out of the edge
            cx, cy = self.pc
            d = 0.36 * self.S
            return {"left": (cx + d, cy), "right": (cx - d, cy), "top": (cx, cy + d)}[self.peek_edge]
        if self.mode == "perch" or self.clip.startswith("perch"):
            return self.x + 0.14 * self.S, self.y - 0.56 * self.S
        return self.x - 0.07 * self.S, self.y - 1.03 * self.S

    def standing_clip(self):
        if self.mode == "perch":  # also sitting on the stream stage's CMD, where her desktop seat is still the floor
            return "perch"
        return "perch" if self.seat is not None and self.seat.kind != "floor" else "idle"

    def _portrait(self):
        """Her face right now for the call-style chat box: the current frame (blinks, talking mouth) cropped around
        her head. None while she's not on screen (the box keeps the last face)."""
        if self.mode == "chip":  # mini: her mini face, talking, blinking, annoyed...
            img = self.chip.face()
            if img is None:
                return None
            key = ("mini", self.chip.state())
            if key not in self._faces:
                side = round(FACE[0] * 240 * self.dpr)
                pm = QPixmap.fromImage(img.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                pm.setDevicePixelRatio(self.dpr)
                self._faces[key] = pm
            return self._faces[key]
        if self.mode in ("hidden", "to_chip", "stream", "intro"):
            return None
        key = (self.shown, self.frame, self.mouth)
        if key in self._faces:
            return self._faces[key]
        c = self.sp[self.shown]
        ax, ay = c.anchor
        hx, hy = self.head()
        cx, cy = hx - (self.x - ax * c.w), hy - (self.y - ay * c.h)  # her head in the frame's own pixels
        side = FACE[0] * self.S
        fx, fy = FACE[2] if self.shown.startswith("perch") else FACE[1]
        src = QRectF(cx + fx * self.S - side / 2, cy + fy * self.S - side / 2, side, side)
        out = QPixmap(round(side * self.dpr), round(side * self.dpr))
        out.setDevicePixelRatio(self.dpr)
        out.fill(Qt.transparent)
        q = QPainter(out)
        q.setRenderHint(QPainter.SmoothPixmapTransform)
        q.translate(-src.topLeft())
        q.drawPixmap(0, 0, c.frames[self.frame])
        if self.mouth >= 0:
            q.drawPixmap(0, 0, self.sp["mouth"].frames[self.mouth])
        q.end()
        if len(self._faces) > 200:
            self._faces.clear()
        self._faces[key] = out
        return out

    # -- per-frame ----------------------------------------------------------------
    def tick(self):
        t = time.monotonic()
        dt = min(0.05, t - self.last)
        self.last = t
        self.now = t - self.t0
        m = self.mode
        if m == "intro":
            self._intro_step()
        elif m == "fall":
            self._fall_step(dt)
        elif m == "held":
            self._held_step()
        elif m == "charge":
            self._charge_step()
        elif m == "stream":
            if self.now - self.st >= self.stream_T - 0.05:
                self.arrive()
        elif m == "to_chip":
            if self.now - self.st >= self.stream_T - 0.05:
                self._chip_arrive()
        elif m == "chip":
            self._chip_step()
        elif m == "appear":
            self._appear_step()
        elif m == "bye":
            self._bye_step()
        elif m in ("stand", "perch", "sleep", "lay"):
            self._rest_step()
        elif m == "peek":
            self._peek_step()
        pulse, playing = self.beat() if self.beat else (0.0, False)
        target = pulse if playing else 0.0
        self.vibe = max(target, self.vibe * 0.88)
        self.fx.step(dt)
        if self.entrance is not None and self.entrance.isVisible() and self.mode != "intro":
            self.entrance.step()  # its last frame still fading out over her
        if self.portal is not None and self.portal.isVisible():
            self.portal.step()
            if self.portal.done:
                self.portal.hide()
        for layer in self.layers:
            layer.refresh()
        self._animate()
        if self.mode != "hidden" and self.body.isVisible():
            self._place()
            self.body.update()
        if self.bubble.isVisible():
            hx, hy = self.head()
            self.bubble.place(hx, hy, self.screen_rect(hx, (hy + 10) if self.mode == "chip" else self.y - 10))

    def _place(self):
        self.body.move(round(self.x - self.A.x()), round(self.y - self.A.y()))

    def _animate(self):
        """Pick the frame for this tick: clip timing, blinks during idle loops, mouth flaps while talking."""
        c = self.cur()
        i = int((self.now - self.clip_t) * c.fps)
        if self.clip in ("glitch", "perch_glitch", "wake") and i >= c.count:
            self.play(self.standing_clip() if self.mode != "sleep" else "idle")
            c, i = self.cur(), 0
        if self.clip == "grin" and i >= c.count * 2:
            self.play("idle")
            c, i = self.cur(), 0
        if self.clip in ("idle", "perch"):
            cyc = i // c.count
            if cyc != self.cycle:
                self.cycle = cyc
                r = random.random()
                pre = "perch_" if self.clip == "perch" else ""
                self.blink = (pre + ("blink_glitch" if r < 0.08 else "blink")) if r < 0.45 else None
                if self.clip == "idle" and r > 0.95 and self.mode == "stand" and not self.bubble.isVisible():
                    self.play("grin")
                    return self._animate()
        self.frame = i % c.count
        self.shown = self.clip
        if self.blink and self.clip in ("idle", "perch") and 6 <= self.frame < 10:
            self.shown, self.frame = self.blink, self.frame - 6
        self.mouth = -1
        if self.speaking and self.shown == "grin":  # her hello lands mid-grin: talk with her mouth, not the grin
            self.shown = "idle"
        if self.speaking and self.shown in ("idle", "blink", "blink_glitch"):  # the mouth flaps over her standing face
            self.mouth = int(self.now * 12 + 0.5) % 5 if int(self.now * 12) % 4 else 0

    def draw(self, p: QPainter):
        if self.mode in ("hidden", "chip", "to_chip"):
            return
        if self.mode == "lay" and self.lay.ok:
            return self._draw_lay(p)
        c = self.sp[self.shown]
        pm = c.frames[self.frame]
        if self._peeking():
            return self._draw_peek(p, c, pm)
        ax, ay = c.anchor
        breathe = tilt = 0.0
        if self.mode in ("stand", "perch"):
            breathe = math.sin(self.now * 1.75) * 2.0 * self.k
            tilt = math.sin(self.now * 0.95) * 0.6
            if not self.can_talk:  # the watcher leans a hair toward wherever your cursor is
                lean = max(-1.0, min(1.0, (QCursor.pos().x() - self.x) / (4 * self.S))) * 2.2
                self.gaze = self.gaze * 0.94 + lean * 0.06
                tilt += self.gaze
        sx, sy = self.sx, self.sy
        if self.squash_t >= 0:  # landing squash: flatten, overshoot, settle
            q = (self.now - self.squash_t) / 0.42
            if q >= 1:
                self.squash_t = -1
            else:
                amt = self.squash_amt * math.exp(-5 * q) * math.cos(q * 9)
                sy *= 1 - amt
                sx *= 1 + amt * 0.7
        p.translate(self.A)
        p.translate(0, (breathe if self.mode == "stand" else 0) - self.vibe * 1.2 * self.k)  # a small bob on the beat
        p.rotate(self.angle + tilt)
        mid = 0.5 * self.S if self.mode == "bye" else 0.0
        p.translate(0, -mid)
        p.scale(sx, sy)
        p.translate(0, mid)
        if self.mode == "perch":
            p.translate(0, breathe * 0.5)
        tl = QPointF(-ax * c.w, -ay * c.h)
        if self.white > 0.02:  # electric aura behind her while charging / materialising
            p.save()
            p.setOpacity(min(1.0, self.white) * 0.55)
            for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3)):
                p.drawPixmap(tl + QPointF(dx * self.k, dy * self.k), c.white(self.frame))
            p.restore()
        if self.vibe > 0.5 and self.mode in ("stand", "perch") and self.white < 0.05:
            pm = self._vibed(c, pm)  # on the beat: bands of her hair jump sideways
        p.drawPixmap(tl, pm)
        if self.mouth >= 0:
            p.drawPixmap(tl, self.sp["mouth"].frames[self.mouth])
        if self.white > 0.02:
            p.setOpacity(min(1.0, self.white))
            p.drawPixmap(tl, c.white(self.frame))
            p.setOpacity(1.0)

    def _lay_look(self) -> str:
        if self.speaking:
            return "lay_talk2" if int(self.now * 8) % 3 == 1 else ("lay_talk" if int(self.now * 8) % 3 == 0 else "lay_awake")
        if self.now > self.lay_doze or self.now < self.lay_blink:
            return "lay"  # dozing off, or a blink
        if self.now > self.lay_blink + random.uniform(2.5, 6.0) and random.random() < 0.02:
            self.lay_blink = self.now + 0.15
        return "lay_awake"

    def _draw_lay(self, p: QPainter):
        """Lying on the taskbar or on top of a window, breathing slowly."""
        pm = self.lay.get(self._lay_look())
        breathe = math.sin(self.now * 1.2) * 0.012
        p.translate(self.A)
        p.translate(0, -self.vibe * 1.0 * self.k)
        p.scale(1.0, 1.0 + breathe)
        p.drawPixmap(self.lay.top_left(0, 0), pm)

    def _draw_peek(self, p: QPainter, c, pm):
        """Leaning in from a screen edge: drawn around the middle of her canvas, turned toward the screen, bobbing
        in and out a little like she's sneaking a look."""
        bob = (math.sin(self.now * 1.6) * 0.5 + 0.5) * 0.06 * self.S
        dx, dy = {"left": (bob, 0), "right": (-bob, 0), "top": (0, bob)}[self.peek_edge]
        p.translate(0.75 * self.S + dx, 0.75 * self.S + dy)
        p.rotate(self.peek_angle)
        tl = QPointF(-0.5 * c.w, -0.5 * c.h)
        if self.white > 0.02:
            p.save()
            p.setOpacity(min(1.0, self.white) * 0.55)
            for ox, oy in ((-3, 0), (3, 0), (0, -3), (0, 3)):
                p.drawPixmap(tl + QPointF(ox * self.k, oy * self.k), c.white(self.frame))
            p.restore()
        p.drawPixmap(tl, pm)
        if self.mouth >= 0:
            p.drawPixmap(tl, self.sp["mouth"].frames[self.mouth])
        if self.white > 0.02:
            p.setOpacity(min(1.0, self.white))
            p.drawPixmap(tl, c.white(self.frame))
            p.setOpacity(1.0)

    # -- peeking: she leans in from the side of the screen, or hangs upside down from the top -----------------
    def _peek_spot(self):
        """A screen edge with nothing beyond it (not the side that leads to another monitor): left, right or top,
        somewhere along it. -> a Ledge("peek") with .edge set, or None."""
        screens = QGuiApplication.screens()
        scr = QGuiApplication.screenAt(QPointF(self.x, self.y - 10).toPoint()) or QGuiApplication.primaryScreen()
        g, w = scr.geometry(), ledges.work_area(scr)

        def outside(px, py):
            return not any(s.geometry().contains(round(px), round(py)) for s in screens)

        S = self.S
        edges = []
        if outside(g.left() - 4, g.center().y()):
            edges.append("left")
        if outside(g.right() + 4, g.center().y()):
            edges.append("right")
        if outside(g.center().x(), g.top() - 4):
            edges += ["top", "top"]  # hanging from the top is the cutest: twice the odds
        if not edges:
            return None
        edge = random.choice(edges)
        L = ledges.Ledge("peek", 0, 0, 0)
        L.edge = edge
        L.t = (random.uniform(w.top() + 0.45 * S, w.bottom() - 0.45 * S) if edge != "top"
               else random.uniform(w.left() + 0.4 * S, w.right() - 0.4 * S))
        L.g = g
        return L

    def _edge_peek(self, gx, gy):
        """Dropped close to the left, right or top of the screen (an edge with no monitor beyond it)?
        -> a Ledge("peek") on that edge where she was let go, or None."""
        scr = QGuiApplication.screenAt(QPointF(gx, gy).toPoint()) or QGuiApplication.primaryScreen()
        g, w = scr.geometry(), ledges.work_area(scr)
        S, near = self.S, 0.28 * self.S

        def outside(px, py):
            return not any(s.geometry().contains(round(px), round(py)) for s in QGuiApplication.screens())

        edge = None
        if gx - g.left() < near and outside(g.left() - 4, gy):
            edge = "left"
        elif g.right() - gx < near and outside(g.right() + 4, gy):
            edge = "right"
        elif gy - g.top() < near and outside(gx, g.top() - 4):
            edge = "top"
        if edge is None:
            return None
        L = ledges.Ledge("peek", 0, 0, 0)
        L.edge, L.g = edge, g
        L.t = (min(w.bottom() - 0.45 * S, max(w.top() + 0.45 * S, gy)) if edge != "top"
               else min(w.right() - 0.4 * S, max(w.left() + 0.4 * S, gx)))
        return L

    def _peek_center(self, L):
        out = -0.1 * self.sp["idle"].h  # her middle sits just inside the edge: head, face and shoulders show
        g = L.g
        if L.edge == "left":
            return g.left() - out, L.t
        if L.edge == "right":
            return g.right() + out, L.t
        return L.t, g.top() - out

    def _peek_pixels(self, L, step):
        """Her idle frame turned for that edge, sampled for the pixel stream (so the flight lands in the pose)."""
        c = self.sp["idle"]
        cx, cy = self._peek_center(L)
        ang = math.radians({"left": 90, "right": -90, "top": 180}[L.edge])
        ax, ay = c.anchor
        pts = c.sample(0, cx + (ax - 0.5) * c.w, cy + (ay - 0.5) * c.h, step)
        ca, sa = math.cos(ang), math.sin(ang)
        return [(cx + (x - cx) * ca - (y - cy) * sa, cy + (x - cx) * sa + (y - cy) * ca, col) for x, y, col in pts]

    def _peek_step(self):
        if self.now - self.st > self.peek_len and not self.bubble.isVisible() and not self.speaking:
            self.pop_out()

    def pop_out(self, line=None):
        """Out of the edge and back onto the desktop: she drops in toward the middle of the screen."""
        if not self._peeking():
            return
        edge, (cx, cy) = self.peek_edge, self.pc
        h = self.sp["idle"].h
        self.seat = None
        self.angle = 0.0
        self.x = {"left": cx + 0.5 * h, "right": cx - 0.5 * h, "top": cx}[edge]
        self.y = {"left": cy + 0.5 * h, "right": cy + 0.5 * h, "top": cy + h}[edge]
        vx = {"left": 520, "right": -520, "top": random.uniform(-150, 150)}[edge] * self.k
        vy = {"left": -380, "right": -380, "top": 60}[edge] * self.k
        self.fx.flash(cx, cy, 90, life=0.2, color=PINK)
        self.fx.sparks(cx, cy, 14, speed=(100, 380), life=(0.12, 0.3))
        self.sfx.play("throw")
        self.fall_from_here(vx, vy)
        if line:
            QTimer.singleShot(250, lambda: self.say(line))

    def _vibed(self, c, pm):
        """A copy of the frame with two or three bands of her hair moved sideways (moved, not copied on top, so
        she never looks doubled). Same tear for a few frames so it reads as a glitch, not noise."""
        out = QPixmap(pm.size())
        out.setDevicePixelRatio(pm.devicePixelRatio())
        out.fill(Qt.transparent)
        q = QPainter(out)
        q.drawPixmap(0, 0, pm)
        d, k, v = c.dpr, self.k, self.vibe
        top, bottom = c.h * (0.03 if self.mode == "stand" else 0.16), c.h * (0.42 if self.mode == "stand" else 0.45)
        rnd = random.Random(int(self.now * 12))
        for _ in range(1 + (v > 0.85)):  # barely vibing: one thin band, a second only on a big beat
            hh = rnd.uniform(3, 8) * k
            y0 = rnd.uniform(top, bottom - hh)
            dx = rnd.choice((-1, 1)) * rnd.uniform(1, 3.5) * k * v
            band = QRectF(0, y0, c.w, hh)
            q.setCompositionMode(QPainter.CompositionMode_Clear)
            q.fillRect(band, Qt.transparent)
            q.setCompositionMode(QPainter.CompositionMode_SourceOver)
            q.drawPixmap(band.translated(dx, 0), pm, QRectF(0, y0 * d, pm.width(), hh * d))
        q.end()
        return out

    def listening(self, on: bool):
        """Talk key held (or she caught your voice): the bubble shows a REC dot and your mic level."""
        if not self.can_talk:
            return
        if on:
            self.sfx.play("pop")
            self.next_roam = max(self.next_roam, self.now + 30)
            hx, hy = self.head()
            self.bubble.listen(True)
            self.bubble.place(hx, hy, self.screen_rect(hx, self.y - 10))
        else:
            self.bubble.listen(False)

    # -- intro: the sky tears open and she drops out ------------------------------
    def drop_in(self, greet=True, x_frac=None):
        """Her entrance: the intro flipbook fills the screen, shrinks onto the spot she lands on, and she rebuilds
        out of where it ends, then says hi. (x_frac is ignored: the animation picks where she lands.)"""
        self.bubble.close_bubble()
        scr = QGuiApplication.primaryScreen()
        work = ledges.work_area(scr)
        if self.entrance is None or self.entrance.S != self.S or self.entrance.done:
            self._new_entrance()
        self.entrance.landed = False
        self.aim_at = (QRectF(scr.geometry()), work.bottom() + 1)  # fills the screen, ends where she lands
        self.body.hide()
        self.seat = None
        self.say_after = ((self.greeting() if self.greeting else None)
                          or random.choice(i18n.tlist("lines.greetings", GREETINGS))) if greet else None
        self.set_mode("intro")
        self.sfx.play("charge")

    def _screen_face(self, t: float):
        """Her face for the card's avatar: the standing frames cropped round her head, blinking now and then."""
        blink = (t % 2.6) > 2.35
        name = "blink" if blink else "idle"
        c = self.sp[name]
        frame = min(c.count - 1, int((t % 2.6 - 2.35) * c.fps)) if blink else int(t * c.fps) % c.count
        key = (name, frame, self.S)
        if key in self._screen_faces:
            return self._screen_faces[key]
        ax, ay = c.anchor
        cx, cy = ax * c.w - 0.07 * self.S, ay * c.h - 1.03 * self.S  # her head in the frame's own pixels
        side = FACE[0] * self.S
        fx, fy = FACE[1]
        src = QRectF(cx + fx * self.S - side / 2, cy + fy * self.S - side / 2, side, side)
        out = QPixmap(round(side * self.dpr), round(side * self.dpr))
        out.setDevicePixelRatio(self.dpr)
        out.fill(Qt.transparent)
        q = QPainter(out)
        q.setRenderHint(QPainter.SmoothPixmapTransform)
        q.translate(-src.topLeft())
        q.drawPixmap(0, 0, c.frames[frame])
        q.end()
        self._screen_faces[key] = out
        return out

    def _new_entrance(self):
        scr = QGuiApplication.primaryScreen()
        self.entrance = splash.Entrance(self.S, scr.geometry(), scr.devicePixelRatio())

    def _intro_step(self):
        E = self.entrance
        if not E.started:  # its frames are still loading (a second at most): start once they're in
            if not E.ready:
                return
            x = E.aim(*self.aim_at)
            self.target = (next((f for f in ledges.floors() if f.holds(x)), ledges.floors()[0]), x)
            E.start()
            if E.done:  # no frames: she just rebuilds where she lands
                return self.arrive()
        E.step()
        if E.landed:  # the animation ends on her: she rebuilds right there as it gets sucked into her
            self.arrive()
            cx, cy = self.center()
            self.fx.flash(cx, cy, 190, life=0.45, color=WHITE)
            self.fx.burst(cx, cy, 12, r0=0.35 * self.S / self.k, r1=420, life=0.4, width=(3, 12), colors=(WHITE, PINK, CYAN))
            self.fx.ring(cx, cy, 260, life=0.5, color=PINK, width=2.4)
            self.white = 1.0
            self.squash_t, self.squash_amt = self.now, 0.16

    # -- falling (thrown, dropped, or knocked off a window) -------------------------
    def _fall_step(self, dt):
        g = 2600 * self.k
        self.vy = min(self.vy + g * dt, 4200 * self.k)
        self.vx *= 1 - 0.4 * dt
        nx, ny = self.x + self.vx * dt, self.y + self.vy * dt
        self.white = max(0.0, self.white - dt * 2.5)
        self.angle += (max(-25, min(25, self.vx * 0.02)) - self.angle) * min(1, dt * 6)
        area = self.screen_rect(nx, min(ny, self.y) - 40)
        margin = 0.3 * self.S
        if nx < area.left() + margin:
            nx, self.vx = area.left() + margin, abs(self.vx) * 0.5
        if nx > area.right() - margin:
            nx, self.vx = area.right() - margin, -abs(self.vx) * 0.5
        if ny < area.top() + 1.1 * self.S and self.vy < 0:
            self.vy = max(self.vy, 0) * 0.3
        if self.vy > 0:
            self.refresh_ledges()
            best = None
            for L in self.ledge_list:
                if self.intro_only_floor and L.kind != "floor":
                    continue
                if self.y - 2 <= L.y <= ny + 1 and L.holds(nx) and (best is None or L.y < best.y):
                    best = L
            floor = self._floor_under(nx, self.y)
            if best is None and ny >= floor.y:  # already under every ledge (dragged below the taskbar...): the floor
                best = floor
            if best is not None:
                self.x, self.y = (nx if best.holds(nx) else max(best.left + 8, min(best.right - 8, nx))), best.y
                self.land(best, self.vy)
                return
        self.x, self.y = nx, ny
        if self.vy > 0 and random.random() < 0.25:
            cx, cy = self.center()
            self.fx.embers(cx, cy, 1, radius=40)

    def _floor_under(self, x, y) -> ledges.Ledge:
        """The floor (bottom of a screen's work area) she'd hit falling from (x, y): the nearest one below her on a
        screen under x, else the lowest one there, else the closest screen's (she's off to the side of them all)."""
        fl = ledges.floors()
        under = [f for f in fl if f.holds(x)]
        if not under:
            return min(fl, key=lambda f: min(abs(x - f.left), abs(x - f.right)))
        below = [f for f in under if f.y >= y - 2]
        return min(below, key=lambda f: f.y) if below else max(under, key=lambda f: f.y)

    def land(self, L: ledges.Ledge, speed: float):
        self.intro_only_floor = False
        self.seat = L
        self.angle = 0
        self.vx = self.vy = 0
        x = max(L.left + 8, min(L.right - 8, self.x))
        self.x = x
        self.seat_off = (x - L.rect.left(), L.rect.top()) if L.kind == "window" else None
        perch = L.kind != "floor"
        self.set_mode("perch" if perch else "stand")
        self.play("perch_glitch" if perch else "glitch")
        self.impact(speed)
        self.last_touch = self.now
        self.next_roam = self.now + random.uniform(*self.roam)
        if self.say_after:
            line, self.say_after = self.say_after, None
            QTimer.singleShot(650, lambda: self.say(line))
        QTimer.singleShot(700, self._settled)

    def _settled(self):
        cb, self.on_settled = self.on_settled, None
        if cb:
            cb()

    def _land_floor(self, x, y, quiet=False):
        L = next((f for f in ledges.floors() if f.holds(x)), ledges.floors()[0])
        self.x, self.y = x, L.y
        self.body.show()
        if quiet:
            self.seat = L
            self.set_mode("stand")
            self.play("idle")
            self.next_roam = self.now + random.uniform(*self.roam)
        else:
            self.land(L, 0)

    def impact(self, speed):
        """Hit the ground: a squash, soft dust rolling out both ways, a few of her pixels hopping off, a faint ripple."""
        k = self.k
        s = max(0.25, min(1.5, speed / (1900 * k)))
        self.squash_t, self.squash_amt = self.now, 0.12 + 0.16 * min(1, s)
        x, y = self.x + (0.12 * self.S if self.mode == "perch" else 0), self.y
        self.fx.puffs(x, y, int(4 + 6 * s), spread=0.6 + 0.6 * s)
        self.fx.pixels(x, y - 2, int(6 + 12 * s), self.sp.color, speed=(90 * s + 80, 260 * s + 120),
                       up=(215, 325), ground=y, life=(0.7, 1.2), size=(3, 5))
        self.fx.ring(x, y, 60 + 90 * s, life=0.45, color=QColor(235, 228, 240), width=1.6, flat=0.18)
        self.sfx.play("thud", f"{min(1.0, s):.1f}")
        if s > 0.5:
            QTimer.singleShot(180, lambda: self.sfx.play("tinkle"))

    # -- resting: idle on the floor, or sitting on a window / icon ----------------------
    def _rest_step(self):
        if self.mode == "sleep":
            if self.now - self.st > 40:
                self.wake()
            elif random.random() < 0.012:
                hx, hy = self.head()
                self.fx.embers(hx + 0.1 * self.S, hy + 0.1 * self.S, 1, radius=4, colors=(CYAN,))
            return
        if self.seat and self.seat.kind != "floor":
            self._follow_seat()
            if self.mode not in ("perch", "lay"):
                return
        if self.mode == "lay":  # lying down: she gets up again after a while (never mid-line)
            if self.now > self.lay_until and not self.bubble.isVisible() and not self.speaking:
                self._end_lay()
            elif self.now > self.lay_doze and random.random() < 0.01:
                hx, hy = self.head()
                self.fx.embers(hx, hy, 1, radius=4, colors=(CYAN,))
            return
        if self.roaming and self.now > self.next_roam and not self.bubble.isVisible() and not self.speaking:
            r = random.random()
            bored = self.now - self.last_touch > 120  # nobody's talked to her in a while: she's tired, lies down more
            if r < 0.12:
                self.go_chip()
            elif r < (0.62 if bored else 0.37) and self.lay_ok and self.lay.ok and self.seat is not None and \
                    self.seat.kind in ("floor", "window"):
                self._start_lay()
                if bored:  # a proper lie-down, not just a stretch
                    self.lay_until = self.now + random.uniform(60, 150)
            else:
                self.teleport()

    def _follow_seat(self):
        """Ride along when her window moves; fall when it closes, minimises or something covers her."""
        L = self.seat
        if L.kind == "window":
            r = ledges.window_rect(L.hwnd)
            if r is not None:
                dx = r.left() - L.rect.left()
                dy = r.top() - L.rect.top()
                if dx or dy:
                    self.x += dx
                    self.y = r.top()
                    L.left += dx
                    L.right += dx
                    L.y = r.top()
                    L.rect = r
        else:
            r = L.rect
        self.refresh_ledges()
        ok = r is not None and any(o.key == L.key and o.holds(self.x) for o in self.ledge_list)
        if ok:
            self.lost_since = -1
            return
        if self.lost_since < 0:
            self.lost_since = self.now
        if self.now - self.lost_since > 0.45:
            self.lost_since = -1
            self.fall_from_here()

    def lie_down(self):
        """Asked to lie down: where she stands or sits now (on the taskbar or a window), if her skin has the pose."""
        if self.mode in ("stand", "perch") and self.lay.ok and self.seat is not None and \
                self.seat.kind in ("floor", "window"):
            self._start_lay()
            self.lay_until = self.now + random.uniform(40, 80)

    def _start_lay(self):
        """She lies down right where she is: on the taskbar, or on top of the window she's sitting on."""
        self.set_mode("lay")
        self.lay_until = self.now + random.uniform(25, 60)
        self.lay_doze = self.now + random.uniform(10, 30)  # eyes drift shut after a bit
        self.lay_blink = 0.0
        self.sfx.play("pop")
        self.fx.sparks(self.x, self.y - 0.1 * self.S, 6, speed=(60, 160), life=(0.15, 0.3))

    def _end_lay(self):
        """Up again: back to standing (taskbar) or sitting (window), with a little glitch."""
        perch = self.seat is not None and self.seat.kind != "floor"
        self.set_mode("perch" if perch else "stand")
        self.play("perch_glitch" if perch else "glitch")
        self.next_roam = self.now + random.uniform(*self.roam)

    def fall_from_here(self, vx=0.0, vy=0.0):
        self.seat = None
        self.vx, self.vy = vx, vy
        self.play("dangle")
        self.set_mode("fall")

    def wake(self):
        self.play("wake")
        self.set_mode("stand")
        self.next_roam = self.now + random.uniform(*self.roam)

    # -- teleport: charge up, become a bolt of light, strike down somewhere else ---------
    def pick_spot(self):
        self.refresh_ledges(force=True)
        cur = self.seat.key if self.seat else None
        S = self.S
        pools = {"window": [], "icon": [], "floor": []}
        for L in self.ledge_list:
            if L.key == cur and L.kind != "floor":
                continue
            area = self.screen_rect((L.left + L.right) / 2, L.y - 5)
            if L.y - (0.85 if L.kind == "floor" else 0.64) * S < area.top():  # no headroom (seated she's ~0.62 S tall)
                continue
            lo, hi = max(L.left, area.left()) + 0.3 * S, min(L.right, area.right()) - 0.45 * S
            if L.kind == "icon":
                lo, hi = L.left + 4, max(L.left + 4, L.right - 4)
            if hi <= lo:
                continue
            pools[L.kind].append((L, lo, hi))
        if random.random() < 0.18 and self.mode != "peek":  # every so often: peek in from an edge instead
            L = self._peek_spot()
            if L is not None:
                return L, 0.0
        weights = {"window": 0.6, "icon": 0.2, "floor": 0.2}
        kinds = [k for k in pools if pools[k]]
        if not pools["window"] and ledges.ZOOMED and not self.told_zoomed and random.random() < 0.3:
            self.told_zoomed = True  # once: tell them why she isn't on their apps
            line = i18n.t("lines.no_seat")
            QTimer.singleShot(900, lambda: self.say(line))
        if not kinds:
            return None
        kind = random.choices(kinds, [weights[k] for k in kinds])[0]
        L, lo, hi = random.choice(pools[kind])
        for _ in range(6):  # floor: don't land right where she already is
            x = random.uniform(lo, hi)
            if kind != "floor" or abs(x - self.x) > 1.2 * S:
                break
        return L, x

    def teleport(self, spot=None):
        """Defrag: she breaks into her own pixels, they fly over and rebuild her somewhere else."""
        if self.mode == "peek":
            return self.pop_out()
        if self.mode == "lay":
            self._end_lay()
        if self.mode not in ("stand", "perch", "sleep"):
            return
        spot = spot or self.pick_spot()
        if not spot:
            self.next_roam = self.now + 5
            return
        self.bubble.close_bubble()
        self.target = spot
        self.play("perch_glitch" if self.mode == "perch" else "glitch")
        self.sfx.play("charge")
        self.set_mode("charge")
        self.last_touch = self.now

    def _charge_step(self):
        t = (self.now - self.st) / 0.42
        e = t * t
        cx, cy = self.center()
        if random.random() < 0.08:
            self.fx.arc_around(cx, cy, 0.42 * self.S, 1, random.choice((CYAN, PINK)))
        self.white = min(0.45, e * 0.5)
        if t >= 1:
            self.white = 0.0
            self.start_stream()

    @staticmethod
    def _pair(src, dst, step):
        """Match pixels top to bottom (her hair flies to where her hair will be), thinned to the same count."""
        n = min(len(src), len(dst))
        if n < 10:
            return None
        src.sort(key=lambda q: (round(q[1] / step), q[0]))
        dst.sort(key=lambda q: (round(q[1] / step), q[0]))

        def even(lst):
            return [lst[round(i * (len(lst) - 1) / max(1, n - 1))] for i in range(n)]

        return even(src), even(dst)

    def _target_pixels(self, L, x, step):
        if L.kind == "peek":
            return self._peek_pixels(L, step)
        return self.sp["perch" if L.kind != "floor" else "idle"].sample(0, x, L.y, step)

    def start_stream(self):
        L, x = self.target
        step = STREAM_STEP * self.k
        pair = self._pair(self.sp[self.shown].sample(self.frame, self.x, self.y, step), self._target_pixels(L, x, step), step)
        cx, cy = self.center()
        if not pair:  # nothing to sample (shouldn't happen): just blink over
            self.body.hide()
            self.stream_T = 0.2
            self.seat = None
            return self.set_mode("stream")
        self.body.hide()
        self.sfx.play("stream")
        self.fx.flash(cx, cy, 100, life=0.25, color=PINK)
        self.stream_T = self.fx.start_stream(*pair, travel=0.9, spread=0.4)
        self.seat = None
        self.set_mode("stream")

    # -- naps: she streams into her biochip in the corner -----------------------------
    def focus(self):
        """Where a camera should look right now: her, her pixels mid-flight, or her napping sprite."""
        st = self.fx.stream
        if self.mode in ("stream", "to_chip") and st and st["pos"]:
            pos = st["pos"]
            return sum(q[0] for q in pos) / len(pos), sum(q[1] for q in pos) / len(pos)
        if self.mode == "chip":
            fp = self.chip.floor_point()
            return fp.x(), fp.y() - self.chip.side * 0.5
        if self.mode in ("hidden", "intro"):  # not placed yet: watch where she'll come from
            g = ledges.work_area(QGuiApplication.primaryScreen())
            return getattr(self, "ix", g.left() + g.width() * 0.72), g.center().y()
        cx, cy = self.center()
        if self.bubble.isVisible():  # frame her line too: the bubble is wider than she is
            return self.bubble.geometry().center().x(), cy - 0.25 * self.S
        return cx, cy

    def chip_spot(self):
        if self.chip_pos:
            return self.chip_pos
        g = ledges.work_area(QGuiApplication.primaryScreen())
        self.chip_seat = next((f for f in ledges.floors() if f.holds(g.left() + 1)), None)  # on the taskbar
        return g.left() + self.chip.side * 0.62, g.bottom() + 1

    def _chip_moved(self):
        """Dropped her mini form: dropped close to the taskbar or a window's top, she lies down on it."""
        p = self.chip.floor_point()
        self.chip_pos = (p.x(), p.y())
        self.chip_seat = None
        if self.lay_ok:
            self.refresh_ledges(force=True)
            near = [L for L in self.ledge_list if L.kind in ("window", "floor") and L.holds(p.x())
                    and abs(L.y - p.y()) < 0.35 * self.chip.side]
            if near:
                L = min(near, key=lambda L: abs(L.y - p.y()))
                self.chip_seat = L
                self.chip_pos = (p.x(), L.y)
                self.chip.put(*self.chip_pos)
        self.chip.laying = self._can_lay()

    def _can_lay(self) -> bool:
        return False  # lying down is a full-size thing (like sitting on apps), her mini form just floats

    def _chip_click(self):
        """One click on her mini form: napping, you woke her (grumpy face, then she's back); mini, she listens."""
        self.last_touch = self.now
        if self.mode != "chip" or self.door.active:
            return
        if not self.mini:
            self.chip.zs = False
            self.chip.annoy(1.2)
            self.sfx.play("pop")
            QTimer.singleShot(900, lambda: self.wake_chip())
            return
        if not self.can_talk:
            return
        self.sfx.play("pop")
        if self.bubble.typing:
            self.bubble.close_bubble()
        else:
            hx, hy = self.head()
            self.bubble.open_input()
            self.bubble.place(hx, hy, self.screen_rect(hx, hy + 10))

    def _chip_poked(self):
        if self.can_talk and not self.speaking and self.mode == "chip":
            self.say(random.choice(i18n.tlist("lines.mini.poke", MINI_POKES)))

    def _mini_hop(self):
        """Mini and bored: she glitches over to lie on top of another window, or on the taskbar."""
        self.mini_hop_at = self.now + random.uniform(45, 100)
        self.refresh_ledges(force=True)
        side = self.chip.side
        spots = []
        for L in self.ledge_list:
            if L.kind not in ("window", "floor") or L.right - L.left < 2.2 * side:
                continue
            area = self.screen_rect((L.left + L.right) / 2, L.y - 5)
            if L.y - side < area.top():
                continue
            lo, hi = max(L.left, area.left()) + 0.7 * side, min(L.right, area.right()) - 0.7 * side
            if hi > lo:
                spots.append((L, lo, hi))
        if not spots:
            return
        wins = [sp for sp in spots if sp[0].kind == "window"]
        L, lo, hi = random.choice(wins if wins and random.random() < 0.65 else spots)
        fp = self.chip.floor_point()
        self.fx.flash(fp.x(), fp.y() - side * 0.4, 60, life=0.2, color=PINK)
        x = random.uniform(lo, hi)
        self.chip_seat, self.chip_pos = L, (x, L.y)
        self.chip.flip = x > (L.left + L.right) / 2 and random.random() < 0.5
        self.chip.laying = self._can_lay()
        self.chip.put(x, L.y)
        self.chip.glitch_until = time.monotonic() + 0.25
        self.sfx.play("pop")
        self.fx.flash(x, L.y - side * 0.4, 60, life=0.25, color=CYAN)

    def _follow_chip_seat(self):
        """Her mini form rides along with the window she lies on, and drops to the taskbar when it goes away."""
        L = self.chip_seat
        if L is None or L.kind != "window" or self.door.active or self.chip.press is not None:
            return
        if self.now - self._seat_check < 0.1:  # ten looks a second is plenty
            return
        self._seat_check = self.now
        r = ledges.window_rect(L.hwnd)
        if r is not None and (r.left() != L.rect.left() or r.top() != L.rect.top()):
            dx = r.left() - L.rect.left()
            L.left += dx
            L.right += dx
            L.y = r.top()
            L.rect = r
            x = self.chip_pos[0] + dx
            self.chip_pos = (x, L.y)
            self.chip.put(x, L.y)
        self.refresh_ledges()
        if r is not None and any(o.key == L.key and o.holds(self.chip_pos[0]) for o in self.ledge_list):
            self.lost_since = -1
            return
        if self.lost_since < 0:
            self.lost_since = self.now
        if self.now - self.lost_since > 0.45:  # closed, minimised or covered: down to the taskbar
            self.lost_since = -1
            x = self.chip_pos[0]
            F = next((f for f in ledges.floors() if f.holds(x)), ledges.floors()[0])
            x = min(max(x, F.left + self.chip.side * 0.6), F.right - self.chip.side * 0.6)
            self.chip_seat, self.chip_pos = F, (x, F.y)
            self.chip.put(x, F.y)
            self.chip.glitch_until = time.monotonic() + 0.2
            self.sfx.play("land")

    def go_chip(self, instant=False):
        if self.mode not in ("stand", "perch", "hidden"):
            return
        self.bubble.close_bubble()
        self.seat = None
        cx, cy = self.chip_spot()
        if instant or self.mode == "hidden":
            self.body.hide()
            return self._chip_arrive(quiet=True)
        step = 0.8 * STREAM_STEP * self.k
        pair = self._pair(self.sp[self.shown].sample(self.frame, self.x, self.y, step),
                          sample_chip(self.chip.side, self.dpr, cx, cy, step), step)
        self.body.hide()
        self.sfx.play("stream")
        if pair:
            self.stream_T = self.fx.start_stream(*pair, travel=0.8, spread=0.45)
        else:
            self.stream_T = 0.1
        self.set_mode("to_chip")

    def _chip_arrive(self, quiet=False):
        self.fx.end_stream()
        cx, cy = self.chip_spot()
        self.chip.laying = self._can_lay()
        self.chip.talking = False
        self.mini_hop_at = self.now + random.uniform(30, 70)
        self.chip.put(cx, cy)
        self.chip.show()
        self.chip.raise_()
        self.set_mode("chip")
        self.nap_from = self.now
        self.nap_len = random.uniform(50, 110)
        self.snore_at = self.now + 2.5
        if not quiet:
            self.sfx.play("sleep")
            self.fx.flash(cx, cy - self.chip.side * 0.4, 70, life=0.25, color=PINK)
            self.fx.sparks(cx, cy - self.chip.side * 0.4, 12, speed=(100, 300), life=(0.15, 0.35))

    def _chip_step(self):
        self.chip.tick()
        self._follow_chip_seat()
        busy = self.speaking or self.bubble.isVisible() or self.door.active
        if self.mini:
            if self.chip.zs:  # dozing in mini: wakes up after a while, still mini
                if not busy and self.now > self.snore_at:
                    self.sfx.play("snore")
                    self.snore_at = self.now + 4.5
                if self.now - self.nap_from > self.nap_len and not busy:
                    self.chip.zs = False
                    self.chip.glitch_until = time.monotonic() + 0.15
                return
            if not busy and self.roaming and self.now - self.last_touch > 240 and random.random() < 0.0004:
                self.nap()  # mini and nobody's touched her in ages: she dozes off (eyes shut, z's)
                return
            return
        if busy:  # she's mumbling back at you from her nap: no snores, no waking up mid-line
            self.snore_at = self.now + 3
            return
        if self.now > self.snore_at:
            self.sfx.play("snore")
            self.snore_at = self.now + 4.5
        if self.roaming and self.now - self.nap_from > self.nap_len:
            self.wake_chip()

    def wake_chip(self, spot=None):
        """Out of the chip: her pixels stream out and rebuild her (next to the chip unless told where)."""
        if self.mode != "chip":
            return
        self.mini = False
        self.chip.talking = False
        if self.bubble.typing:
            self.bubble.close_bubble()
        if self.on_full:
            self.on_full()
        fp = self.chip.floor_point()
        if spot is None:
            g = ledges.work_area(QGuiApplication.screenAt(fp.toPoint()) or QGuiApplication.primaryScreen())
            x = min(g.right() - 0.5 * self.S, max(g.left() + 0.4 * self.S, fp.x() + 1.3 * self.S))
            L = next((f for f in ledges.floors() if f.holds(x)), ledges.floors()[0])
            spot = (L, x)
        L, x = spot
        step = 0.8 * STREAM_STEP * self.k
        pair = self._pair(sample_chip(self.chip.side, self.dpr, fp.x(), fp.y(), step), self._target_pixels(L, x, step), step)
        self.chip.hide()
        self.sfx.play("wake")
        self.sfx.play("stream")
        self.target = spot
        self.stream_T = self.fx.start_stream(*pair, travel=0.85, spread=0.4) if pair else 0.1
        self.last_touch = self.now
        self.set_mode("stream")

    def arrive(self):
        L, x = self.target
        if L.kind == "peek":
            return self._arrive_peek(L)
        self.target = None
        k, S = self.k, self.S
        self.x, self.y = x, L.y
        self.seat = L
        self.seat_off = None
        perch = L.kind != "floor"
        self.set_mode("appear")
        self.play("perch_glitch" if perch else "glitch")
        cx, cy = self.center()
        gx = x + (0.12 * S if perch else 0)
        self.sfx.play("rebuild")
        self.fx.end_stream()
        self.fx.flash(cx, cy, 110, life=0.3, color=PINK)
        self.white, self.sx, self.sy = 0.8, 1.0, 1.0
        self.fx.ring(gx, L.y, 150, life=0.45, color=CYAN, width=2.4, flat=0.2)
        self.fx.pixels(gx, L.y - 2, 10, self.sp.color, speed=(120, 320), up=(210, 330), ground=L.y, life=(0.8, 1.4), size=(3, 6))
        self._place()
        self.body.show()

    def _arrive_peek(self, L):
        self.target = None
        self.seat = L
        self.peek_edge = L.edge
        self.peek_angle = {"left": 90, "right": -90, "top": 180}[L.edge]
        self.pc = self._peek_center(L)
        self.peek_len = random.uniform(15, 35)
        cx, cy = self.pc
        # her canvas middle on that point (the body window is placed from x / y and the anchor)
        self.x = cx - 0.75 * self.S + self.A.x()
        self.y = cy - 0.75 * self.S + self.A.y()
        self.angle = 0.0
        self.set_mode("appear")
        self.play("idle")
        self.sfx.play("rebuild")
        self.fx.end_stream()
        hx, hy = self.head()
        self.fx.flash(hx, hy, 100, life=0.3, color=PINK)
        self.white, self.sx, self.sy = 0.8, 1.0, 1.0
        self._place()
        self.body.show()

    def _appear_step(self):
        t = min(1.0, (self.now - self.st) / 0.38)
        self.white = max(0.0, 0.8 - t * 1.3)
        if t >= 1 and self._peeking():
            self.white = 0
            self.set_mode("peek")
            if random.random() < 0.35:
                line = random.choice(i18n.tlist("lines.peeks", PEEKS))
                if line:
                    QTimer.singleShot(700, lambda: self.say(line))
            self._settled()
            return
        if t >= 1:
            self.sx = self.sy = 1.0
            self.white = 0
            self.set_mode("perch" if self.seat.kind != "floor" else "stand")
            self.next_roam = self.now + random.uniform(*self.roam)
            if self.say_after:  # just arrived on your desktop: hi!
                line, self.say_after = self.say_after, None
                self.play("grin")
                QTimer.singleShot(350, lambda: self.say(line))
            elif self.roaming and random.random() < 0.2:
                line = random.choice(i18n.tlist("lines.arrivals", ARRIVALS))
                if line:
                    QTimer.singleShot(500, lambda: self.say(line))
            self._settled()

    # -- mouse: click to chat, drag to carry, let go to throw ---------------------------
    def press(self, e):
        if e.button() != Qt.LeftButton or self.mode not in ("stand", "perch", "sleep", "fall", "peek", "lay"):
            return
        self.press_at = e.globalPosition()
        self.grab = e.position()
        self.hist = [(self.now, self.press_at.x(), self.press_at.y())]
        if self.mode == "fall":  # snatch her out of the air
            self._grab()

    def _grab(self):
        if self._peeking():  # pulled out of the edge: upright, under the cursor
            cx, cy = self.pc
            self.seat = None
            self.angle = 0.0
            self.x, self.y = cx, cy + 0.5 * self.sp["idle"].h
            g = QCursor.pos()
            self.grab = QPointF(g.x() - self.x + self.A.x(), g.y() - self.y + self.A.y())
        self.set_mode("held")
        self.seat = None
        self.play("dangle")
        self.bubble.close_bubble()
        self.white = 0
        self.sx = self.sy = 1.0
        self.sfx.play("grab")

    def drag_move(self, e):
        if self.press_at is None:
            return
        g = e.globalPosition()
        if self.mode != "held":
            if (g - self.press_at).manhattanLength() < 6:
                return
            self._grab()
        self.hist.append((self.now, g.x(), g.y()))
        self.hist = self.hist[-8:]

    def _held_step(self):
        g = QCursor.pos()
        nx = g.x() - self.grab.x() + self.A.x()
        ny = g.y() - self.grab.y() + self.A.y()
        vx = (nx - self.x) * 60
        self.x, self.y = nx, ny
        self.angle += (max(-30, min(30, -vx * 0.012)) - self.angle) * 0.18  # swings behind the motion

    def release(self, e):
        if e.button() != Qt.LeftButton:
            return
        was = self.press_at
        self.press_at = None
        if self.mode != "held":
            if was is not None:
                self.click()
            return
        recent = [h for h in self.hist if self.now - h[0] < 0.09] or self.hist[-2:]
        vx = vy = 0.0
        if len(recent) >= 2 and recent[-1][0] > recent[0][0]:
            span = recent[-1][0] - recent[0][0]
            vx = (recent[-1][1] - recent[0][1]) / span
            vy = (recent[-1][2] - recent[0][2]) / span
        cap = 3200 * self.k
        vx, vy = max(-cap, min(cap, vx)), max(-cap, min(cap, vy))
        self.ledges_at = -9
        if math.hypot(vx, vy) < 900 * self.k:  # set down against a screen edge: she tucks in and peeks from it
            g = QCursor.pos()
            L = self._edge_peek(g.x(), g.y())
            if L is not None:
                self.target = (L, 0.0)
                self._arrive_peek(L)
                self.last_touch = self.now
                return
        if math.hypot(vx, vy) > 900 * self.k:
            self.sfx.play("throw")
        self.fall_from_here(vx, vy)
        self.last_touch = self.now

    def click(self):
        self.last_touch = self.now
        if self.mode == "peek":  # caught peeking
            return self.pop_out(random.choice(i18n.tlist("lines.pops", POP_OUTS)))
        self.sfx.play("pop")
        if self.mode == "sleep":
            self.wake()
            return
        if self.bubble.typing:
            self.bubble.close_bubble()
        elif self.can_talk:  # the watcher doesn't chat: she just keeps looking at you
            hx, hy = self.head()
            self.bubble.open_input()
            self.bubble.place(hx, hy, self.screen_rect(hx, self.y - 10))
        self.next_roam = max(self.next_roam, self.now + 20)

    # -- talking ----------------------------------------------------------------
    def say(self, text, user="", done=None, more=False):
        """Speak a line: the bubble types it in step with her voice, her mouth moves while the audio plays.
        more=True: the reply is still being written; say_more() adds sentences, say_end() closes it (blips only).
        The watcher says nothing; `done` still runs (quitting waits on it)."""
        if not self.can_talk:
            if done:
                QTimer.singleShot(0, done)
            return
        if self.mode == "sleep":
            self.wake()
        self.next_roam = max(self.next_roam, self.now + 30)

        def started(secs, cps=None):
            if not self.bubble.isVisible():
                self.sfx.play("pop")
            self.speaking = True
            self.blip_n = 0
            self.chip.thinking = False
            if self.mode == "chip":  # she answers as mini (half asleep if she's napping): her little mouth moves
                self.chip.talking = True
                if sounds_annoyed(text):
                    self.chip.annoy(3 + len(text) / 24.0)
                hx, hy = self.head()
                self.bubble.place(hx, hy, self.screen_rect(hx, hy + 10))
            self.bubble.say(text, user=user, secs=secs, cps=cps)

        def ended():
            self.speaking = False
            self.chip.talking = False
            self.bubble.release(2.4)
            self.next_roam = max(self.next_roam, self.now + 8)
            if done:
                done()

        if self.blips:  # no voice: old-school RPG text blips, paced by the bubble; done once it's all typed
            started(None, 24.0)
            self.talk_open = more
            if self.talk_timer is not None:
                self.talk_timer.stop()
            timer = QTimer()

            def check():
                # done once it's all typed, or once the bubble got closed mid-line (grabbed, fell): it won't type on,
                # and waiting for it would leave her talking (mouth flapping) forever
                if not self.bubble.isVisible() or (not self.talk_open and self.bubble.typed_all()):
                    timer.stop()
                    QTimer.singleShot(200, ended)

            timer.timeout.connect(check)
            timer.start(50)
            self.talk_timer = timer
        elif self.voice:
            self.voice(text, started, ended)
        else:
            secs = min(9.0, 0.6 + len(text) * 0.05)
            started(secs)
            QTimer.singleShot(int(secs * 1000), ended)

    def _typed(self, ch: str):
        if self.blips and self.speaking:
            self.blip_n += 1
            if self.blip_n % 2:  # every other letter, like the old handhelds
                self.sfx.play("blip", ch.lower())

    def say_more(self, text: str):
        if not self.can_talk:
            return
        if self.speaking:
            if self.mode == "chip" and sounds_annoyed(text):
                self.chip.annoy(3 + len(text) / 24.0)
            self.bubble.append(text)
        else:  # the first line already finished: start a fresh one
            self.say(text, more=True)

    def say_end(self):
        self.talk_open = False

    def nap(self):
        """A nap she takes herself (the brain decides): snores, and wakes up again after a while. Already mini, she
        dozes off right there and wakes up mini again."""
        self.chip.zs = True
        self.nap_from = self.now
        self.nap_len = random.uniform(50, 110)
        if self.mode == "chip":
            return
        self.mini = False
        self.go_chip()

    def toggle_mini(self):
        if self.mode == "chip":
            if not self.mini:  # napping: "Go mini" keeps her small and awake instead of waking her up full size
                self.mini = True
                self.chip.zs = False
                return
            self.wake_chip()
        elif self.mode in ("stand", "perch", "hidden", "sleep", "lay"):
            self.go_mini()

    def go_mini(self, instant=False):
        """Mini on purpose (menu, "Always mini", or asked to): small and awake, she stays until you bring her back."""
        if self.mode == "chip":
            self.mini = True
            self.chip.zs = False
            return
        if self.mode == "sleep":
            self.wake()
            self.set_mode("stand")
        if self.mode == "lay":
            self._end_lay()
        self.mini = True
        self.chip.zs = False
        self.go_chip(instant=instant)

    # -- her menu, drawn inside a glitchy door that opens beside her (beside the chip in mini mode) -------
    def open_menu(self):
        D = self.door
        if D.active or self.mode not in ("stand", "perch", "sleep", "chip", "appear", "lay"):
            return
        # mini: a smaller door, right up against her little self; full size: the door at her size
        D.set_scale(0.8 * max(0.9, self.k), low=0.7) if self.mode == "chip" else D.set_scale(0.9 * min(self.k, 1.2), low=0.75)
        w, h = D.size_for(self.menu)
        self.next_roam = max(self.next_roam, self.now + 30)
        self.press_at = None  # a left press the menu swallowed must not turn into a drag once it closes
        if self.mode == "chip":
            fp = self.chip.floor_point()
            side = self.chip.side
            g = ledges.work_area(QGuiApplication.screenAt(fp.toPoint()) or QGuiApplication.primaryScreen())
            gap = 0.02 * side  # tucked in close beside her
            pad = 0.82 * side + gap  # the chip's spot, left of the door
            cx = 0.5 * side  # chip centre, in the door window
            x = min(max(fp.x() - cx, g.left()), g.right() - (pad + w))
            y = min(max(fp.y() - 0.42 * side - h / 2, g.top()), g.bottom() - h)
            self.chip_home = (fp.x(), fp.y())
            self.chip.put(x + cx, y + h / 2 + 0.42 * side)  # halfway up the door's left side
            self.chip.glitch_until = time.monotonic() + 0.2
            D.open(x, y, pad, link=(cx + 0.3 * side, pad + INSIDE.left() * D.sx + 4, h / 2), on_closed=self._menu_closed)
            self.chip.raise_()
        else:
            cx, cy = self.center()
            g = ledges.work_area(QGuiApplication.screenAt(QPointF(cx, cy).toPoint()) or QGuiApplication.primaryScreen())
            reach = 0.3 * self.S
            x = cx + reach
            if x + w > g.right():  # no room on her right: open on her left
                x = cx - reach - w
            x = min(max(x, g.left()), g.right() - w)
            y = min(max(cy - h / 2, g.top()), g.bottom() - h)
            D.open(x, y, on_closed=self._menu_closed)

    def _menu_closed(self):
        self.next_roam = max(self.next_roam, self.now + 8)
        if self.chip_home and self.mode == "chip":  # back where it was
            self.chip.glitch_until = time.monotonic() + 0.2
            self.chip.put(*self.chip_home)
        self.chip_home = None

    # -- quitting: a goodbye line, then she switches off like an old CRT --------------------
    def bye(self, then):
        if self.mode == "bye":
            return
        self.roaming = False
        self.bye_chip = self.mode == "chip"  # mini: she leaves the same way, streaming up into the screen top right
        if self.mode == "chip" and self.can_talk:
            self.say(random.choice(i18n.tlist("lines.byes", BYES)), done=lambda: self._crt_off(then))
            return
        if self.mode in ("stand", "perch"):
            self.say(random.choice(i18n.tlist("lines.byes", BYES)), done=lambda: self._crt_off(then))
        else:
            self._crt_off(then)

    def _crt_off(self, then):
        self.bubble.close_bubble()
        chip = getattr(self, "bye_chip", False) and self.chip.isVisible()
        if not self.body.isVisible() and not chip:
            QTimer.singleShot(300, then)
            return
        if chip:
            self.chip.glitch_until = time.monotonic() + 0.4
        else:
            self.play("perch_glitch" if self.mode == "perch" else "glitch")
        self.bye_then = then
        self.bye_stage = 0
        if self.portal is None:
            self.portal = Portal(self.k, self._screen_face)
        self.portal.start(ledges.work_area(QGuiApplication.primaryScreen()), leaving=True)
        self.sfx.play("charge")
        self.set_mode("bye")

    def _bye_step(self):
        """The entrance backwards: the brackets lock on at the top right, she streams up into them, the little screen
        flicks on with her face and a "cya" sticker, then shuts down to a dot and the brackets fly apart."""
        t = self.now - self.st
        P = self.portal
        if self.bye_stage == 0:
            self.white = min(0.5, t / 0.35 * 0.5)  # glowing up before she goes
            if t < 0.35:
                return
            g = P.screen_rect()
            cx, cy = g.center().x(), g.center().y()
            step = STREAM_STEP * self.k
            c = self.sp["idle"]
            ax, ay = c.anchor
            dot = [(cx + (px - cx) * 0.1, cy + (py - cy) * 0.1, col)
                   for px, py, col in c.sample(0, cx + (ax - 0.5) * c.w, cy + (ay - 0.5) * c.h, step)]
            if getattr(self, "bye_chip", False) and self.chip.isVisible():  # her mini self streams up instead
                fp = self.chip.floor_point()
                src = sample_chip(self.chip.side, self.dpr, fp.x(), fp.y(), step)
                self.chip.hide()
            else:
                src = self.sp[self.shown].sample(self.frame, self.x, self.y, step)
            pair = self._pair(src, dot, step)
            self.body.hide()
            self.white = 0.0
            self.sfx.play("stream")
            self.bye_T = self.now + (self.fx.start_stream(*pair, travel=0.85, spread=0.35) if pair else 0.1)
            self.bye_stage = 1
        elif self.bye_stage == 1 and self.now > self.bye_T:  # all of her is in: the screen comes on
            self.fx.end_stream()
            c = P.screen_rect().center()
            self.fx.flash(c.x(), c.y(), 90, life=0.3, color=PINK)
            self.bye_stage = 2
        elif self.bye_stage == 2 and P.t > SCREEN_FACE:
            c = P.screen_rect().center()
            self.fx.burst(c.x(), c.y(), 12, r0=10, r1=420, life=0.45, width=(4, 14))
            self.sfx.play("crtoff")
            self.bye_stage = 3
        elif self.bye_stage == 3 and P.done:
            P.hide()
            self.sx = self.sy = 1.0
            self.set_mode("hidden")
            QTimer.singleShot(250, self.bye_then)

    def _crt_dot(self, x, y, floor):
        """The last glowing pixel hangs for a beat, then drops and bounces on the floor. Bloop."""
        self.fx.flash(x, y, 60, life=0.5)
        self.fx.embers(x, y, 3, radius=3, colors=(WHITE,))
        QTimer.singleShot(450, lambda: (self.fx.parts.append(_dot(x, y, floor, self.k)), self.sfx.play("bloop")))

    def heard(self, line: str):
        if not self.can_talk:
            return
        self.bubble.think(line)
        if self.mode == "chip":
            self.chip.thinking = True  # mini: her loading glitch until the answer starts
        self.next_roam = max(self.next_roam, self.now + 40)
        if self.on_user:
            self.on_user(line)
        else:
            QTimer.singleShot(900, lambda: self.say(random.choice(i18n.tlist("lines.demo", DEMO_REPLIES)), user=line))

    # -- menu / tray -------------------------------------------------------------
    def _build_menu(self):
        m = self.menu = QMenu()
        m.setStyleSheet(
            "QMenu { background: rgb(14,6,22); color: rgb(246,240,250); border: 1px solid rgb(255,20,96); padding: 4px; }"
            "QMenu::item { padding: 5px 18px; } QMenu::item:selected { background: rgb(255,20,96); color: black; }"
            "QMenu::separator { height: 1px; background: rgb(60,30,70); margin: 4px 8px; }")
        from .. import i18n

        self.talk_action = m.addAction(i18n.t("menu.talk"), self.click)
        self.talk_action.setVisible(self.can_talk)
        self.nap_action = m.addAction(i18n.t("menu.mini"), self.toggle_mini)
        roam = QAction(i18n.t("menu.roam"), m, checkable=True, checked=True)
        roam.toggled.connect(lambda on: setattr(self, "roaming", on))
        m.addAction(roam)
        self.roam_action = roam
        m.addSeparator()
        self.quit_action = m.addAction(i18n.t("menu.quit"), lambda: self.bye(self.quit_fn))
        m.aboutToShow.connect(self._sync_menu_texts)

    def _sync_menu_texts(self):
        from .. import i18n

        self.nap_action.setText(i18n.t("menu.full") if self.mode == "chip" and self.mini else i18n.t("menu.mini"))

    def retranslate(self):
        """The interface language changed: rewrite this menu's words and the tray tip."""
        from .. import i18n

        self.talk_action.setText(i18n.t("menu.talk"))
        self.roam_action.setText(i18n.t("menu.roam"))
        self.quit_action.setText(i18n.t("menu.quit"))
        self._sync_menu_texts()
        try:
            self.tray.setToolTip(i18n.t("tray.tip"))
        except AttributeError:
            pass

    def _build_tray(self):
        from .. import i18n
        from ..config import ASSETS

        icon = QIcon(str(ASSETS / "icons" / "tray.png"))
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip(i18n.t("tray.tip"))
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(lambda r: self.toggle_mini() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _topmost(self):
        import ctypes

        for w in (self.body, self.bubble):
            if w.isVisible():
                ctypes.windll.user32.SetWindowPos(int(w.winId()), -1, 0, 0, 0, 0, 0x2 | 0x1 | 0x10)
