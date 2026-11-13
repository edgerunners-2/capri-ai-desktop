"""Spotify control.

She finds the exact track and opens it in the Spotify app, which starts playing it (Free accounts too); the web
player when the app isn't installed. The finding is done by Spotify's own search when client_id/secret are set (a free
developer app, no Premium needed), else by a quick web lookup of its Spotify link. With spotify.premium: true as well,
she drives playback through the Web API instead. Pause/skip go through media keys.
"""
from __future__ import annotations

import os
import re
import sys
import urllib.parse
import urllib.request

SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"


class Spotify:
    def __init__(self, cfg: dict):
        self.cfg = cfg["spotify"]
        self.sp = None  # full remote control (Premium only): spotify.premium: true plus the keys
        self.lookup = None  # search only (any account, Free too): finds the exact track, the app plays it
        if self.cfg.get("client_id") and self.cfg.get("client_secret"):
            try:
                import spotipy
                from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

                from ..config import DATA

                self.lookup = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
                    client_id=self.cfg["client_id"], client_secret=self.cfg["client_secret"]), requests_timeout=8)
                if self.cfg.get("premium"):
                    self.sp = spotipy.Spotify(
                        auth_manager=SpotifyOAuth(
                            client_id=self.cfg["client_id"],
                            client_secret=self.cfg["client_secret"],
                            redirect_uri=self.cfg["redirect_uri"],
                            scope=SCOPES,
                            cache_path=str(DATA / ".spotify_token"),
                        )
                    )
            except Exception as e:
                print(f"[capri] spotify api unavailable, using fallback: {e}")

    # -- api path ---------------------------------------------------------
    def _device(self):
        devices = self.sp.devices().get("devices", [])
        active = [d for d in devices if d.get("is_active")]
        return (active or devices or [{}])[0].get("id")

    def _find(self, query: str) -> tuple[str | None, str]:
        """-> ('track/<id>' or a playlist/artist, 'Song by Artist'). Spotify's own search when the keys are set (exact,
        and an artist's name gives their top song), else a web lookup."""
        if self.lookup:
            try:
                q = query.strip()
                kind = "playlist" if "playlist" in q.lower() else "track"
                items = self.lookup.search(q=q.replace("playlist", "").strip(), type=kind, limit=1)[kind + "s"]["items"]
                if items:
                    it = items[0]
                    name = it["name"] + (f" by {it['artists'][0]['name']}" if kind == "track" else "")
                    return f"{kind}/{it['id']}", name
            except Exception as e:
                print(f"[capri] spotify search failed: {e}")
        track = find_track(query) or find_track(query + " song")
        if track and not track.startswith("track/"):
            # an artist / album page just sits there: one of their songs actually starts playing
            track = find_track(query + " popular song", kinds=("track",)) or track
        return track, ""

    def play(self, query: str) -> str:
        if self.sp:
            try:
                q = query.strip()
                kind = "playlist" if "playlist" in q.lower() else "track"
                res = self.sp.search(q=q.replace("playlist", "").strip(), type=kind, limit=1)
                items = res[kind + "s"]["items"]
                if not items:
                    return f"nothing on Spotify for '{q}'"
                item = items[0]
                if kind == "playlist":
                    self.sp.start_playback(device_id=self._device(), context_uri=item["uri"])
                    return f"playing playlist {item['name']}"
                self.sp.start_playback(device_id=self._device(), uris=[item["uri"]])
                return f"playing {item['name']} by {item['artists'][0]['name']}"
            except Exception as e:
                print(f"[capri] spotify remote failed ({e}); opening it in the app instead")
        import threading

        def go():  # the lookup takes a second or two: she answers meanwhile
            track, name = self._find(query)
            app = has_app()
            if track:  # opening a track in the Spotify app starts playing it (Free accounts too)
                _open("spotify:" + track.replace("/", ":") if app else f"https://open.spotify.com/{track}")
            else:
                q = urllib.parse.quote(query)
                _open(f"spotify:search:{q}" if app else f"https://open.spotify.com/search/{q}")
            print(f"[capri] spotify: {name or track or 'search'} for {query} ({'app' if app else 'web'})")

        threading.Thread(target=go, daemon=True).start()
        return f"opening {query} on Spotify"

    def control(self, action: str) -> str:
        action = action.strip().lower()
        if self.sp:
            try:
                {
                    "pause": lambda: self.sp.pause_playback(),
                    "resume": lambda: self.sp.start_playback(),
                    "next": lambda: self.sp.next_track(),
                    "previous": lambda: self.sp.previous_track(),
                }[action]()
                return f"spotify {action}"
            except Exception as e:
                return f"spotify error: {e}"
        return _media_key(action)

    def now_playing(self) -> str | None:
        if not self.sp:
            return None
        try:
            cur = self.sp.current_playback()
            if cur and cur.get("item"):
                return f"{cur['item']['name']} by {cur['item']['artists'][0]['name']}"
        except Exception:
            pass
        return None


def find_track(query: str, kinds=("track", "artist", "album", "playlist")) -> str | None:
    """'Symphony Clean Bandit' -> 'track/<id>' ('artist/<id>' for an artist), from the first search hit that links one.
    None if nothing is found or it's offline."""
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(f"spotify track {query}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                                             "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"})
    try:
        page = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"[capri] spotify lookup failed: {e}")
        return None
    page = urllib.parse.unquote(page)
    # a song name finds its track; a bare artist name ("Ado") finds the artist page, top songs and a play button
    for kind in kinds:
        if m := re.search(rf"open\.spotify\.com(?:/intl-[\w-]+)?/{kind}/([A-Za-z0-9]{{22}})", page):
            return f"{kind}/{m.group(1)}"
    return None


def has_app() -> bool:
    """Is the Spotify desktop app there to take spotify: links? (Uninstalling it leaves an empty handler behind.)"""
    if sys.platform != "win32":
        import shutil

        return bool(shutil.which("spotify") or shutil.which("spotify-launcher"))
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_CLASSES_ROOT):
        try:
            with winreg.OpenKey(hive, r"Software\Classes\spotify\shell\open\command" if hive == winreg.HKEY_CURRENT_USER
                                else r"spotify\shell\open\command") as k:
                if winreg.QueryValue(k, None).strip():
                    return True
        except OSError:
            pass
    return False


def _open(uri: str) -> None:
    """Fire and forget: launching Spotify cold can block ShellExecute for ages, and she shouldn't freeze."""
    import threading

    def go():
        try:
            if sys.platform == "win32":
                os.startfile(uri)  # type: ignore[attr-defined]
            else:
                from .. import linux

                if not linux.open_path(uri):
                    import webbrowser

                    webbrowser.open(uri)
        except Exception as e:
            print(f"[capri] couldn't open {uri}: {e}")

    threading.Thread(target=go, daemon=True).start()


def _media_key(action: str) -> str:
    if sys.platform != "win32":
        from .. import linux

        return linux.media(action)
    import ctypes

    vk = {"pause": 0xB3, "resume": 0xB3, "next": 0xB0, "previous": 0xB1}.get(action)
    if vk is None:
        return f"unknown action {action}"
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
    return f"sent {action}"
