"""Her eyes: which window you're in, and a quick read of what's in it (Windows' built-in OCR, on the CPU;
on Linux, tesseract).
Nothing is saved. Logins, banking, password managers and private windows are never read."""
from __future__ import annotations

import re
import sys

PRIVATE = re.compile(r"password|passwort|1password|bitwarden|keepass|lastpass|bank|paypal|sign[ -]?in|log[ -]?in|"
                     r"incognito|inprivate|private (?:browsing|window)|authenticator|wallet|"
                     r"^@.+ - Discord$|^Discord \| @", re.I)  # Discord DMs: a private chat, she doesn't read it
BROWSER = re.compile(r"\s-\s(?:Brave|Google Chrome|Microsoft​? Edge|Mozilla Firefox|Opera|Vivaldi)$", re.I)
YOUTUBE = re.compile(r"^(?:\(\d+\)\s*)?(?P<song>.+?)\s-\sYouTube(?:\s-\s.*)?$", re.I)


_last_other = 0  # the last window in front that wasn't hers: what you were looking at before you typed to her


def _own(hwnd) -> bool:
    """A window of this process (her bubble, her menu): asking her something puts one in front of your screen."""
    import ctypes
    import os
    from ctypes import wintypes

    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def _foreground():
    global _last_other
    import ctypes

    h = ctypes.windll.user32.GetForegroundWindow()
    if h and not _own(h):
        _last_other = h
    return h


def front_hwnd():
    """The window you're looking at: the one in front, or, while you're typing to her, the one you were in before."""
    import ctypes

    u = ctypes.windll.user32
    h = _foreground()
    if h and _own(h) and _last_other and u.IsWindow(_last_other) and u.IsWindowVisible(_last_other)             and not u.IsIconic(_last_other):
        return _last_other
    return h


def _title(hwnd) -> str:
    import ctypes

    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def active_window_title() -> str:
    if sys.platform != "win32":
        from .. import linux

        return linux.active_title() if sys.platform.startswith("linux") else ""
    return _title(_foreground())


def front_title() -> str:
    """Title of the window you're looking at (see front_hwnd)."""
    if sys.platform != "win32":
        return active_window_title()
    return _title(front_hwnd())


def window_titles() -> list[str]:
    """Titles of every visible top-level window (to spot a YouTube tab playing behind something else)."""
    if sys.platform != "win32":
        from .. import linux

        return linux.titles() if sys.platform.startswith("linux") else []
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(h, _):
        if u.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(512)
            if u.GetWindowTextW(h, buf, 512):
                out.append(buf.value)
        return True

    u.EnumWindows(visit, 0)
    return out


def youtube_song(titles: list[str]) -> str:
    """'(3) Kavinsky - Nightcall - YouTube - Brave' -> 'Kavinsky - Nightcall' (first YouTube window found)."""
    for t in titles:
        m = YOUTUBE.match(BROWSER.sub("", t.strip()))
        if m and m.group("song").strip().lower() != "youtube":
            return re.sub(r"\s*[(\[](?:official|lyrics?|audio|video|hd|4k|mv|music video)[^)\]]*[)\]]", "", m.group("song"), flags=re.I).strip()
    return ""


def foreground_rect() -> tuple[int, int, int, int] | None:
    if sys.platform != "win32":
        if sys.platform.startswith("linux"):
            from .. import linux

            return linux.active_box()
        return None
    import ctypes
    import ctypes.wintypes as wt

    r = wt.RECT()
    if not ctypes.windll.user32.GetWindowRect(front_hwnd(), ctypes.byref(r)):
        return None
    return r.left, r.top, r.right, r.bottom


def glance(max_chars: int = 700) -> tuple[str, str]:
    """(window title, text read off it). Empty text for private windows or when OCR isn't available."""
    title = active_window_title().strip()
    if sys.platform.startswith("linux"):  # on Wayland the title can be blank (a native window): still a glance
        if PRIVATE.search(title):
            return title, ""
        try:
            from .. import linux

            img = linux.grab_active()
            if img is None or img.width < 80 or img.height < 60:
                return title, ""
            img = img.crop((0, min(110 if BROWSER.search(title) else 0, img.height // 3), img.width, img.height))
            lines = [ln for ln in linux.ocr(img) if len(ln) > 2 and re.search(r"[A-Za-z]{3}", ln)]
            return title, " | ".join(lines)[:max_chars]
        except Exception as e:
            print(f"[capri] eyes: {e}")
            return title, ""
    if not title or PRIVATE.search(title):
        return title, ""
    try:
        import winocr
        from PIL import ImageGrab

        r = foreground_rect()
        if not r or r[2] - r[0] < 80 or r[3] - r[1] < 60:
            return title, ""
        top = r[1] + (110 if BROWSER.search(title) else 30)  # skip tabs / address bar / title bar
        img = ImageGrab.grab(bbox=(r[0], top, r[2], r[3]), all_screens=True)
        if img.width > 1600:
            img = img.resize((1600, round(img.height * 1600 / img.width)))
        res = winocr.recognize_pil_sync(img, "en")
        lines = [ln["text"].strip() for ln in res.get("lines", []) if len(ln["text"].strip()) > 2]
        lines = [ln for ln in lines if re.search(r"[A-Za-z]{3}", ln)]  # drop timestamps, counters, glyph junk
        return title, " | ".join(lines)[:max_chars]
    except Exception as e:
        print(f"[capri] eyes: {e}")
        return title, ""


# -- the facts under a real look: which app, its window title, the big headings and the text on it ----------------
# A vision model guesses; these are read straight off the PC, so her look is grounded in them ("you're in Liked
# Songs in Spotify", not "some music app").
APP_NAMES = {"spotify": "Spotify", "discord": "Discord", "chrome": "Google Chrome", "brave": "Brave", "msedge": "Edge",
             "firefox": "Firefox", "opera": "Opera", "code": "VS Code", "steam": "Steam", "steamwebhelper": "Steam",
             "explorer": "File Explorer", "obs64": "OBS", "whatsapp": "WhatsApp", "telegram": "Telegram",
             "notepad": "Notepad", "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint",
             "photoshop": "Photoshop", "vlc": "VLC", "epicgameslauncher": "Epic Games", "riotclientux": "Riot Client",
             "leagueclientux": "League of Legends", "minecraft": "Minecraft", "javaw": "Minecraft (Java)",
             "windowsterminal": "Terminal", "cmd": "Command Prompt", "taskmgr": "Task Manager", "netflix": "Netflix",
             "claude": "Claude", "chatgpt": "ChatGPT", "slack": "Slack", "teams": "Teams", "ms-teams": "Teams",
             "notion": "Notion", "figma": "Figma", "blender": "Blender", "vivaldi": "Vivaldi", "arc": "Arc",
             "zen": "Zen Browser", "applicationframehost": "", "wallpaper64": "Wallpaper Engine", "vrchat": "VRChat"}

# app names that end a window title ("capri-ai - Brave", "Liked Songs - Spotify"): what's left is the page / tab
_TITLE_TAIL = re.compile(r"\s+[-—|]\s+(?:Brave|Google Chrome|Microsoft​?\s*Edge|Mozilla Firefox|Firefox|Opera|Vivaldi|"
                         r"Zen Browser|Arc|Discord|Spotify(?: Premium)?|Visual Studio Code|File Explorer|Claude|Notion|"
                         r"Slack|Steam|YouTube)(?:\s+[-—|]\s+.*)?$", re.I)


def page_name(title: str) -> str:
    """The page or tab a window shows, from its title: 'capri-ai: ...' out of 'GitHub - Capri-cmd/capri-ai: ... - Brave'."""
    t = re.sub(r"^\(\d+\)\s*", "", (title or "").strip())  # "(3) Inbox" unread counts
    cut = _TITLE_TAIL.sub("", t).strip()
    if cut == t:  # no app name at the end: a song playing ("Artist - Song"), a bare app title... not a page
        return ""
    t = cut
    if t.lower().startswith("github - "):
        t = t[9:]
    return t if t and t.lower() not in ("claude", "discord", "spotify", "new tab") else ""


def front_app() -> str:
    """The program behind the window you're looking at: 'Spotify', 'Discord', 'Chrome'... ('' if unknown)."""
    try:
        if sys.platform == "win32":
            import ctypes
            import os
            from ctypes import wintypes

            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(front_hwnd(), ctypes.byref(pid))
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(1024)
                n = wintypes.DWORD(1024)
                if not ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                    return ""
                stem = os.path.splitext(os.path.basename(buf.value))[0]
            finally:
                ctypes.windll.kernel32.CloseHandle(h)
        elif sys.platform.startswith("linux"):
            from .. import linux

            stem = getattr(linux, "active_app", lambda: "")() or ""
        else:
            return ""
    except Exception:
        return ""
    name = APP_NAMES.get(stem.lower())
    return name if name is not None else stem[:1].upper() + stem[1:]


_OCR_LANG = {"en": "en-US", "es": "es-ES", "pt": "pt-BR", "fr": "fr-FR", "ru": "ru-RU", "it": "it-IT", "de": "de-DE",
             "pl": "pl-PL"}


def ocr_lines(img, lang: str = "en") -> list[tuple[str, float, float]]:
    """Every line of text in a PIL image, as (text, height in px, top y). Windows OCR in the user's language (English
    if that language pack isn't installed); tesseract on Linux (no sizes there: height 0)."""
    if sys.platform.startswith("linux"):
        from .. import linux

        return linux.ocr_lines(img, lang)
    import winocr

    res = None
    for code in dict.fromkeys([_OCR_LANG.get(lang, "en-US"), "en-US", "en"]):
        try:
            res = winocr.recognize_pil_sync(img, code)
            if res and res.get("lines"):
                break
        except Exception:
            continue
    out = []
    for ln in (res or {}).get("lines", []):
        text = (ln.get("text") or "").strip()
        words = ln.get("words") or []
        rects = [w.get("bounding_rect") or {} for w in words]
        h = max((r.get("height", 0) for r in rects), default=0)
        y = min((r.get("y", 0) for r in rects), default=0)
        if text:
            out.append((text, float(h), float(y)))
    return out


def screen_facts(img=None, lang: str = "en", max_chars: int = 900) -> dict:
    """{'app', 'title', 'headings', 'text'} for the window in front. headings = its biggest text (page or playlist
    names, video titles), in reading order. Private windows give only the app."""
    title = front_title().strip()
    facts = {"app": front_app(), "title": title, "page": page_name(title), "headings": [], "text": ""}
    if PRIVATE.search(title) or img is None:
        return facts
    try:
        src = img.convert("RGB")
        if src.width < 2400:  # browser / app UI text is small: OCR reads it much better bigger
            k = min(2.0, 2400 / max(1, src.width))
            src = src.resize((round(src.width * k), round(src.height * k)))
        lines = [(t, h, y) for t, h, y in ocr_lines(src, lang) if len(t) > 1 and re.search(r"\w{2}", t)]
    except Exception as e:
        print(f"[capri] eyes (ocr): {e}")
        return facts
    sized = sorted(h for _, h, _ in lines if h)
    if sized:
        mid = sized[len(sized) // 2]
        big = [(t, h, y) for t, h, y in lines if h >= max(mid * 1.5, mid + 6)]
        big = sorted(big, key=lambda r: -r[1])[:5]
        facts["headings"] = [t for t, _, _ in sorted(big, key=lambda r: r[2])]
    facts["text"] = " | ".join(t for t, _, _ in lines)[:max_chars]
    return facts


def facts_note(facts: dict, user: str) -> str:
    """The screen facts as a note for her brain: true things to build her answer on."""
    bits = []
    if facts.get("app"):
        bits.append(f"the app in front is {facts['app']}")
    if facts.get("title"):
        bits.append(f'its window title is "{facts["title"]}"')
    if facts.get("page") and facts["page"] != facts.get("title"):
        bits.append(f'the page / tab open in it is "{facts["page"]}"')
    if facts.get("headings"):
        bits.append("the biggest text on it (page / section names): " + ", ".join(f'"{h}"' for h in facts["headings"]))
    if facts.get("text"):
        bits.append(f'text read off it: {facts["text"]}')
    if not bits:
        return ""
    return (f"(Facts read straight off {user}'s screen right now, these are certain: " + "; ".join(bits) +
            ". Use them to say exactly where they are and what they're doing, naming the app and the page, e.g. "
            "\"you're in your Liked Songs in Spotify\". Never invent anything that isn't in these facts or in "
            "what you see.)")
