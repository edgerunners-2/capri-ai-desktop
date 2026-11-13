"""Screen-wide effects: debris pixels, sparks, shockwaves, flashes, lightning and her defrag pixel stream.

Effects live in global (logical) desktop coordinates in one `Fx` model. Each monitor gets a click-through
`FxLayer` window that is only shown while something is happening on it, and only the dirty area repaints.
"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient
from PySide6.QtWidgets import QWidget

CYAN = QColor(120, 250, 255)
WHITE = QColor(255, 255, 255)
PINK = QColor(255, 20, 96)
VIOLET = QColor(170, 90, 255)


def _alpha(c: QColor, a: float) -> QColor:
    c = QColor(c)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def jag(points: list[tuple[float, float]], jitter: float, depth: int = 4) -> list[QPointF]:
    """Lightning path: midpoint displacement between the given anchor points."""
    pts = [QPointF(*points[0])]
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        seg = [(ax, ay), (bx, by)]
        j = jitter
        for _ in range(depth):
            nxt = [seg[0]]
            for (x0, y0), (x1, y1) in zip(seg, seg[1:]):
                dx, dy = x1 - x0, y1 - y0
                ln = math.hypot(dx, dy) or 1
                off = random.uniform(-j, j)
                nxt += [((x0 + x1) / 2 - dy / ln * off, (y0 + y1) / 2 + dx / ln * off), (x1, y1)]
            seg = nxt
            j *= 0.55
        pts += [QPointF(x, y) for x, y in seg[1:]]
    return pts


class P:
    __slots__ = ("x", "y", "vx", "vy", "age", "life", "size", "color", "kind", "ground", "grav", "drag", "rest")

    def __init__(self, x, y, vx, vy, life, size, color, kind, ground=None, grav=0.0, drag=0.0):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.age, self.life, self.size, self.color, self.kind = 0.0, life, size, color, kind
        self.ground, self.grav, self.drag, self.rest = ground, grav, drag, False


class Fx:
    def __init__(self, scale: float = 1.0):
        self.s = scale
        self.amount = 0.55  # how busy the effects are: particle counts and flash sizes scale with this
        self.parts: list[P] = []
        self.rings: list[dict] = []
        self.flashes: list[dict] = []
        self.bolts: list[dict] = []
        self.bars: list[dict] = []
        self.bursts: list[dict] = []
        self.stream: dict | None = None
        self.t = 0.0

    # -- spawners -------------------------------------------------------------
    def pixels(self, x, y, n, colors, speed=(250, 700), up=(200, 340), ground=None, life=(1.4, 2.6), size=(3, 7)):
        n = max(1, round(n * self.amount))
        """Chunky square pixels in her colours, thrown in an arc between angles `up` (degrees, 270 = up)."""
        s = self.s
        for _ in range(n):
            a = math.radians(random.uniform(*up))
            v = random.uniform(*speed) * s
            self.parts.append(P(x + random.uniform(-14, 14) * s, y - random.uniform(0, 6) * s,
                                math.cos(a) * v, math.sin(a) * v, random.uniform(*life),
                                max(2, round(random.uniform(*size) * s)), colors(), "pixel", ground, 2200 * s, 0.6))

    def sparks(self, x, y, n, speed=(300, 900), spread=(0, 360), life=(0.18, 0.45), colors=(CYAN, WHITE, PINK), grav=600):
        n = max(1, round(n * self.amount))
        s = self.s
        for _ in range(n):
            a = math.radians(random.uniform(*spread))
            v = random.uniform(*speed) * s
            self.parts.append(P(x, y, math.cos(a) * v, math.sin(a) * v, random.uniform(*life),
                                random.uniform(1.4, 2.6) * s, random.choice(colors), "spark", None, grav * s, 2.2))

    def embers(self, x, y, n, radius=30, colors=(CYAN, PINK, WHITE)):
        s = self.s
        for _ in range(n):
            a = random.uniform(0, math.tau)
            r = random.uniform(0, radius) * s
            self.parts.append(P(x + math.cos(a) * r, y + math.sin(a) * r, random.uniform(-40, 40) * s,
                                random.uniform(-160, -40) * s, random.uniform(0.3, 0.8),
                                random.uniform(2, 5) * s, random.choice(colors), "ember", None, 0, 1.5))

    def ring(self, x, y, r1, life=0.45, color=CYAN, width=3.0, flat=1.0, r0=0.0, half=0):
        """Expanding shockwave. flat < 1 squashes it into a ground ellipse; half=1 keeps the lower half."""
        self.rings.append(dict(x=x, y=y, r0=r0 * self.s, r1=r1 * self.s, age=0.0, life=life, color=color,
                               width=width * self.s, flat=flat, half=half))

    def flash(self, x, y, radius, life=0.3, color=WHITE):
        radius *= 0.55 + 0.45 * self.amount
        self.flashes.append(dict(x=x, y=y, r=radius * self.s, age=0.0, life=life, color=color))

    def bolt(self, points, life=0.25, color=CYAN, width=2.4, jitter=26, branches=2):
        self.bolts.append(dict(pts=points, age=0.0, life=life, color=color, width=width * self.s,
                               jitter=jitter * self.s, branches=branches))

    def burst(self, x, y, n=9, r0=40, r1=520, life=0.38, aim=None, spread=360, width=(5, 16),
              colors=(WHITE, PINK, VIOLET)):
        """Speed lines shooting out of a point: long tapered wedges, thin at the point and wide at the far end.
        aim: the middle direction in degrees (0 = right, 90 = down), with `spread` degrees around it."""
        s = self.s
        lines = []
        for i in range(n):
            a = (aim if aim is not None else 0) + (random.uniform(-spread / 2, spread / 2) if aim is not None
                                                   else i * 360 / n + random.uniform(-12, 12))
            lines.append((math.radians(a), random.uniform(0.55, 1.0) * r1 * s, random.uniform(*width) * s,
                          random.choice(colors), random.uniform(0, 0.08)))
        self.bursts.append(dict(x=x, y=y, r0=r0 * s, lines=lines, age=0.0, life=life))

    def puffs(self, x, y, n=6, color=QColor(215, 205, 222), spread=1.0):
        """Soft dust puffs rolling out sideways from a landing: they swell, slow down and fade."""
        s = self.s
        for i in range(n):
            side = -1 if i % 2 else 1
            v = random.uniform(70, 210) * s * spread
            self.parts.append(P(x + side * random.uniform(0, 14) * s, y - random.uniform(2, 10) * s, side * v,
                                -random.uniform(10, 45) * s, random.uniform(0.45, 0.8),
                                random.uniform(7, 13) * s, color, "puff", None, 0, 3.2))

    def glitch_band(self, x0, x1, y0, y1, life=0.45):
        self.bars.append(dict(x0=x0, x1=x1, y0=y0, y1=y1, age=0.0, life=life))

    def arc_around(self, cx, cy, radius, n=1, color=CYAN):
        """Little static arcs crackling over her body while she charges up."""
        r = radius * self.s
        for _ in range(n):
            a = random.uniform(0, math.tau)
            b = a + random.uniform(0.6, 1.6)
            ra, rb = r * random.uniform(0.5, 1.0), r * random.uniform(0.5, 1.0)
            self.bolt([(cx + math.cos(a) * ra, cy + math.sin(a) * ra * 1.6),
                       (cx + math.cos(b) * rb, cy + math.sin(b) * rb * 1.6)], life=0.09, color=color,
                      width=1.6, jitter=12, branches=0)

    def start_stream(self, src, dst, travel=0.85, spread=0.5):
        """Her body breaking into its own pixels, which fly over and rebuild her at the destination.
        src / dst: lists of (x, y, QColor) in screen coords, paired by index. Pixels nearest the target leave
        first, so she peels away toward where she's going."""
        k = self.s
        tx = sum(d[0] for d in dst) / len(dst)
        sx0 = min(p[0] for p in src)
        sx1 = max(p[0] for p in src)
        span = max(1.0, sx1 - sx0)
        lift = random.choice((-1, 1))
        dist = math.hypot(tx - sum(s[0] for s in src) / len(src), dst[0][1] - src[0][1])
        arch = min(320.0, 90 + 0.25 * dist) * k  # how high the ribbon arcs: more for a longer trip
        parts = []
        for (ax, ay, col), (bx, by, _) in zip(src, dst):
            near = (sx1 - ax) / span if tx >= ax else (ax - sx0) / span
            delay = spread * near * 0.85 + random.uniform(0, spread * 0.15)
            # all pixels share one arch with a little jitter, so she flows over as one ribbon, not a spray
            c1 = (ax + random.uniform(-18, 18) * k, ay - arch + random.uniform(-25, 25) * k)
            c2 = (bx + lift * (50 + random.uniform(-15, 15)) * k, by - arch * 1.1 + random.uniform(-25, 25) * k)
            parts.append((ax, ay, c1[0], c1[1], c2[0], c2[1], bx, by, delay, col, random.uniform(0.95, 1.08)))
        self.stream = dict(age=0.0, parts=parts, travel=travel, total=spread + travel * 1.15, end=-1.0, pos=[])
        return self.stream["total"]

    def end_stream(self):
        if self.stream:
            self.stream["end"] = self.stream["age"]

    @staticmethod
    def _bez(p, u):
        ax, ay, c1x, c1y, c2x, c2y, bx, by = p[:8]
        a = 1 - u
        return (a ** 3 * ax + 3 * a * a * u * c1x + 3 * a * u * u * c2x + u ** 3 * bx,
                a ** 3 * ay + 3 * a * a * u * c1y + 3 * a * u * u * c2y + u ** 3 * by)

    def _stream_pos(self):
        st = self.stream
        out = []
        for p in st["parts"]:
            q = (st["age"] - p[8]) / (st["travel"] * p[10])
            q = 0.0 if q < 0 else 1.0 if q > 1 else q
            u = 0.5 - 0.5 * math.cos(math.pi * q)  # ease in-out sine: no snap at either end
            x, y = self._bez(p, u)
            px, py = self._bez(p, max(0.0, u - 0.06))
            out.append((x, y, px, py, q, p[9]))
        st["pos"] = out

    # -- simulation -----------------------------------------------------------
    def step(self, dt: float):
        self.t += dt
        keep = []
        for p in self.parts:
            p.age += dt
            if p.age >= p.life:
                continue
            if not p.rest:
                p.vy += p.grav * dt
                f = max(0.0, 1 - p.drag * dt)
                p.vx *= f
                if p.kind != "pixel":
                    p.vy *= f
                p.x += p.vx * dt
                p.y += p.vy * dt
                if p.ground is not None and p.y >= p.ground - p.size / 2 and p.vy > 0:
                    p.y = p.ground - p.size / 2
                    if p.vy < 120 * self.s:  # settled on the floor: lie there and fade
                        p.rest, p.vx, p.vy = True, 0, 0
                    else:
                        p.vy *= -random.uniform(0.25, 0.45)
                        p.vx *= 0.55
            keep.append(p)
        self.parts = keep
        for lst in (self.rings, self.flashes, self.bolts, self.bars, self.bursts):
            for it in lst:
                it["age"] += dt
            lst[:] = [it for it in lst if it["age"] < it["life"]]
        if self.stream:
            st = self.stream
            st["age"] += dt
            if st["end"] >= 0 and st["age"] - st["end"] > 0.22:
                self.stream = None
            else:
                self._stream_pos()

    def busy(self) -> bool:
        return bool(self.parts or self.rings or self.flashes or self.bolts or self.bars or self.bursts or self.stream)

    def bounds(self) -> QRect:
        """Everything that might draw this frame, in global coords (with glow margins)."""
        r = QRectF()

        def add(x, y, m):
            nonlocal r
            r = r.united(QRectF(x - m, y - m, 2 * m, 2 * m))

        for p in self.parts:
            add(p.x, p.y, p.size + abs(p.vx) * 0.05 + abs(p.vy) * 0.05 + 6)
        for g in self.rings:
            add(g["x"], g["y"], g["r1"] + g["width"] * 4)
        for f in self.flashes:
            add(f["x"], f["y"], f["r"])
        for b in self.bolts:
            for x, y in b["pts"]:
                add(x, y, b["jitter"] * 1.6 + 30 * self.s)
        for b in self.bursts:
            far = b["r0"] + max(ln[1] for ln in b["lines"]) + 20
            r = r.united(QRectF(b["x"] - far, b["y"] - far, 2 * far, 2 * far))
        for b in self.bars:
            r = r.united(QRectF(b["x0"] - 40, b["y0"], b["x1"] - b["x0"] + 80, b["y1"] - b["y0"]))
        if self.stream and self.stream["pos"]:
            xs = [q[0] for q in self.stream["pos"]] + [q[2] for q in self.stream["pos"]]
            ys = [q[1] for q in self.stream["pos"]] + [q[3] for q in self.stream["pos"]]
            m = 16 * self.s
            r = r.united(QRectF(min(xs) - m, min(ys) - m, max(xs) - min(xs) + 2 * m, max(ys) - min(ys) + 2 * m))
        return r.toAlignedRect()

    # -- drawing --------------------------------------------------------------
    def paint(self, p: QPainter, ox: int, oy: int):
        p.translate(-ox, -oy)
        p.setRenderHint(QPainter.Antialiasing, True)
        self._paint_bars(p)
        self._paint_bursts(p)
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        for f in self.flashes:
            k = f["age"] / f["life"]
            g = QRadialGradient(QPointF(f["x"], f["y"]), f["r"] * (0.6 + 0.6 * k))
            g.setColorAt(0, _alpha(f["color"], 0.95 * (1 - k)))
            g.setColorAt(0.25, _alpha(CYAN, 0.45 * (1 - k)))
            g.setColorAt(1, _alpha(PINK, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(QPointF(f["x"], f["y"]), f["r"] * 1.2, f["r"] * 1.2)
        for g in self.rings:
            k = g["age"] / g["life"]
            e = 1 - (1 - k) ** 3
            rad = g["r0"] + (g["r1"] - g["r0"]) * e
            rect = QRectF(g["x"] - rad, g["y"] - rad * g["flat"], 2 * rad, 2 * rad * g["flat"])
            for w, a in ((g["width"] * 4, 0.18), (g["width"] * 2, 0.35), (g["width"], 0.95)):
                p.setPen(QPen(_alpha(g["color"], a * (1 - k)), w * (1 - 0.5 * k)))
                p.setBrush(Qt.NoBrush)
                if g["half"]:
                    p.drawArc(rect, 180 * 16, 180 * 16)
                else:
                    p.drawEllipse(rect)
        for b in self.bolts:
            self._paint_bolt(p, b)
        if self.stream and self.stream["pos"]:
            self._paint_stream(p)
        for q in self.parts:
            a = 1 - max(0.0, (q.age - q.life * 0.6) / (q.life * 0.4))
            if q.kind == "spark":
                p.setPen(QPen(_alpha(q.color, a), q.size, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(QPointF(q.x, q.y), QPointF(q.x - q.vx * 0.03, q.y - q.vy * 0.03))
            elif q.kind == "ember":
                p.setPen(Qt.NoPen)
                p.setBrush(_alpha(q.color, a * 0.8))
                p.drawEllipse(QPointF(q.x, q.y), q.size, q.size)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.Antialiasing, False)
        for q in self.parts:
            if q.kind == "puff":
                k = q.age / q.life
                p.setPen(Qt.NoPen)
                p.setBrush(_alpha(q.color, 0.4 * (1 - k) ** 1.5))
                r = q.size * (1 + 1.6 * k)
                p.setRenderHint(QPainter.Antialiasing, True)
                p.drawEllipse(QPointF(q.x, q.y), r, r * 0.7)
                p.setRenderHint(QPainter.Antialiasing, False)
                continue
            if q.kind == "pixel":
                a = 1 - max(0.0, (q.age - q.life * 0.65) / (q.life * 0.35))
                if a <= 0:
                    continue
                c = _alpha(q.color, a)
                sz = q.size
                p.fillRect(QRectF(round(q.x - sz / 2), round(q.y - sz / 2), sz, sz), c)
                if not q.rest and q.age < 0.25:  # fresh debris glows hot for a moment
                    p.fillRect(QRectF(round(q.x - sz / 2), round(q.y - sz / 2), sz, sz),
                               _alpha(WHITE, 0.6 * (1 - q.age / 0.25)))

    def _paint_bolt(self, p: QPainter, b: dict):
        k = b["age"] / b["life"]
        flick = 0.55 + 0.45 * random.random()
        path_pts = jag(b["pts"], b["jitter"])
        path = QPainterPath(path_pts[0])
        for pt in path_pts[1:]:
            path.lineTo(pt)
        for w, c, a in ((b["width"] * 7, b["color"], 0.12), (b["width"] * 3, b["color"], 0.4), (b["width"], WHITE, 1.0)):
            p.setPen(QPen(_alpha(c, a * (1 - k) * flick), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        for _ in range(b["branches"]):  # forks off the main channel
            if len(path_pts) < 4:
                break
            i = random.randrange(1, len(path_pts) - 2)
            o = path_pts[i]
            ln = b["jitter"] * random.uniform(1.0, 2.4)
            a = random.uniform(0, math.tau)
            fork = jag([(o.x(), o.y()), (o.x() + math.cos(a) * ln, o.y() + math.sin(a) * ln)], b["jitter"] * 0.4, 3)
            fp = QPainterPath(fork[0])
            for pt in fork[1:]:
                fp.lineTo(pt)
            p.setPen(QPen(_alpha(b["color"], 0.7 * (1 - k)), b["width"] * 0.7))
            p.drawPath(fp)

    def _paint_stream(self, p: QPainter):
        st, k = self.stream, self.s
        fade = 1.0 if st["end"] < 0 else max(0.0, 1 - (st["age"] - st["end"]) / 0.22)
        size = 5.6 * k
        # light streaks behind some of the flying pixels (additive)
        for i, (x, y, px, py, q, col) in enumerate(st["pos"]):
            if 0 < q < 1 and i % 3 == 0:
                hot = 1 - abs(q - 0.5) * 2
                p.setPen(QPen(_alpha(CYAN if (int(x) ^ int(y)) & 1 else PINK, (0.25 + 0.5 * hot) * fade), size * 0.9, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(QPointF(px, py), QPointF(x, y))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.Antialiasing, False)
        for x, y, px, py, q, col in st["pos"]:
            c = QColor(col)
            if 0 < q < 1:  # in flight: brighter, whiter
                w = 0.55 * (1 - abs(q - 0.5) * 2)
                c = QColor(round(c.red() + (255 - c.red()) * w), round(c.green() + (255 - c.green()) * w), round(c.blue() + (255 - c.blue()) * w))
            c.setAlphaF(fade)
            p.fillRect(QRectF(x - size / 2, y - size / 2, size, size), c)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setCompositionMode(QPainter.CompositionMode_Plus)

    def _paint_bursts(self, p: QPainter):
        for b in self.bursts:
            for a, ln, w, col, delay in b["lines"]:
                t = (b["age"] - delay) / (b["life"] - delay)
                if t <= 0 or t >= 1:
                    continue
                head = 1 - (1 - min(1.0, t * 1.7)) ** 3  # the far end shoots out fast...
                tail = max(0.0, (t - 0.3) / 0.7) ** 1.4  # ...then the near end chases it
                ca, sa = math.cos(a), math.sin(a)
                r_in, r_out = b["r0"] + ln * tail, b["r0"] + ln * head
                if r_out - r_in < 2:
                    continue
                px, py = -sa * w / 2, ca * w / 2
                ox, oy = b["x"] + ca * r_out, b["y"] + sa * r_out
                poly = QPolygonF([QPointF(b["x"] + ca * r_in, b["y"] + sa * r_in), QPointF(ox + px, oy + py),
                                  QPointF(ox - px, oy - py)])
                p.setPen(Qt.NoPen)
                p.setBrush(_alpha(col, 0.9 * (1 - t ** 3)))
                p.drawPolygon(poly)

    def _paint_bars(self, p: QPainter):
        """Horizontal tear lines across a band of the screen, like the signal breaking up."""
        for b in self.bars:
            k = b["age"] / b["life"]
            for _ in range(int(10 * (1 - k) * self.amount) + 1):
                y = random.uniform(b["y0"], b["y1"])
                h = random.uniform(1, 7) * self.s
                x0 = random.uniform(b["x0"], b["x1"])
                w = random.uniform(40, 420) * self.s
                c = random.choice((CYAN, PINK, WHITE, VIOLET))
                p.fillRect(QRectF(x0 - w / 2, y, w, h), _alpha(c, random.uniform(0.25, 0.8) * (1 - k)))


class FxLayer(QWidget):
    """One click-through, always-on-top transparent window per monitor, drawing the shared Fx model."""

    def __init__(self, fx: Fx, screen):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.fx = fx
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.dirty = QRect()

    def refresh(self):
        g = self.geometry()
        b = self.fx.bounds() if self.fx.busy() else QRect()
        hit = b.intersected(g)
        if hit.isEmpty() and self.dirty.isEmpty():
            if self.isVisible():
                self.hide()
            return
        if not self.isVisible():
            self.show()
            self._click_through()
        area = hit.translated(-g.x(), -g.y()).united(self.dirty)
        self.dirty = hit.translated(-g.x(), -g.y())
        self.update(area.adjusted(-4, -4, 4, 4))

    def _click_through(self):
        import sys

        if sys.platform == "win32":
            import ctypes

            hwnd = int(self.winId())
            u = ctypes.windll.user32
            u.SetWindowLongW(hwnd, -20, u.GetWindowLongW(hwnd, -20) | 0x20 | 0x80000 | 0x08000000 | 0x80)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        g = self.geometry()
        self.fx.paint(p, g.x(), g.y())
        p.end()
