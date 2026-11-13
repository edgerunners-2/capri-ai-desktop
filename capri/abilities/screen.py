"""What kind of window you're in (a game, a YouTube video, Discord or another chat, something distracting) and a
closer read of it: the messages in a chat and what you're typing in its message box, the comments under a video.
All of it is read off the screen with Windows' OCR, on the PC, and never saved. Private windows and apps on your
never-look list are skipped (vision.PRIVATE + the list in her settings)."""
from __future__ import annotations

import re
import sys

from . import vision

# where you chat: matched against the window title and the program's file name
CHATS = {
    "Discord": re.compile(r"\bdiscord\b", re.I),
    "WhatsApp": re.compile(r"\bwhatsapp\b", re.I),
    "Telegram": re.compile(r"\btelegram\b", re.I),
    "Messenger": re.compile(r"\bmessenger\b", re.I),
    "Instagram": re.compile(r"\binstagram\b", re.I),
    "Slack": re.compile(r"\bslack\b", re.I),
    "Teams": re.compile(r"\bmicrosoft teams\b|\bms-teams\b", re.I),
}
# the time sinks focus mode keeps you off
DISTRACTING = re.compile(r"\b(?:youtube|tiktok|reddit|twitter|x\.com|instagram|facebook|netflix|twitch|discord|"
                         r"9gag|tumblr|pinterest|crunchyroll|prime video|disney\+|kick\.com)\b|\s-\sX$", re.I)
# a game, by where its program lives (or what it's called)
GAME_PATH = re.compile(r"steamapps[\\/]common|[\\/]riot games[\\/]|[\\/]epic games[\\/]|xboxgames|[\\/]ea games[\\/]|"
                       r"ubisoft game launcher[\\/]games|[\\/]battle\.net[\\/]|[\\/]roblox[\\/]versions|minecraft|"
                       r"[\\/]games[\\/]", re.I)
NOT_GAME = re.compile(r"launcher|steamwebhelper|epicgameslauncher|riotclient|battle\.net\.exe|ealauncher|"
                      r"crashhandler|unitycrashhandler|easyanticheat", re.I)
GAME_EXE = re.compile(r"^(?:valorant|valorant-win64-shipping|leagueoflegends|league of legends|cs2|csgo|"
                      r"fortniteclient-win64-shipping|robloxplayerbeta|minecraft|javaw|gta5|gtav|r5apex|overwatch|"
                      r"cyberpunk2077|eldenring|rocketleague|destiny2|cod|modernwarfare|dota2|osu!|genshinimpact|"
                      r"zenlesszonezero|starrail|marvel-win64-shipping|deadlock|thefinals|witcher3)\.exe$", re.I)

never_look: list[str] = []  # set by the app from her settings: words that mark windows she must not read


def private(title: str, exe: str = "") -> bool:
    """A window she must not read: logins, banking, password managers, private browsing, your never-look list."""
    if vision.PRIVATE.search(title or ""):
        return True
    hay = f"{title} {exe}".lower()
    return any(w and w.lower() in hay for w in never_look)


def foreground_exe() -> str:
    """Full path of the program in front ('' when it can't be told)."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes

        import psutil

        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(ctypes.windll.user32.GetForegroundWindow(), ctypes.byref(pid))
        return psutil.Process(pid.value).exe()
    except Exception:
        return ""


def fullscreen() -> bool:
    """The window in front covers its whole monitor (games, fullscreen video)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        import ctypes.wintypes as wt

        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        r = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        u.GetMonitorInfoW(u.MonitorFromWindow(hwnd, 2), ctypes.byref(mi))
        m = mi.rcMonitor
        return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom
    except Exception:
        return False


def kind(title: str, exe: str = "") -> tuple[str, str]:
    """-> (kind, name): ('game', 'VALORANT'), ('youtube', video title), ('chat', 'Discord'), ('distracting', site),
    ('browser', ''), ('other', '')."""
    import os

    name = os.path.basename(exe)
    if exe and (GAME_EXE.match(name) or (GAME_PATH.search(exe) and not NOT_GAME.search(name))):
        game = re.sub(r"\s*[-|]\s*(?:steam|epic games)$", "", title, flags=re.I).strip() or os.path.splitext(name)[0]
        return "game", game
    clean = vision.BROWSER.sub("", title.strip())
    if m := vision.YOUTUBE.match(clean):
        video = m.group("song").strip()
        if video.lower() not in ("youtube", "home", "subscriptions", "history", "watch later", "shorts"):
            return "youtube", video
    for app, rx in CHATS.items():
        if rx.search(title) or rx.search(name):
            return "chat", app
    if DISTRACTING.search(title):
        site = DISTRACTING.search(title).group(0).strip(" -")
        return "distracting", site
    if vision.BROWSER.search(title):
        return "browser", ""
    return "other", ""


def read_box(fx0: float, fy0: float, fx1: float, fy1: float) -> list[tuple[str, float]]:
    """OCR one part of the window in front, given as fractions of it (0..1). -> [(line, y fraction)], top to bottom."""
    if sys.platform != "win32":
        return []
    try:
        import winocr
        from PIL import ImageGrab

        r = vision.foreground_rect()
        if not r or r[2] - r[0] < 200 or r[3] - r[1] < 150:
            return []
        w, h = r[2] - r[0], r[3] - r[1]
        box = (r[0] + int(w * fx0), r[1] + int(h * fy0), r[0] + int(w * fx1), r[1] + int(h * fy1))
        img = ImageGrab.grab(bbox=box, all_screens=True)
        if img.width > 1600:
            img = img.resize((1600, round(img.height * 1600 / img.width)))
        res = winocr.recognize_pil_sync(img, "en")
        out = []
        for ln in res.get("lines", []):
            text = ln["text"].strip()
            ys = [wd["bounding_rect"]["y"] for wd in ln.get("words", []) if "bounding_rect" in wd]
            y = (min(ys) / img.height) if ys else 0.0
            if len(text) > 1:
                out.append((text, fy0 + y * (fy1 - fy0)))
        return out
    except Exception as e:
        print(f"[capri] screen read failed: {e}")
        return []


# Discord-style layout: servers + channels on the left, the chat in the middle, members on the right, the message box
# along the bottom. Other chat apps put the chat on the right of a contact list: close enough with their own boxes.
_LAYOUT = {
    "Discord": ((0.24, 0.07, 0.80, 0.86), (0.24, 0.86, 0.80, 0.97)),
    "WhatsApp": ((0.33, 0.08, 1.00, 0.88), (0.33, 0.88, 1.00, 0.98)),
    "Telegram": ((0.30, 0.07, 1.00, 0.88), (0.30, 0.88, 1.00, 0.98)),
}
_PLACEHOLDER = re.compile(r"^(?:message\s|type a message|write a message|send a message|aa$|\+$)", re.I)
# "jayden Today at 4:12 PM": a message's name + time line, not something anyone said
_HEADER = re.compile(r"\b\d{1,2}[:.]\d{2}\s*(?:[ap]\.?\s?[mv]\.?)?$|\b(?:today|yesterday) at\b", re.I)
_UI_NOISE = re.compile(r"^(?:today at|yesterday at|\d{1,2}:\d{2}|new messages?|jump to present|\(edited\)|"
                       r"replying to|is typing|are typing|several people are typing)", re.I)


def read_chat(app: str, title: str, exe: str = "") -> tuple[list[str], str]:
    """-> (the last few messages you can see in the chat, what you've typed in the message box so far).
    Nothing for private windows or apps on the never-look list."""
    if private(title, exe):
        return [], ""
    chat_box, type_box = _LAYOUT.get(app, ((0.30, 0.08, 1.00, 0.87), (0.30, 0.87, 1.00, 0.98)))
    r = vision.foreground_rect()
    if app == "Discord" and r and r[2] - r[0] > 700:  # its side bars are fixed widths: skip them by pixels
        w = r[2] - r[0]
        right = (w - 275) / w if w > 1250 else 1.0  # the member list only shows in wide windows
        chat_box = (365 / w, chat_box[1], right, chat_box[3])
        type_box = (365 / w, type_box[1], right, type_box[3])
    if r and r[3] - r[1] > 300:  # the message box is a fixed ~75 px strip at the bottom, whatever the window size
        split = 1 - 76 / (r[3] - r[1])
        chat_box = (chat_box[0], chat_box[1], chat_box[2], split)
        type_box = (type_box[0], split, type_box[2], 1 - 14 / (r[3] - r[1]))
    lines = [t for t, _ in read_box(*chat_box) if not _UI_NOISE.match(t) and not _HEADER.search(t)
             and re.search(r"[A-Za-z]{2}", t)]
    typed = " ".join(t for t, _ in read_box(*type_box)).strip()
    if _PLACEHOLDER.match(typed) or len(typed) < 2:
        typed = ""
    return lines[-8:], typed


def read_comments(title: str, allow_private: bool = False) -> list[str]:
    """The YouTube comments you can see right now (only once you've scrolled down to them). Reads the left column
    (the right one is recommended videos), starting at the "N Comments" heading or, scrolled past it, at the first
    "@someone" line. allow_private: you asked for this window to be read (a scripted video), so the private-window
    rule steps aside."""
    if private(title) and not allow_private:
        return []
    lines = [t for t, _ in read_box(0.02, 0.12, 0.62, 0.98)]
    start = next((i for i, t in enumerate(lines) if re.match(r"^[\d,.]+[KkMm]?\s+Comments?\b", t)), None)
    if start is None:
        start = next((i - 1 for i, t in enumerate(lines) if re.match(r"^@[\w.-]{2,}", t)), None)
    if start is None:
        return []
    out = []
    for t in lines[start + 1:]:
        if re.match(r"^(?:@[\w.-]+|Sort by|Add a comment|Reply|\d+\s*(?:replies|reply)|[\d.]+[KkMm]?$|"
                    r"[\d.]+[KkMm]?\b.{0,4}Reply$|\d+ (?:seconds?|minutes?|hours?|days?|weeks?|months?|years?) ago)", t, re.I):
            continue
        if len(t) > 8:
            out.append(t)
    return out[:6]
