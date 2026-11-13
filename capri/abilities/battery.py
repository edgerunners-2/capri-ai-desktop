"""The laptop's battery: (percent, plugged in) or None on a desktop / when Windows won't say."""
from __future__ import annotations

import sys

LOW = 20  # at or under this, unplugged: she goes mini to save power and tells you
OK = 25  # back over this (or plugged in): she comes back full size


def status() -> tuple[int, bool] | None:
    if sys.platform == "win32":
        import ctypes

        class _SPS(ctypes.Structure):
            _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte), ("pct", ctypes.c_ubyte),
                        ("saver", ctypes.c_ubyte), ("life", ctypes.c_ulong), ("full", ctypes.c_ulong)]

        s = _SPS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return None
        if s.flag == 128 or s.flag == 255 or s.pct == 255:  # no battery / unknown
            return None
        return int(s.pct), s.ac == 1
    if sys.platform.startswith("linux"):  # straight from the kernel: no extra package needed
        from pathlib import Path

        try:
            bats = [d for d in Path("/sys/class/power_supply").iterdir()
                    if (d / "type").read_text().strip() == "Battery" and (d / "capacity").exists()]
            if not bats:
                return None
            pct = int((bats[0] / "capacity").read_text().strip())
            status = (bats[0] / "status").read_text().strip().lower()
            ac = any((d / "online").read_text().strip() == "1" for d in Path("/sys/class/power_supply").iterdir()
                     if (d / "type").read_text().strip() == "Mains" and (d / "online").exists())
            return pct, ac or status in ("charging", "full")
        except (OSError, ValueError):
            return None
    try:
        import psutil

        b = psutil.sensors_battery()
    except Exception:
        return None
    return (round(b.percent), bool(b.power_plugged)) if b else None


def low(st) -> bool:
    return bool(st) and not st[1] and st[0] <= LOW


def fine(st) -> bool:
    return not st or st[1] or st[0] > OK
