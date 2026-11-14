"""Hold-to-talk keys without a keyboard hook: a combo like "ctrl+1" is a set of virtual-key codes, and
"held" just asks Windows whether every one of them is down right now (Linux: the kernel's key state, see
capri/linux.py). Also captures a new combo for rebinding."""
from __future__ import annotations

import ctypes
import sys

NAMES = {
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
    "left ctrl": 0xA2, "right ctrl": 0xA3, "left shift": 0xA0, "right shift": 0xA1, "left alt": 0xA4, "right alt": 0xA5,
    "space": 0x20, "tab": 0x09, "caps lock": 0x14, "enter": 0x0D, "backspace": 0x08, "esc": 0x1B,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23, "page up": 0x21, "page down": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "`": 0xC0, "-": 0xBD, "=": 0xBB,
    "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC,
    "mouse4": 0x05, "mouse5": 0x06,
}
NAMES.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})
NAMES.update({str(d): 0x30 + d for d in range(10)})
NAMES.update({f"f{n}": 0x6F + n for n in range(1, 25)})
NAMES.update({f"num {d}": 0x60 + d for d in range(10)})
_BY_VK = {v: k for k, v in NAMES.items() if not k.startswith(("left ", "right ")) or k in ("right ctrl", "right alt", "right shift")}
_MODS = {0x11: "ctrl", 0x10: "shift", 0x12: "alt", 0x5B: "win"}
# sided modifier codes fold into the generic one for capture
_FOLD = {0xA2: 0x11, 0xA3: 0x11, 0xA0: 0x10, 0xA1: 0x10, 0xA4: 0x12, 0xA5: 0x12, 0x5C: 0x5B}

def parse(combo: str) -> list[int]:
    keys = [k.strip().lower() for k in combo.replace(" + ", "+").split("+") if k.strip()]
    vks = [NAMES[k] for k in keys if k in NAMES]
    if len(vks) != len(keys) or not vks:
        raise ValueError(f"unknown key in {combo!r}")
    return vks


def pretty(combo: str) -> str:
    return "+".join(k.strip().title() if len(k.strip()) > 1 else k.strip().upper() for k in combo.split("+"))


def held(vks: list[int]) -> bool:
    if not vks:
        return False
    if sys.platform != "win32":
        down = _linux_down() if sys.platform.startswith("linux") else set()
        return all(vk in down for vk in vks)
    g = ctypes.windll.user32.GetAsyncKeyState
    return all(g(vk) & 0x8000 for vk in vks)


def down_now() -> set[int]:
    """Every key that is down at this instant (sided modifiers folded into ctrl / shift / alt / win)."""
    if sys.platform != "win32":
        return {_FOLD.get(vk, vk) for vk in _linux_down()} if sys.platform.startswith("linux") else set()
    g = ctypes.windll.user32.GetAsyncKeyState
    out = set()
    for vk in list(_MODS) + [v for v in _BY_VK if v not in _MODS] + list(_FOLD):
        if g(vk) & 0x8000:
            out.add(_FOLD.get(vk, vk))
    out.discard(0x01)
    out.discard(0x02)
    return out


def name(vks) -> str:
    mods = [_MODS[v] for v in (0x11, 0x10, 0x12, 0x5B) if v in vks]
    rest = [_BY_VK.get(v, f"vk{v}") for v in vks if v not in _MODS]
    return "+".join(mods + rest)


class Capture:
    """Call poll() on a timer while the user presses a new combo: returns the combo name once every key
    is let go (the biggest set that was down together), else None."""

    def __init__(self):
        self.best: set[int] = set()
        self.seen_down = False

    def poll(self) -> str | None:
        now = down_now()
        if now:
            self.seen_down = True
            if len(now) >= len(self.best):
                self.best = now
            return None
        if self.seen_down and self.best and any(v not in _MODS for v in self.best):
            return name(self.best)
        if self.seen_down:  # only modifiers: not a usable combo, start over
            self.best, self.seen_down = set(), False
        return None
