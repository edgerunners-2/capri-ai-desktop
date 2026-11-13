"""Open stuff on the PC: apps, sites, Google / image / YouTube searches."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import sys
import urllib.parse
import webbrowser

# spoken name -> what to launch (URI schemes work through the Windows shell)
APPS = {
    "spotify": "spotify:",
    "discord": "discord:",
    "steam": "steam:",
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "twitch": "https://www.twitch.tv",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "reddit": "https://www.reddit.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "netflix": "https://www.netflix.com",
    "chatgpt": "https://chatgpt.com",
    "browser": "https://www.google.com",
    "brave": "brave.exe",
    "brave browser": "brave.exe",
    "chrome": "chrome",
    "edge": "msedge",
    "firefox": "firefox",
    "opera": "opera",
    "notepad": "notepad",
    "calculator": "calc",
    "calc": "calc",
    "paint": "mspaint",
    "explorer": "explorer",
    "file explorer": "explorer",
    "files": "explorer",
    "task manager": "taskmgr",
    "settings": "ms-settings:",
    "cmd": "cmd",
    "terminal": "wt",
    "obs": "obs64",
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",
    "minecraft": "minecraft:",
    "epic games": "com.epicgames.launcher:",
    "epic": "com.epicgames.launcher:",
    # the same apps said in her other languages
    "стим": "steam:", "дискорд": "discord:", "спотифай": "spotify:", "ютуб": "https://www.youtube.com",
    "калькулятор": "calc", "блокнот": "notepad", "браузер": "https://www.google.com", "проводник": "explorer",
    "calculadora": "calc", "calculatrice": "calc", "rechner": "calc", "taschenrechner": "calc", "calcolatrice": "calc",
    "kalkulator": "calc", "navegador": "https://www.google.com", "navigateur": "https://www.google.com",
    "configuración": "ms-settings:", "einstellungen": "ms-settings:", "paramètres": "ms-settings:",
    "impostazioni": "ms-settings:", "ustawienia": "ms-settings:", "настройки": "ms-settings:",
    "bloc de notas": "notepad", "bloc-notes": "notepad", "editor": "notepad", "explorador": "explorer",
}


def _browser(url: str, hint: str = "") -> None:
    """Open a URL without blocking her reply (a cold browser start can take a while). The tab is remembered, with
    the words its title will have (the video, the search, the site), so "close the tabs you opened" finds it."""
    if not hint:
        host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
        hint = host.split(".")[0] if host else ""
    import time

    _opened.append({"url": url, "hint": hint, "t": time.time()})
    del _opened[:-20]
    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()


_opened: list[dict] = []  # the tabs she opened this run, newest last: {"url", "hint"}
BROWSERS = {"chrome", "msedge", "brave", "firefox", "opera", "vivaldi", "arc", "zen", "librewolf", "waterfox",
            "chromium", "thorium", "floorp",
            # their Linux process names (cut to 15 letters by the kernel)
            "firefox-esr", "firefox-bin", "vivaldi-bin", "chromium-browse", "brave-browser", "google-chrome", "zen-bin"}


def _browser_windows() -> list[tuple[int, str]]:
    """(hwnd, title) of every open browser window, the one used last first. The title is its active tab's."""
    if sys.platform != "win32":
        from .. import linux

        return [(w, t) for w, prog, t in linux.app_windows() if prog in BROWSERS]
    import ctypes
    from ctypes import wintypes

    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    out = []

    def exe_of(h) -> str:
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        ph = k.OpenProcess(0x1000, False, pid.value)
        if not ph:
            return ""
        try:
            buf, n = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
            ok = k.QueryFullProcessImageNameW(ph, 0, buf, ctypes.byref(n))
            return os.path.splitext(os.path.basename(buf.value))[0].lower() if ok else ""
        finally:
            k.CloseHandle(ph)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(h, _):
        if u.IsWindowVisible(h) and not u.IsIconic(h) and u.GetWindow(h, 4) == 0:  # shown, not minimized, top-level
            buf = ctypes.create_unicode_buffer(512)
            if u.GetWindowTextW(h, buf, 512) and exe_of(h) in BROWSERS:
                out.append((h, buf.value))
        return True

    u.EnumWindows(visit, 0)  # top of the stack first: the browser window you used last
    return out


def _close_active_tab(hwnd: int, title: str) -> bool:
    """Bring that browser window to the front and press Ctrl+W: its active tab closes. -> True when it did."""
    import time

    if sys.platform != "win32":
        from .. import linux

        if not linux.close_tab(str(hwnd)):
            return False
        for _ in range(30):  # closed: the window shows another tab now, or it's gone
            time.sleep(0.05)
            now = dict((w, t) for w, _, t in linux.app_windows())
            if now.get(str(hwnd)) != title:
                return True
        return False
    import ctypes

    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    fg = u.GetForegroundWindow()
    a, b = u.GetWindowThreadProcessId(fg, None), k.GetCurrentThreadId()
    attached = a and a != b and u.AttachThreadInput(b, a, True)  # Windows only hands focus over this way
    try:
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
    finally:
        if attached:
            u.AttachThreadInput(b, a, False)
    for _ in range(25):
        if u.GetForegroundWindow() == hwnd:
            break
        time.sleep(0.02)
    else:
        return False  # never got in front: no keys sent anywhere else
    for vk, up in ((0x11, 0), (0x57, 0), (0x57, 2), (0x11, 2)):  # Ctrl down, W down, W up, Ctrl up
        u.keybd_event(vk, 0, up, 0)
        time.sleep(0.01)
    for _ in range(30):  # closed: the title changed (the next tab) or the window is gone (that was its last tab)
        time.sleep(0.05)
        if not u.IsWindow(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(hwnd, buf, 512)
        if buf.value != title:
            return True
    return False


# windows she never closes: the taskbar and desktop (Windows' own shell), and herself
_SHELL = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW", "NotifyIconOverflowWindow"}
_NEVER = {"capri", "python", "pythonw", "dwm", "csrss", "winlogon", "lockapp", "searchhost", "startmenuexperiencehost",
          "shellexperiencehost", "textinputhost", "systemsettings_broker", "applicationframehost"}


def _app_windows() -> list[tuple[int, str, str]]:
    """(hwnd, exe name, title) of every app window you can see on screen, the one used last first."""
    if sys.platform != "win32":
        from .. import linux

        return [w for w in linux.app_windows() if w[1] and w[1] not in linux._KEEP_ALIVE]
    import ctypes
    from ctypes import wintypes

    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    me = os.getpid()
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(h, _):
        if not u.IsWindowVisible(h) or u.GetWindow(h, 4) != 0:  # hidden, or a dialog owned by another window
            return True
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, cls, 256)
        title = ctypes.create_unicode_buffer(512)
        if cls.value in _SHELL or not u.GetWindowTextW(h, title, 512):
            return True
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == me:
            return True
        ph = k.OpenProcess(0x1000, False, pid.value)
        exe = ""
        if ph:
            buf, n = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
            if k.QueryFullProcessImageNameW(ph, 0, buf, ctypes.byref(n)):
                exe = os.path.splitext(os.path.basename(buf.value))[0].lower()
            k.CloseHandle(ph)
        if exe and exe not in _NEVER:
            out.append((h, exe, title.value))
        return True

    u.EnumWindows(visit, 0)
    return out


def _app_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def find_app_windows(name: str) -> list[tuple[int, str, str]]:
    """The windows of the app you named: 'discord' -> Discord's window, 'vs code' -> code.exe, 'valorant' -> the
    game. By its program first; by window title only for apps that aren't a browser (a "Discord" tab in Chrome is
    a tab, not Discord)."""
    from .vision import APP_NAMES

    want = _app_key(re.sub(r"\b(?:app|application|program|game|client|launcher)\b", "", name.lower()))
    if len(want) < 2:
        return []
    stems = {stem for stem, label in APP_NAMES.items() if label and _app_key(label) == want} | {want}
    if want in ("browser", "internet", "webbrowser"):
        stems |= BROWSERS
    wins = _app_windows()
    hits = [w for w in wins if w[1] in stems or (len(want) >= 4 and want in w[1])]
    if hits:
        return hits
    return [w for w in wins if w[1] not in BROWSERS and len(want) >= 3
            and re.search(rf"\b{re.escape(name.strip())}\b", w[2], re.I)]


def close_app(name: str) -> str:
    """Close an app the way its X button does (WM_CLOSE): it can still ask to save. Never killed by force."""
    import time

    wins = find_app_windows(name)
    if sys.platform != "win32":
        return _close_app_linux(name, wins)
    import ctypes

    if not wins:
        return f"couldn't close {name}: it isn't open"
    u = ctypes.windll.user32
    for h, _, _ in wins:
        u.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
    for _ in range(30):
        time.sleep(0.1)
        if not any(u.IsWindow(h) and u.IsWindowVisible(h) for h, _, _ in wins):
            return f"closed {name}"
    return f"asked {name} to close, but it's still open (it may be asking to save something)"


def _close_app_linux(name: str, wins) -> str:
    """Linux: ask its windows to close (X11, Hyprland, Sway, XWayland apps); on KDE / GNOME Wayland a native app
    that isn't listed gets a polite quit instead (SIGTERM: it saves and closes, like when you log out)."""
    import time

    from .. import linux
    from .vision import APP_NAMES

    if wins:
        for w, _, _ in wins:
            linux.close_window(str(w))
        for _ in range(30):
            time.sleep(0.1)
            left = {str(w) for w, _, _ in linux.app_windows()}
            if not any(str(w) in left for w, _, _ in wins):
                return f"closed {name}"
        return f"asked {name} to close, but it's still open (it may be asking to save something)"
    if linux.desktop() == "wayland":
        want = _app_key(re.sub(r"\b(?:app|application|program|game|client|launcher)\b", "", name.lower()))
        names = {want, name.lower().strip()} | {stem for stem, label in APP_NAMES.items() if label and _app_key(label) == want}
        names |= {n[:15] for n in names}  # the kernel cuts process names to 15 letters ("telegram-deskto")
        if linux.quit_program(names):
            return f"closed {name}"
    return f"couldn't close {name}: it isn't open"


def _tab_name(title: str) -> str:
    """'(3) Kavinsky - Nightcall - YouTube - Google Chrome' -> 'Kavinsky - Nightcall - YouTube'."""
    t = re.sub(r"^\(\d+\)\s*", "", title)
    return re.sub(r"\s+[-—]\s+(?:Google Chrome|Microsoft​?\s*Edge|Brave|Mozilla Firefox|Opera|Vivaldi|Arc|Zen Browser)"
                  r"(?:\s+[-—]\s+.*)?$", "", t, flags=re.I).strip() or title


def close_tabs(which: str = "") -> str:
    """Close browser tabs. which: "" (the tab in front, or the last one she opened), "all" (every tab she opened that
    is still showing), or a site / words in the title ("youtube", "google", "the nightcall video")."""
    import time

    want = re.sub(r"^(?:the|that|this|my|those|these|your)\s+", "", (which or "").lower().strip())
    if want == "it" and not (_opened and time.time() - _opened[-1]["t"] < 900):
        return "didn't close anything: not sure what 'it' is (no tab I opened lately)"
    wins = _browser_windows()
    if not wins:
        return "couldn't find an open browser tab to close"
    closed = []
    if want in ("all", "yours", "every"):
        hints = [o["hint"].lower()[:30] for o in reversed(_opened) if o["hint"]]
        for _ in range(12):
            hit = next(((h, t) for h, t in _browser_windows() for w in hints if w and w in t.lower()), None)
            if hit is None or not _close_active_tab(*hit):
                break
            closed.append(_tab_name(hit[1]))
        if not closed:
            return "couldn't find any of the tabs I opened still open in front (only the tab showing in each window can be closed)"
    else:
        if not want or want in ("it", "tab", "one", "last", "current"):
            last = _opened[-1]["hint"].lower()[:30] if _opened else ""
            hit = next(((h, t) for h, t in wins if last and last in t.lower()), None) or wins[0]
        else:
            words = [w for w in re.findall(r"[\w']+", want) if w not in ("tab", "tabs", "video", "page", "song", "window")]
            hit = next(((h, t) for h, t in wins if words and all(w in t.lower() for w in words)), None)
            if hit is None:
                return f"couldn't find a {want} tab showing in the browser"
        if not _close_active_tab(*hit):
            return "couldn't get the browser in front to close the tab"
        closed.append(_tab_name(hit[1]))
    return "closed " + (f"the tab {closed[0]!r}" if len(closed) == 1 else f"{len(closed)} tabs: " + ", ".join(closed))


def _launch(target: str) -> bool:
    if target.startswith(("http://", "https://")):
        _browser(target)
        return True
    if sys.platform == "win32":
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    if target.endswith(":") or re.match(r"^[a-z][\w.+-]*://", target):  # app links: minecraft: / steam://rungameid/...
        from .. import linux

        return linux.open_path(target)
    name = target.removesuffix(".exe")
    exe = next((e for e in (shutil.which(n) for n in (name, LINUX_NAMES.get(name, ""))) if e), None)
    if exe:
        subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    return False


# Windows program names -> what they're called on Linux (first one installed wins)
LINUX_NAMES = {"chrome": "google-chrome-stable", "msedge": "microsoft-edge-stable", "notepad": "kate",
               "calc": "kcalc", "mspaint": "kolourpaint", "explorer": "dolphin", "taskmgr": "plasma-systemmonitor",
               "cmd": "konsole", "wt": "konsole", "obs64": "obs", "brave": "brave"}


# "peak on steam", "fortnite from epic", "minecraft in the launcher": the store/launcher it lives in
_ON_STORE = re.compile(r"^(?P<what>.+?)\s+(?:on|in|from|via|through|with|using|en|sur|auf|in|na|w|в|через)\s+"
                       r"(?:my\s+|the\s+|mi\s+|mon\s+|meinem\s+)?(?P<store>steam|epic(?: games)?(?: launcher)?|"
                       r"battle\.?net|ea(?: app)?|origin|ubisoft(?: connect)?|uplay|gog(?: galaxy)?|xbox(?: app)?|"
                       r"riot(?: client)?)$", re.I)
_SAY_GAME = re.compile(r"^(?:the\s+)?(?:game|app|program)\s+(?:called\s+|named\s+)?|\s+(?:game|app)$", re.I)


def _clean_name(name: str) -> str:
    key = re.sub(r"\s+", " ", name.lower().strip(" .!?,\"'")).removeprefix("the ").removesuffix(" app").strip()
    return _SAY_GAME.sub("", key).strip() or key


def open_thing(name: str, google_fallback: bool = False) -> str:
    """'spotify' -> opens Spotify; 'peak on steam' -> starts PEAK through Steam. Unknown names are tried as a Steam
    game, a shortcut, a Store app and a program. Asked to OPEN something, she never googles it instead (testers got
    a browser search for "open peak on steam"); only a domain opens in the browser."""
    key = _clean_name(name)
    store = ""
    if m := _ON_STORE.match(key):
        key, store = _clean_name(m.group("what")), m.group("store").lower()
    target = APPS.get(key)
    if target and not store and _launch(target):
        return f"opened {key}"
    if "." in key and " " not in key and not store:  # looks like a domain
        _browser("https://" + key)
        return f"opened {key}"
    if store.startswith("steam") or not store:
        game = find_steam_game(key)
        if game and _launch(f"steam://rungameid/{game[0]}"):
            return f"started {game[1]} on Steam"
    if (lnk := find_shortcut(key)) and _launch(str(lnk)):
        return f"opened {lnk.stem}"
    if (app := find_store_app(key)) and _launch("shell:AppsFolder\\" + app[1]):
        return f"opened {app[0]}"
    if store.startswith("steam"):  # not installed: open it in Steam's own store, never a Google search
        if _launch("steam://openurl/https://store.steampowered.com/search/?term=" + urllib.parse.quote(key)):
            return f"couldn't find {key} in their Steam library, opened it in the Steam store"
    if store:
        launcher = {"epic": "epic games", "xbox": "xbox"}.get(store.split()[0], store.split()[0])
        if (lnk := find_shortcut(launcher)) and _launch(str(lnk)):
            return f"couldn't find {key}, opened {lnk.stem} instead"
        if APPS.get(launcher) and _launch(APPS[launcher]):
            return f"couldn't find {key}, opened {launcher} instead"
    if " " not in key and _launch(key):  # a program on PATH ("notepad", "code"); never a whole sentence
        return f"opened {key}"
    if not google_fallback:
        return f"couldn't find an app or game called {key} on this PC"
    web_search(key)
    return f"couldn't find an app called {key}, googled it instead"


def _steam_roots() -> list:
    """Steam's library folders on this PC (the main install plus every extra library drive)."""
    from pathlib import Path

    homes = []
    if sys.platform == "win32":
        try:
            import winreg

            for hive, key, val in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                   (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                   (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        homes.append(Path(winreg.QueryValueEx(k, val)[0]))
                except OSError:
                    pass
        except ImportError:
            pass
        homes += [Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam",
                  Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Steam"]
    else:
        h = Path.home()
        homes += [h / ".local/share/Steam", h / ".steam/steam", h / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]
    libs, seen = [], set()
    for home in homes:
        vdf = home / "steamapps" / "libraryfolders.vdf"
        found = [home]
        try:
            found += [Path(p.replace("\\\\", "\\")) for p in
                      re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="ignore"))]
        except OSError:
            pass
        for lib in found:
            key = os.path.normcase(str(lib))
            if key not in seen and (lib / "steamapps").is_dir():
                seen.add(key)
                libs.append(lib)
    return libs


def steam_games() -> list[tuple[str, str]]:
    """Every installed Steam game: (appid, name), from the appmanifest files in each library."""
    out = []
    for lib in _steam_roots():
        for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
            try:
                text = acf.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            appid = re.search(r'"appid"\s+"(\d+)"', text)
            name = re.search(r'"name"\s+"([^"]+)"', text)
            if appid and name and not re.search(r"\b(?:redistributable|steamworks|proton|runtime)\b", name.group(1), re.I):
                out.append((appid.group(1), name.group(1)))
    return out


def find_steam_game(name: str) -> tuple[str, str] | None:
    """'peak' -> ('3527290', 'PEAK'); 'cs2' / 'counter strike' -> Counter-Strike 2. None when it isn't installed."""
    want = re.sub(r"[^\w]+", " ", name.lower()).strip()
    if len(want) < 2:
        return None
    best, score = None, 0.0
    for appid, title in steam_games():
        label = re.sub(r"[^\w]+", " ", title.lower()).strip()
        initials = "".join(w[0] for w in label.split() if w and not w.isdigit()) + "".join(
            w for w in label.split() if w.isdigit())
        if label == want:
            s = 3.0
        elif want == initials or want.replace(" ", "") == label.replace(" ", ""):
            s = 2.5
        elif re.search(rf"\b{re.escape(want)}\b", label):
            s = 2.0 - len(label) / 200
        elif label.startswith(want):
            s = 1.5 - len(label) / 200
        else:
            continue
        if s > score:
            best, score = (appid, title), s
    return best


# games people "play" (not songs): "play valorant" opens it (or she says it isn't installed), never a song search
KNOWN_GAMES = {
    "valorant", "fortnite", "minecraft", "roblox", "league of legends", "cs2", "csgo", "counter strike",
    "counter strike 2", "apex", "apex legends", "overwatch", "overwatch 2", "rocket league", "genshin", "genshin impact",
    "among us", "geoguessr", "elden ring", "gta", "gta 5", "gta v", "rainbow six siege", "r6", "dota", "dota 2", "pubg",
    "warzone", "call of duty", "terraria", "stardew valley", "peak", "repo", "lethal company", "phasmophobia", "osu",
    "marvel rivals", "deadlock", "the finals", "palworld", "hollow knight", "silksong", "cyberpunk 2077", "fall guys",
    "rust", "dead by daylight", "brawlhalla", "paladins", "smite", "hearthstone", "wow", "world of warcraft",
}


def game_named(name: str) -> bool:
    """Is this the name of a game ("valorant", "cs2", "peak", a game in your Steam library or with a Start Menu
    shortcut), said exactly, not a song that happens to share a word with one?"""
    want = re.sub(r"[^\w]+", " ", (name or "").lower()).strip()
    if len(want) < 2:
        return False
    if want in KNOWN_GAMES:
        return True
    hit = find_steam_game(want)
    if hit:
        label = re.sub(r"[^\w]+", " ", hit[1].lower()).strip()
        initials = "".join(w[0] for w in label.split() if w and not w.isdigit()) + "".join(
            w for w in label.split() if w.isdigit())
        if want in (label, initials) or want.replace(" ", "") == label.replace(" ", ""):
            return True
    if want in APPS:  # an app ("spotify", "discord") isn't a game to play
        return False
    lnk = find_shortcut(want)
    return lnk is not None and re.sub(r"[^a-z0-9]+", " ", lnk.stem.lower()).strip() == want


def find_shortcut(name: str):
    """Any installed app by the name you'd say: the best-matching Start Menu or desktop shortcut ('whatsapp' ->
    WhatsApp.lnk, 'valorant' -> VALORANT.lnk, 'premiere' -> Adobe Premiere Pro 2025.lnk). None if nothing fits."""
    if sys.platform != "win32":
        return None
    from pathlib import Path

    want = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    if len(want) < 2:
        return None
    roots = [Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
             Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
             Path.home() / "Desktop", Path.home() / "OneDrive/Desktop", Path(os.environ.get("PUBLIC", "")) / "Desktop"]
    best, score = None, 0.0
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if p.suffix.lower() not in (".lnk", ".url", ".appref-ms"):
                continue
            label = re.sub(r"[^a-z0-9]+", " ", p.stem.lower()).strip()
            if re.search(r"\b(?:uninstall|readme|help|manual|website|documentation|release notes)\b", label):
                continue
            if label == want:
                s = 3.0
            elif re.search(rf"\b{re.escape(want)}\b", label):
                s = 2.0 - len(label) / 200  # 'discord' in 'discord ptb' beats 'discord bot maker manual'
            elif label.replace(" ", "").startswith(want.replace(" ", "")):
                s = 1.5 - len(label) / 200
            else:
                continue
            if s > score:
                best, score = p, s
    return best


_start_apps: list[tuple[str, str]] | None = None


def find_store_app(name: str) -> tuple[str, str] | None:
    """Microsoft Store apps (WhatsApp, Xbox, Photos...) have no shortcut file: Windows' own Start list has them.
    -> (name, AppID), launched through shell:AppsFolder. The list is read once (about a second)."""
    global _start_apps
    if sys.platform != "win32":
        return None
    if _start_apps is None:
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "Get-StartApps | ForEach-Object { $_.Name + \"`t\" + $_.AppID }"],
                                 capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=15,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            _start_apps = [tuple(line.split("\t", 1)) for line in out.splitlines() if "\t" in line]
        except Exception as e:
            print(f"[capri] couldn't list Start apps: {e}")
            return None
    want = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    hits = [(n, a) for n, a in _start_apps if re.search(rf"\b{re.escape(want)}\b", re.sub(r"[^a-z0-9]+", " ", n.lower()))
            and not re.search(r"\buninstall\b", n, re.I)]
    return min(hits, key=lambda h: len(h[0])) if hits else None


_yt = None


def warm_youtube() -> None:
    """Open the connection to YouTube ahead of time, so the first "play X" doesn't pay for the handshake."""
    global _yt
    try:
        import requests

        _yt = requests.Session()
        _yt.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en"})
        _yt.get("https://www.youtube.com/results", params={"search_query": "cyberpunk"}, timeout=6, stream=True).close()
    except Exception as e:
        print(f"[capri] youtube warm-up failed: {e}")


def youtube_play(query: str) -> str:
    """Open the top YouTube result for `query` straight away (falls back to the results page). Reads the results
    page as a stream and stops as soon as the first video and its title have arrived."""
    import re

    global _yt
    try:
        import requests

        if _yt is None:  # one kept-alive connection: no new TLS handshake per song
            _yt = requests.Session()
            _yt.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en"})
        found = re.compile(r'"videoRenderer":\{"videoId":"([\w-]{11})".*?"title":\{"runs":\[\{"text":"(.*?)"\}', re.S)
        buf = ""
        with _yt.get("https://www.youtube.com/results", params={"search_query": query}, timeout=6, stream=True) as r:
            raw = b""
            for chunk in r.iter_content(65536):  # raw bytes: no slow charset sniffing
                raw += chunk
                start = raw.find(b'"videoRenderer"')
                if start >= 0 and raw.find(b'"title"', start) >= 0 and found.search(raw[start:].decode("utf-8", "ignore")):
                    break
            buf = raw.decode("utf-8", "ignore")
        m = found.search(buf)
        if m:
            import json

            try:
                title = json.loads('"' + m.group(2) + '"')  # JSON escapes like &, without mangling accents
            except ValueError:
                title = m.group(2)
            _browser(f"https://www.youtube.com/watch?v={m.group(1)}", hint=title)  # the tab will be titled that
            return f"playing {title} on YouTube"
    except Exception as e:
        print(f"[capri] youtube lookup failed: {e}")
    return web_search(query, "youtube")


def web_search(query: str, kind: str = "web") -> str:
    q = urllib.parse.quote_plus(query)
    url = {
        "images": f"https://www.google.com/search?tbm=isch&q={q}",
        "youtube": f"https://www.youtube.com/results?search_query={q}",
        "web": f"https://www.google.com/search?q={q}",
    }[kind]
    _browser(url, hint=query)
    label = {"images": "pictures of", "youtube": "YouTube for", "web": "Google for"}[kind]
    return f"opened {label} {query}"
