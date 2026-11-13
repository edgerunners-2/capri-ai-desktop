"""Wires Capri on the desktop to her brain (Mistral-Nemo + memory), ears (faster-whisper), eyes (window + OCR),
music (YouTube DJ + a beat ear on your speakers). She talks in her speech bubble with blip sounds."""
from __future__ import annotations

import os
import random
import re
import sys
import threading
from pathlib import Path
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication
from PySide6.QtWidgets import QApplication

from .. import config, i18n, skins
from .capri import Capri
from .settings import Settings

CREDITS = (  # her right-click menu > Credits; same as the README
    ("Art && menu UI: xilia_world00", ""),
    ("LLM training: ghasty3", "https://github.com/ghasty3"),
    ("Code: yume_mud", "https://github.com/Capri-cmd"),
    ("Brain: llama.cpp (MIT)", "https://github.com/ggml-org/llama.cpp"),
    ("Ears: whisper.cpp (MIT)", "https://github.com/ggml-org/whisper.cpp"),
    ("Ears: Qwen3-ASR (Apache 2.0)", "https://huggingface.co/Qwen/Qwen3-ASR-0.6B"),
    ("Clips: FFmpeg", "https://ffmpeg.org"),
    ("Improved using Qwen", "https://huggingface.co/Qwen/Qwen3.8-Flash-Next"),
    ("Capri on GitHub", "https://github.com/Capri-cmd/Capri-AI"),
)
DONATE = "https://linktr.ee/capriai"  # her menu > Donate

class Bridge(QObject):
    """Thread-safe hop from worker threads (brain, ears, voice) back onto the Qt thread."""

    replied = Signal(str, str, bool)  # her text, what the user said ("" = her own idea), go nap afterwards
    part = Signal(str, str, bool)  # one sentence of a reply still being written, what the user said, first sentence?
    part_end = Signal(bool)  # that reply is finished (go nap afterwards?)
    started = Signal(object, float)  # line token, seconds of audio
    ended = Signal(object)
    heard = Signal(str)
    hearing = Signal(bool)
    level = Signal(float)
    fetched = Signal(object)  # a download finished: run this on the Qt thread


def idle_seconds() -> float:
    """Seconds since the last keyboard / mouse input anywhere on Windows."""
    if sys.platform != "win32":
        from .. import linux

        return linux.idle_seconds() if sys.platform.startswith("linux") else 0.0
    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    li = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0


def _open_folder(path):
    if sys.platform == "win32":
        os.startfile(path)
    else:
        from .. import linux

        linux.open_path(path)


def _alike(a: str, b: str) -> bool:
    """Two OCR reads of the same text (they differ by a letter here and there)."""
    import difflib

    if not a or not b:
        return False
    a, b = re.sub(r"\W+", "", a.lower()), re.sub(r"\W+", "", b.lower())
    return difflib.SequenceMatcher(None, a, b).ratio() > 0.85


BATTERY_LOW = ["Low battery, twin. Plug me in or we're both going dark.",
               "Battery's at {pct}%. Going mini to save juice, plug in.", "{pct}% left, choom. Find a charger before I flatline."]
BATTERY_BACK = ["Ahh, power. Back to full size.", "Charging. Much better, twin."]


class DeskApp:
    def __init__(self, video: bool = False):
        self.cfg = config.load()
        d = self.cfg["desk"]
        self.video_mode = video
        self.s = Settings(self.cfg)
        i18n.set_lang(self.s.get("ui_lang", "auto"))  # the menu speaks their language (Auto follows the system)
        self.bridge = Bridge()
        size = int(d["video_size"] if video else (self.s["size"] or d["size"]))  # bigger for the camera
        self.dev = self.cfg.get("edition") == "dev"  # the portable test build for other devs
        self.capri = Capri(size, tuple(d["roam_seconds"]), intro=bool(d["intro"]) and not video, start=not video,
                           skin=self.s["skin"])
        self.capri.blips = True  # she talks in her speech bubble with blip sounds
        self.capri.lay_ok = bool(self.s["lay"])  # her mini form lies on the taskbar / window tops
        self.capri.on_full = self._left_mini
        self.capri.on_user = self.on_user
        self.capri.quit_fn = self.quit
        self.on_quit: list = []
        b = self.bridge
        b.replied.connect(self._replied)
        b.part.connect(self._part)
        b.part_end.connect(self._part_end)
        self.nap_after = False
        b.heard.connect(self.capri.heard)
        b.hearing.connect(self._hearing)
        b.fetched.connect(lambda then: then())
        self.dl = None  # what's downloading right now, shown at the top of her Brain menu ("Getting X: 40%")
        self.dl_action = None
        b.level.connect(lambda v: setattr(self.capri.bubble, "mic_level", v))
        self.agent = None
        self.busy = threading.Lock()
        self.ears = None
        self.beat = None
        if video:  # the video is scripted: no brain, ears or eyes needed
            return
        self.booted = False
        self.swapping = False  # a brain swap is running: her Brain menu is locked until it's done
        self._size_lock = 0.0  # her Size menu is locked until this moment (a resize is landing)
        if self.capri.can_talk:  # the watcher needs no brain or ears: they start if you switch her back
            self._wake_up()
        QTimer.singleShot(5000, self._start_beat)  # after boot: starting it mid-startup raced numpy imports and crashed
        self._build_menu()
        # what she keeps an eye on
        self.title = ""
        self.last_own = time.monotonic()  # last time she said something unprompted
        self.last_talk = time.monotonic()  # last time you two talked
        self.idle_pinged = False
        self.song = ""  # YouTube song in the browser
        self.song_since = 0.0
        self.song_judged = ""
        self.her_pick = ""  # what she put on (DJ mode), so she doesn't judge her own pick
        self.last_glance = 0.0
        from ..abilities.music import Playlist

        self.playlist = Playlist()
        # game mode, focus nagging, and reacting to videos / chats / what you type
        self.game = None  # (game, since) while you're in one
        self.react_video, self.video_since, self.video_done = "", 0.0, True
        self.last_react = 0.0  # her last reaction to a video / chat (they have their own pace)
        self.last_nag, self.nag_due = 0.0, False
        self.last_read, self.reading = 0.0, False
        self.chat_last, self.draft_prev, self.draft_said = "", "", ""
        self.react_next = None  # (draft, make line): what the chat reader wants her to say next
        self.hello_due, self.video_roll = False, False
        self.watch = QTimer()
        self.watch.timeout.connect(self._watch)
        self.watch.start(3000)
        if not self.s["lang_set"]:
            QTimer.singleShot(2500, self._ask_language)  # after her entrance, not over it
        if self.s["always_mini"]:
            self._mini_tries = 0
            QTimer.singleShot(3000, self._start_mini)
        QTimer.singleShot(45000, self._ears_check)
        self._batt_warned = self._batt_mini = False
        self.batt = QTimer()
        self.batt.timeout.connect(self._battery)
        self.batt.start(30000)
        QTimer.singleShot(8000, self._battery)

    # -- boot ---------------------------------------------------------------------
    def _wake_up(self):
        if not self.booted:
            self.booted = True
            threading.Thread(target=self._boot, daemon=True).start()
            self._start_ears()

    def set_skin(self, skin_id: str):
        """A new look, saved and live. The watcher's ears stay muted; leaving her brings brain and ears back."""
        skin = skins.get(skin_id)
        self.s.set("skin", skin.id)
        if skin.secret and skin.id not in self.s["skins_seen"]:
            self.s.set("skins_seen", self.s["skins_seen"] + [skin.id])
        self.capri.set_skin(skin.id)
        if self.ears:
            self.ears.muted = not skin.can_talk
        if skin.can_talk:
            self._wake_up()

    def _boot(self):
        from ..brain import llm as llm_mod
        from ..brain.agent import Agent

        # her brain server crashed and is starting again: she says so instead of going silent for a minute
        llm_mod.ON_REVIVE = lambda: self.bridge.replied.emit(
            "Hold up, my brain chip glitched. Rebooting it, gimme a sec.", "", False)

        t = time.monotonic()
        brain = self.s["brain"]  # picked from her Brain menu
        if brain:
            self.cfg["llm"]["model_path"] = brain
        self.cfg.setdefault("claude_code", {})["smart"] = bool(self.s["cc_smart"])
        self.agent = Agent(self.cfg)
        self.agent.on_played = self._played
        self.agent.clip = self._clip  # "capri, clip that"
        if self.s["clips"]:
            self._start_clips()
        try:  # her tools, open to Claude Code in smart mode (127.0.0.1 only, with a secret token)
            from ..brain.toolbridge import ToolBridge

            self.toolbridge = ToolBridge(self.agent)
        except Exception as e:
            print(f"[capri] tool bridge failed: {e}")
        # her own updates: lines while one downloads, and closing herself to run the installer
        self.agent.on_update_line = lambda text: self.bridge.replied.emit(text, "", False)
        self.agent.on_line = lambda text: self.bridge.replied.emit(text, "", False)
        self.agent.on_mini = lambda: self.bridge.fetched.emit(self.capri.go_mini)  # "go mini" by voice
        self.agent.on_lay = lambda: self.bridge.fetched.emit(self.capri.lie_down)  # "lie down"
        self.agent.on_install = lambda path: self.bridge.fetched.emit(lambda: self._install_update(path))
        threading.Thread(target=self._update_notice, daemon=True).start()
        self.agent.settings = self.s  # "leave me alone", never-look apps, focus mode, city... change them by voice
        from ..abilities import screen

        screen.never_look = list(self.s["never_look"])
        from ..abilities.reminders import describe

        # a timer / alarm / reminder going off: she wakes up and says it (the alarm sound is already ringing)
        self.agent.on_reminder = lambda item: self.bridge.replied.emit(describe(item), "", False)
        self.agent.reminders.start()
        from ..abilities.launcher import warm_youtube

        threading.Thread(target=warm_youtube, daemon=True).start()
        if sys.platform.startswith("linux"):  # her eyes on KDE Wayland: teach KWin to report the active window
            from .. import linux

            threading.Thread(target=linux.ensure_watch, daemon=True).start()
        try:
            self.agent.warm_up()
        except Exception as e:
            print(f"[capri] warm-up failed: {e}")
        print(f"[capri] brain online in {time.monotonic() - t:.1f}s")
        if (self.cfg["llm"].get("model_path") or "").lower() == "none" and not self.s["my_brains"]:
            from .. import portable

            if not portable.brains():  # first run: the brain that fits this PC (or: she's a visual companion here)
                best, _ = portable.best_brain()
                line = (i18n.t("lines.brain.best", name=best.label.split("  (")[0]) if best
                        else i18n.t("lines.brain.weak"))
                self.bridge.replied.emit(line, "", False)
        self._claude_notice()
        sight_dir = None if self.dev else self.cfg.get("sight", {}).get("model_dir")
        if self._brain_eyes():  # her brain came with eyes (an mmproj, or Claude Code): no second model needed
            sight_dir = None
        if sight_dir:  # her real eyes load after the brain, so they only take what VRAM is left
            from ..abilities.sight import Eyes
            from ..brain.llm import GPU_LOCK

            for _ in range(120):  # the ears import the same libraries: loading both at once broke transformers' imports
                if self.ears is None or self.ears.stt is not None or not self.ears.is_alive():
                    break
                time.sleep(0.5)
            eyes = Eyes(sight_dir, GPU_LOCK)
            eyes.load()
            if not eyes.ok and "ImportError" in (eyes.error or ""):
                time.sleep(3)
                eyes = Eyes(sight_dir, GPU_LOCK)
                eyes.load()
            self.agent.eyes = eyes

    def _brain_eyes(self) -> bool:
        """Her eyes follow her brain: one that can see screenshots looks through itself, and swapping away from it
        doesn't leave her looking through a brain that's gone. -> True when the brain sees."""
        from ..abilities.sight import BrainEyes

        if getattr(self.agent.llm, "vision", False):
            self.agent.eyes = BrainEyes(self.agent.llm)
            return True
        if isinstance(self.agent.eyes, BrainEyes):
            self.agent.eyes = None
        return False

    def _claude_notice(self):
        """Claude Code is on this PC and isn't her brain: she mentions once that it can be."""
        from ..brain import claude_code

        if self.s["claude_told"] or self.cfg["llm"].get("model_path") == claude_code.ID:
            return
        if claude_code.find_cli(self.cfg):
            self.s.set("claude_told", True)
            self.bridge.replied.emit("Psst, I spotted Claude Code on your PC. Want it as my brain? It can even work "
                                     "your PC for u. Right-click me, Brain, Claude Code smart mode.", "", False)

    def _played(self, result: str):
        if result.startswith("playing"):
            self.her_pick = result[len("playing "):].rsplit(" on YouTube", 1)[0]

    def _start_ears(self):
        try:
            from ..voice.stt import Listener

            if self.s["whisper_model"]:
                self.cfg["stt"]["whisper_model"] = self.s["whisper_model"]
            self.ears = Listener(self.cfg, self.s, self.bridge.heard.emit, self.bridge.hearing.emit, self.bridge.level.emit)
            self.ears.start()
        except Exception as e:
            print(f"[capri] ears unavailable: {e}")

    def _start_beat(self):
        try:
            from ..abilities.music import BeatEar

            self.beat = BeatEar()
            self.beat.start()
            # music = a YouTube song in the browser, or a steady beat (not game explosions)
            music = lambda: self.beat.playing and (bool(getattr(self, "song", "")) or self.beat.rhythmic)  # noqa: E731
            self.capri.beat = lambda: (self.beat.pulse, music()) if self.s["vibe"] else (0.0, False)
            self.capri.sfx.quiet = music  # no booms or zaps over your songs
        except Exception as e:
            print(f"[capri] beat ear unavailable: {e}")

    def _hearing(self, on: bool):
        self.capri.listening(on)
        if on:
            self.last_talk = time.monotonic()

    # -- brain --------------------------------------------------------------------
    def on_user(self, line: str):
        if not self.capri.can_talk:
            return
        self.last_talk = time.monotonic()
        if self.agent is None:
            self.capri.say(i18n.t("lines.booting"), user=line)
            return
        if not self.busy.acquire(blocking=False):
            return
        print(f"[capri] you: {line}")

        def work():
            t = time.monotonic()
            try:
                first = [True]

                def sentence(s):
                    if first[0]:
                        print(f"[capri] first words after {time.monotonic() - t:.2f}s")
                    self.bridge.part.emit(s, line, first[0])
                    first[0] = False

                streaming = self.capri.blips  # a real voice needs the whole line before it can speak
                reply = self.agent.respond(line, on_sentence=sentence if streaming else None)
                for a in reply.actions:
                    print(f"[capri] {a}")
                print(f"[capri] capri ({time.monotonic() - t:.1f}s): {reply.text}")
                if streaming and not first[0]:
                    self.bridge.part_end.emit(reply.go_idle)
                else:
                    self.bridge.replied.emit(reply.text, line, reply.go_idle)
                threading.Thread(target=self.agent.learn, args=(line,), daemon=True).start()
                if self.ears is not None:  # her ears lean on the language you're talking in for short lines
                    self.ears.hint = self.agent._recent_language()
            except Exception as e:
                import traceback

                traceback.print_exc()  # the details go to data\\capri.log; she says it in character
                try:
                    oops = self.agent.trouble(line, e)
                except Exception:
                    oops = "Something glitched in my code, choom. Say that again?"
                self.bridge.replied.emit(oops, line, False)
            finally:
                self.busy.release()

        threading.Thread(target=work, daemon=True).start()

    def _replied(self, text: str, line: str, nap: bool):
        if not self.capri.can_talk:  # a reply or reminder already on its way when she switched
            return
        if self.ears:
            self.ears.muted = True  # auto mode: don't transcribe her own blips

        def done():
            if self.ears:
                self.ears.muted = False
            if nap:
                self.capri.nap()

        self.capri.say(text, user=line, done=done)

    def _part(self, text: str, line: str, first: bool):
        if not self.capri.can_talk:
            return
        if not first:
            self.capri.say_more(text)
            return
        if self.ears:
            self.ears.muted = True
        self.nap_after = False

        def done():
            if self.ears:
                self.ears.muted = False
            if self.nap_after:
                self.capri.nap()

        self.capri.say(text, user=line, done=done, more=True)

    def _part_end(self, nap: bool):
        self.nap_after = nap
        self.capri.say_end()

    def _own(self, make, then=None) -> bool:
        """Something she brings up herself: think in the background, say it if it's still a good moment.
        -> False when it isn't one (she's busy, napping, mid-teleport), so the caller can try again later."""
        if self.agent is None or not self.capri.can_talk or self.capri.mode not in ("stand", "perch") or self.capri.speaking or self.capri.bubble.isVisible():
            return False
        if not self.busy.acquire(blocking=False):
            return False
        self.last_own = time.monotonic()

        def work():
            try:
                text = make()
                if text:
                    print(f"[capri] capri (own): {text}")
                    self.bridge.replied.emit(text, "", False)
                if then:
                    then()
            except Exception as e:
                print(f"[capri] own line failed: {e}")
            finally:
                self.busy.release()

        threading.Thread(target=work, daemon=True).start()
        return True

    # -- eyes, chatter and the DJ (every 3 s) --------------------------------------------------
    def _watch(self):
        from ..abilities import vision
        from ..abilities.music import CYBER

        if self.agent is None or not self.capri.can_talk:  # the watcher doesn't read your screen either
            return
        d = self.cfg["desk"]
        now = time.monotonic()
        cooldown = d["react_cooldown_minutes"] * 60
        idle = idle_seconds()

        # what's playing on YouTube (any window, even behind others)
        song = vision.youtube_song(vision.window_titles())
        self.agent.now_playing = song
        if song != self.song:
            self.song, self.song_since = song, now
        playing = song and (self.beat is None or self.beat.playing)

        # her eyes: a fresh glance when you switch windows (and every 30 s anyway)
        from ..abilities import screen

        title = vision.active_window_title().strip()
        own_window = not title or title in ("Capri", "Capri CMD") or "Program Manager" in title
        switched = title != self.title and not own_window
        if switched:
            self.title = title
        exe = screen.foreground_exe() if not own_window else ""
        kind, name = screen.kind(title, exe) if not own_window else ("other", "")
        quiet = self.s["quiet"]  # "leave me alone": no reading your screen, no talking on her own
        hidden = quiet or screen.private(title, exe) or (kind == "game" and self.s["game_mode"])
        if self.s["eyes"] and not own_window and not hidden and (switched or now - self.last_glance > 30):
            self.last_glance = now

            def look():
                t, text = vision.glance()
                self.agent.glance = {"t": time.time(), "title": t, "text": text}

            threading.Thread(target=look, daemon=True).start()

        if quiet or getattr(self, "video", None) is not None:  # a scripted video records: it does the talking
            return
        # focus mode: quiet, except to send you back to work when you wander off
        focus_left = self.s["focus_until"] - time.time()
        if focus_left > 0:
            # the moment you open one, then every 3 minutes you stay on it
            if kind in ("youtube", "distracting", "game", "chat"):
                if switched:
                    self.nag_due = True
                if self.nag_due or now - self.last_nag > 180:
                    if self.capri.mode == "chip":
                        self.capri.wake_chip()  # she wakes up to catch you
                        return
                    where, left = name or title, int(focus_left // 60) + 1
                    if self._own(lambda: self.agent.focus_nag(where, left)):
                        self.last_nag, self.nag_due = now, False
            else:
                self.nag_due = False
            return
        # game mode: not a word while you play; when you stop, she asks how it went
        if kind == "game" and self.s["game_mode"]:
            if self.game is None or self.game[0] != name:
                self.game = (name, now)
            return
        if self.game and not own_window:
            game, since = self.game
            self.game = None
            mins = int((now - since) // 60)
            if mins >= 3 and self.s["chatter"]:
                QTimer.singleShot(4000, lambda: self._own(lambda: self.agent.game_over(game, mins)))
                return
        if self.s["chatter"] and self.s["reactions"] and not screen.private(title, exe):
            if self._react(kind, name, title, exe, switched, now):
                return

        # DJ: judge a new song once it's been on a bit
        if playing and song != self.song_judged and now - self.song_since > 12:
            self.song_judged = song
            mine = self.her_pick and _same_song(self.her_pick, song)
            if mine:
                if random.random() < 0.6:
                    self._own(lambda: self.agent.hype_line(song))
                return
            if self.s["dj"] and not self.dev and not CYBER.search(song):
                pick = self.playlist.next()

                def swap():
                    from ..abilities import launcher
                    from ..abilities.spotify import _media_key

                    _media_key("pause")
                    time.sleep(0.4)
                    result = launcher.youtube_play(pick)
                    print(f"[capri] DJ: {result}")
                    if result.startswith("playing"):
                        self.her_pick = result[len("playing "):].rsplit(" on YouTube", 1)[0]

                self._own(lambda: self.agent.music_take(song, switching_to=pick.split(" (")[0]), then=swap)
                return
            if now - self.last_own > cooldown and random.random() < 0.5:
                self._own(lambda: self.agent.music_take(song))
                return

        if not self.s["chatter"]:
            return
        # you've been gone a while
        if idle > d["chatter_idle_minutes"] * 60:
            if not self.idle_pinged and now - self.last_own > cooldown:
                self.idle_pinged = True
                self._own(lambda: self.agent.idle_line(int(idle // 60)))
            return
        self.idle_pinged = False
        # you just opened something new (videos and chats have their own reactions)
        if switched and kind not in ("youtube", "chat", "game") and now - self.last_own > cooldown \
                and now - self.last_talk > 60 and random.random() < 0.5:
            QTimer.singleShot(2500, lambda: self._own(lambda: self.agent.react_line(title)))  # after the glance lands

    def _react(self, kind: str, name: str, title: str, exe: str, switched: bool, now: float) -> bool:
        """Reacting like a friend next to you: the YouTube video you're watching (and its comments once you scroll
        to them), the chat you're in, and what you're typing in it. -> True when she's busy with one.
        A reaction only counts (cooldown, "already said") once she actually says it."""
        from ..abilities import screen

        if self.capri.mode == "chip" and (self.react_next is not None or self.hello_due or kind == "youtube"
                                          and not self.video_done and now - self.video_since > 20):
            self.capri.wake_chip()  # something juicy on screen: she wakes up for it (says it next tick)
            return True
        if self.react_next is not None:  # the chat reader (a thread) found something: say it from here
            make, self.react_next = self.react_next, None
            if self._own(make[1]):
                self.last_react = now
                if make[0]:
                    self.draft_said = make[0]
                return True
        if kind != "chat":
            self.chat_last = self.draft_prev = ""
        if now - self.last_talk < 30:  # you're talking to her: not the moment
            return False
        if kind == "youtube":
            if name != self.react_video:
                self.react_video, self.video_since, self.video_done = name, now, False
                self.video_roll = random.random() < 0.7  # not every video
            elif not self.video_done and self.video_roll and now - self.video_since > 25 and now - self.last_react > 120:
                if self._own(lambda: self.agent.youtube_take(name, screen.read_comments(title))):
                    self.video_done, self.last_react = True, now
                    return True
            return False
        if kind != "chat":
            return False
        if switched:
            self.hello_due = now - self.last_react > 180 and random.random() < 0.5
        if self.hello_due and now - self.last_react > 180:
            if self._own(lambda: self.agent.chat_hello(name)):
                self.hello_due, self.last_react = False, now
                return True
        if self.reading or now - self.last_read < 5:
            return False
        self.last_read, self.reading = now, True

        def read():
            try:
                msgs, draft = screen.read_chat(name, title, exe)
                newest = msgs[-1] if msgs else ""
                fresh = bool(newest) and bool(self.chat_last) and not _alike(newest, self.chat_last)
                t = time.monotonic()
                if draft and _alike(draft, self.draft_prev) and not _alike(draft, self.draft_said) and len(draft) > 6 \
                        and t - self.last_react > 75:
                    # the same draft for 5+ s: you've stopped to think, she has a look
                    if random.random() < 0.6:
                        self.react_next = (draft, lambda: self.agent.typing_take(name, draft, msgs))
                    else:
                        self.draft_said = draft
                elif fresh and not draft and t - self.last_react > 150 and random.random() < 0.5:
                    self.react_next = ("", lambda: self.agent.chat_take(name, msgs))
                self.draft_prev, self.chat_last = draft, newest or self.chat_last
            finally:
                self.reading = False

        threading.Thread(target=read, daemon=True).start()
        return False

    # -- menu ---------------------------------------------------------------------
    def _build_menu(self):
        from ..voice import hotkey

        self._hotkey = hotkey
        m = self.capri.menu
        if getattr(self, "_menu_built", False):  # a language switch: drop our rows and lay them out again
            try:
                m.aboutToShow.disconnect(self._sync_fn)  # only ours: Capri's own nap-label hook stays
            except (RuntimeError, TypeError):
                pass
            for a in self._mine:
                m.removeAction(a.menuAction() if hasattr(a, "menuAction") else a)
        self._menu_built = True
        self._mine = []
        before = m.actions()[-1]  # keep "Delta (quit)" last

        def add_menu(key: str):
            menu = m.addMenu(i18n.t(key))
            m.insertMenu(before, menu)
            self._mine.append(menu)
            return menu

        ears = add_menu("menu.ears")
        group = QActionGroup(ears)
        self.ear_actions = {}
        for mode, key in (("hold", "ears.hold"), ("auto", "ears.auto"), ("off", "ears.off")):
            a = QAction(i18n.t(key), ears, checkable=True, checked=self.s["ears"] == mode)
            a.triggered.connect(lambda _=False, md=mode: self.s.set("ears", md))
            group.addAction(a)
            ears.addAction(a)
            self.ear_actions[mode] = a
        ears.addSeparator()
        self.key_action = ears.addAction(i18n.t("ears.key", combo=hotkey.pretty(self.s["talk_key"])), self._rebind)
        mics = ears.addMenu(i18n.t("ears.mic"))
        ears.aboutToShow.connect(lambda: self._fill_ears(mics))
        if self.dev:  # her whisper model, picked in setup; switching restarts her ears
            self._ear_models = ears.addMenu(i18n.t("ears.model"))
            ears.aboutToShow.connect(self._fill_ear_models)
        toggles = (("vibe", "toggle.vibe"),
                   ("eyes", "toggle.eyes"), ("chatter", "toggle.chatter"),
                   ("reactions", "toggle.reactions"), ("game_mode", "toggle.game"),
                   ("quiet", "toggle.quiet"))
        self.toggle_actions = {}
        opts = add_menu("menu.settings")  # the on/off switches live one level down: the main menu stays short
        for key, text in toggles:
            a = QAction(i18n.t(text), opts, checkable=True, checked=bool(self.s[key]))
            a.toggled.connect(lambda on, k=key: self.s.set(k, on))
            opts.addAction(a)
            self.toggle_actions[key] = a

        for key, text, fn in (("always_mini", "menu.stay_mini", self._set_always_mini),
                              ("lay", "menu.lay", self._set_lay)):
            a = QAction(i18n.t(text), opts, checkable=True, checked=bool(self.s[key]))
            a.toggled.connect(fn)
            opts.addAction(a)
            self.toggle_actions[key] = a

        def sync():  # some of these change by voice ("leave me alone"): the ticks follow
            for k, act in self.toggle_actions.items():
                if act.isChecked() != bool(self.s[k]):
                    act.blockSignals(True)
                    act.setChecked(bool(self.s[k]))
                    act.blockSignals(False)

        m.aboutToShow.connect(sync)
        opts.aboutToShow.connect(sync)
        self._sync_fn = sync
        looks = add_menu("menu.skins")
        looks.aboutToShow.connect(lambda: self._fill_skins(looks))
        sizes = add_menu("menu.size")
        sgroup = QActionGroup(sizes)  # one size at a time: a single tick, and one resize at once
        sgroup.setExclusive(True)
        self.size_actions = {}
        for key, px in (("size.tiny", 150), ("size.small", 190), ("size.medium", 240), ("size.large", 320),
                        ("size.huge", 420)):
            a = QAction(i18n.t(key), sizes, checkable=True)
            a.triggered.connect(lambda _=False, v=px: self._set_size(v))
            sgroup.addAction(a)
            sizes.addAction(a)
            self.size_actions[px] = a
        sizes.aboutToShow.connect(self._sync_sizes)
        brains = add_menu("menu.brain")
        brains.aboutToShow.connect(lambda: self._fill_brains(brains))
        opts.addSeparator()  # the rarely-used pages live in Settings too, so her door stays short
        memory = opts.addMenu(i18n.t("menu.memory"))
        memory.addAction(i18n.t("memory.see"), self._see_memories)
        memory.addAction(i18n.t("memory.wipe"), self._wipe_memories)
        clips = opts.addMenu(i18n.t("menu.clips"))  # "capri, clip that": how much she keeps, and where it goes
        cgroup = QActionGroup(clips)
        cgroup.setExclusive(True)
        self.clip_actions = {}
        for secs, key in ((0, "clips.off"), (30, "clips.30"), (60, "clips.60"), (120, "clips.120")):
            a = QAction(i18n.t(key), clips, checkable=True, checked=int(self.s["clips"] or 0) == secs)
            a.triggered.connect(lambda _=False, v=secs: self._set_clips(v))
            cgroup.addAction(a)
            clips.addAction(a)
            self.clip_actions[secs] = a
        clips.addSeparator()
        clips.addAction(i18n.t("clips.folder"), self._pick_clips_dir)
        clips.addAction(i18n.t("clips.open"), self._open_clips_dir)
        langs = opts.addMenu(i18n.t("menu.language"))
        lgroup = QActionGroup(langs)
        cur = self.s.get("ui_lang", "auto")
        for code, name in [("auto", i18n.t("language.auto_talk"))] + list(i18n.LANGS.items()):
            a = QAction(name, langs, checkable=True, checked=code == cur)
            a.triggered.connect(lambda _=False, c=code: self._set_ui_lang(c))
            lgroup.addAction(a)
            langs.addAction(a)
        credits = opts.addMenu(i18n.t("menu.credits"))
        for label, link in CREDITS:
            credits.addAction(label).triggered.connect(lambda _=False, u=link: self._credit(u))
        m.removeAction(opts.menuAction())  # Settings goes last, just above Delta (quit)
        donate = QAction(i18n.t("menu.donate"), m)  # keeps Capri going: her linktree (donations, socials)
        donate.triggered.connect(lambda: __import__("webbrowser").open(DONATE))
        m.insertAction(before, donate)
        self._mine.append(donate)
        m.insertMenu(before, opts)
        self._mine.append(m.insertSeparator(before))

    def _battery(self):
        """Laptop low and unplugged: she goes mini to save power and tells you to plug in. Plugged back in (or charged
        up again): she comes back full size, if it was the battery that made her small."""
        from ..abilities import battery

        try:
            st = battery.status()
        except Exception:
            return
        c = self.capri
        if battery.low(st):
            if self._batt_warned:
                return
            self._batt_warned = True
            if c.mode in ("stand", "perch", "sleep", "lay"):
                c.go_mini()
                self._batt_mini = True
            line = random.choice(i18n.tlist("lines.battery.low", BATTERY_LOW)).format(pct=st[0])
            QTimer.singleShot(900, lambda: c.say(line))
        elif battery.fine(st):
            self._batt_warned = False
            if self._batt_mini:
                self._batt_mini = False
                if c.mode == "chip" and not self.s["always_mini"]:
                    c.wake_chip()
                    line = random.choice(i18n.tlist("lines.battery.back", BATTERY_BACK))
                    QTimer.singleShot(1500, lambda: c.say(line))

    def _start_mini(self):
        """Always mini: once her entrance is over she shrinks straight into her mini form."""
        if self.capri.mode == "chip" or not self.s["always_mini"]:
            return
        if self.capri.mode in ("stand", "perch"):
            self.capri.go_mini()
            return
        self._mini_tries += 1
        if self._mini_tries < 40:
            QTimer.singleShot(500, self._start_mini)

    def _set_always_mini(self, on: bool):
        self.s.set("always_mini", on)
        if on:  # full size: she shrinks now; napping already: she stays small, now awake
            self.capri.go_mini()

    def _left_mini(self):
        """She went back to full size (menu, double-click): Always mini is off then, or she'd shrink at next start."""
        if self.s["always_mini"]:
            self.s.set("always_mini", False)

    def _set_lay(self, on: bool):
        self.s.set("lay", on)
        self.capri.lay_ok = on
        if self.capri.mode == "chip":
            self.capri.chip.laying = self.capri._can_lay()

    def _sync_sizes(self):
        """Her Size menu: the tick on the size she is, and every row locked while a resize is still landing."""
        busy = time.monotonic() < self._size_lock
        for px, a in self.size_actions.items():
            a.setChecked(self.capri.S == px)
            a.setEnabled(not busy)

    def _set_size(self, px: int):
        if time.monotonic() < self._size_lock:  # a second size picked mid-resize: ignored, the first one wins
            return self._sync_sizes()
        if self.capri.set_size(px) is False:  # she can't resize right now (mid-teleport, leaving...)
            return self._sync_sizes()
        self._size_lock = time.monotonic() + 1.2
        self.s.set("size", px)
        self._sync_sizes()

    def _set_ui_lang(self, code: str, quiet: bool = False):
        """Language menu / first-run picker: one language for everything. Her menu, her replies and her ears all
        switch to it right away and stay on it (Auto: menu follows the system, replies follow how you talk)."""
        self.s.set("ui_lang", code)
        self.s.set("lang_set", True)
        i18n.set_lang(code)
        if self.ears is not None and code in i18n.LANGS:
            self.ears.hint = code
        QTimer.singleShot(0, self._apply_ui_lang)
        if not quiet and self.capri.can_talk:
            QTimer.singleShot(300, lambda: self.capri.say(i18n.t("lines.lang_set" if code in i18n.LANGS else
                                                                 "lines.lang_auto")))
        QTimer.singleShot(1500, self._ears_check)

    def _ask_language(self):
        """First run: which language she should talk in, so a French PC or a German tester doesn't get a surprise.
        A small window with the eight languages (each in its own words) and Auto. Asked once."""
        if self.s["lang_set"]:
            return
        from PySide6.QtWidgets import QDialog, QGridLayout, QLabel, QPushButton, QVBoxLayout

        dlg = QDialog(None)
        dlg.setWindowTitle(i18n.t("langpick.title"))
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dlg.setStyleSheet(
            "QDialog { background: rgb(14,6,22); } QLabel { color: rgb(246,240,250); font-size: 14px; }"
            "QPushButton { background: rgb(38,32,48); color: rgb(246,240,250); border: 1px solid rgb(60,30,70);"
            " padding: 8px 14px; font-size: 14px; } QPushButton:hover { background: rgb(255,46,112); color: black; }")
        box = QVBoxLayout(dlg)
        text = QLabel(i18n.t("langpick.text"))
        text.setWordWrap(True)
        box.addWidget(text)
        grid = QGridLayout()
        picks = list(i18n.LANGS.items()) + [("auto", i18n.t("language.auto_talk"))]
        for n, (code, name) in enumerate(picks):
            b = QPushButton(name)
            b.clicked.connect(lambda _=False, c=code: (dlg.accept(), self._set_ui_lang(c)))
            if code == i18n.lang() and code in i18n.LANGS:
                b.setDefault(True)
            grid.addWidget(b, n // 3, n % 3)
        box.addLayout(grid)
        dlg.setMinimumWidth(460)
        # closing it without a pick keeps Auto and doesn't ask again (it would nag every start)
        dlg.rejected.connect(lambda: self.s.set("lang_set", True))
        self._lang_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _ears_check(self):
        """Her ears are an English-only model but she's set to talk another language: she says how to fix it."""
        ears = self.ears
        if not self.dev or ears is None or ears.stt is None or ears.multilingual or self.s["ears"] == "off":
            return
        lang = self.s["ui_lang"] if self.s["ui_lang"] in i18n.LANGS else i18n.lang()
        if lang != "en" and self.capri.can_talk:
            self.capri.say(i18n.t("lines.ears.english"))

    def _apply_ui_lang(self):
        self._build_menu()
        self.capri.retranslate()

    def _credit(self, link: str):
        """Credits rows open the person's GitHub; the artist is on Discord only."""
        if link:
            import webbrowser

            webbrowser.open(link)
        else:
            self.capri.say(i18n.t("lines.credit"))

    def _see_memories(self):
        """Opens her memory shard (the JSON with her facts and recent chat) in the default viewer: text editor on
        Linux, Notepad / whatever .json opens on Windows."""
        from ..abilities import desk_tools

        mem = self.agent.memory
        try:
            if not mem.path.exists():  # nothing saved yet: write the empty shard so there's a file to look at
                with mem.lock:
                    mem._save()
            desk_tools.open_file(mem.path)
        except Exception as e:
            print(f"[capri] couldn't open {mem.path}: {e}")
            self.capri.say(i18n.t("memory.open_fail", err=e, path=mem.path))

    def _wipe_memories(self):
        """Clears everything she remembers: her facts and the recent chat. Asks first; the shard stays, emptied."""
        from PySide6.QtWidgets import QMessageBox

        mem = self.agent.memory
        try:  # an explicit box (not the static helper): her window is frameless and always on top, and on Wayland a
            box = QMessageBox(None)  # modal parented to it can end up invisible. A top-level one always shows.
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(i18n.t("memory.wipe.title"))
            box.setText(i18n.t("memory.wipe.text"))
            box.setInformativeText(i18n.t("memory.wipe.info", file=mem.path.name))
            yes = box.addButton(i18n.t("yes"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(i18n.t("no"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(box.buttons()[-1])  # No is the default
            box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            box.show()
            box.raise_()
            box.activateWindow()
            box.exec()
            ok = box.clickedButton() is yes
        except Exception as e:  # never a silent menu row: say why
            print(f"[capri] memory dialog failed: {e}")
            self.capri.say(i18n.t("lines.wipe_fail", err=e))
            return
        if not ok:
            return
        with mem.lock:
            mem.data = {"facts": [], "history": []}
            mem._save()
        if getattr(mem, "eru", None):  # the vector store too, in builds that have one
            try:
                mem.eru.wipe()
            except Exception as e:
                print(f"[capri] couldn't wipe the vector memory: {e}")
        self.capri.say(i18n.t("memory.wiped"))

    def _fill_skins(self, menu):
        """Her looks; a secret one is "???" until you've picked it once."""
        menu.clear()
        group = QActionGroup(menu)
        for skin in skins.SKINS:
            a = QAction(skins.label(skin, self.s["skins_seen"]), menu, checkable=True, checked=skin.id == self.capri.skin.id)
            a.triggered.connect(lambda _=False, sk=skin, act=a: (self.set_skin(sk.id), act.setText(sk.name)))
            group.addAction(a)
            menu.addAction(a)

    def _fill_brains(self, menu):
        """Every .gguf in ERU AI's models/llm folder; picking one swaps her brain live (a few seconds). Brains she
        doesn't have yet are right there too: picking one downloads it (and her brain engine if it's missing)."""
        import glob

        from .. import portable

        menu.clear()
        self.dl_action = None
        if self.dl:
            self.dl_action = menu.addAction(self.dl)
            self.dl_action.setEnabled(False)
            menu.addSeparator()
        if self.swapping:  # one brain at a time: every row stays locked until the swap is done
            menu.addAction(i18n.t("brain.swapping")).setEnabled(False)
            menu.addSeparator()
        bgroup = QActionGroup(menu)
        bgroup.setExclusive(True)
        from ..brain import claude_code

        mp = self.cfg["llm"].get("model_path") or ""
        cur = os.path.normcase(os.path.abspath(mp)) if mp not in ("none", claude_code.ID, "lm-studio") else mp
        off = menu.addAction(i18n.t("brain.none"))
        off.setCheckable(True)
        off.setChecked(cur == "none")
        off.setEnabled(not self.swapping)
        bgroup.addAction(off)
        off.triggered.connect(lambda _=False: self._swap_brain("none"))
        # Claude Code: an extra pick when its CLI is on this PC (it thinks in the cloud, and can work the PC for you)
        if claude_code.find_cli(self.cfg):
            smart = bool(self.s["cc_smart"])
            cc = menu.addAction(i18n.t("brain.claude"))
            cc.setCheckable(True)
            cc.setChecked(cur == claude_code.ID and not smart)
            cc.setEnabled(not self.swapping)
            bgroup.addAction(cc)
            cc.triggered.connect(lambda _=False: self._use_claude(False))
            sm = menu.addAction(i18n.t("brain.claude_smart"))
            sm.setCheckable(True)
            sm.setChecked(cur == claude_code.ID and smart)
            sm.setEnabled(not self.swapping)
            bgroup.addAction(sm)
            sm.triggered.connect(lambda _=False: self._use_claude(True))
        else:
            import webbrowser

            menu.addAction(i18n.t("brain.claude_missing"), lambda: webbrowser.open(claude_code.SETUP_URL))
        # LM Studio: any model you run there (free, on your own GPU), for when Claude costs too much
        lms = menu.addAction(i18n.t("brain.lmstudio"))
        lms.setCheckable(True)
        lms.setChecked(cur == "lm-studio")
        lms.setEnabled(not self.swapping)
        bgroup.addAction(lms)
        lms.triggered.connect(lambda _=False: self._swap_brain("lm-studio"))
        menu.addSeparator()
        if self.dev:
            paths = [str(p) for p in portable.brains()]
        else:
            root = os.path.join(self.cfg["eru_ai_root"], "models", "llm")
            paths = sorted(glob.glob(os.path.join(root, "**", "*.gguf"), recursive=True))
        # the brain in use and any you picked from your PC show up too, wherever they live
        for extra in [mp] + list(self.s["my_brains"]):
            if extra and extra != "none" and os.path.exists(extra) and \
                    os.path.normcase(os.path.abspath(extra)) not in {os.path.normcase(os.path.abspath(p)) for p in paths}:
                paths.append(extra)
        for path in paths:
            if "mmproj" in os.path.basename(path).lower():  # a vision add-on file, not a brain
                continue
            name = os.path.basename(path).rsplit(".", 1)[0]
            label = re.sub(r"[-_.](?:Q\d\w*|IQ\d\w*|q\d\w*)$", "", name)
            a = menu.addAction(f"{label}  ({os.path.getsize(path) / 2 ** 30:.1f} GB)")
            a.setCheckable(True)
            a.setChecked(os.path.normcase(os.path.abspath(path)) == cur)
            a.setEnabled(not self.swapping)
            bgroup.addAction(a)
            a.triggered.connect(lambda _=False, p=path: self._swap_brain(p))
        have = {os.path.basename(p).lower() for p in paths}  # already on this PC somewhere else: no "Get" for it
        missing = [it for it in portable.BRAINS if not it.installed() and Path(it.files[0][1]).name.lower() not in have]
        if missing:
            menu.addSeparator()
            best, _ = portable.best_brain()
            for it in missing:
                star = "  ★ best for your PC" if it is best else ""
                a = menu.addAction(f"Get {it.label.split('  (')[0]}, {portable.gb(it.size)}{star}")
                a.setToolTip(it.note)
                a.setEnabled(self.dl is None and not self.swapping)
                a.triggered.connect(lambda _=False, i=it: self._get_brain(i))
        menu.addSeparator()
        menu.addAction("Use a model from my PC...  (.gguf)", self._pick_brain).setEnabled(not self.swapping)
        menu.addAction("Open my models folder",
                       lambda: (portable.LLM_DIR.mkdir(parents=True, exist_ok=True), _open_folder(portable.LLM_DIR)))

    def _fill_ear_models(self):
        from .. import portable

        menu = self._ear_models
        menu.clear()
        models = portable.ears_models()
        use = portable.pick_ears(models, self.s["whisper_model"] or "")
        for path in models:
            label = path.name.replace("ggml-", "").replace(".bin", "")
            if portable.qwen_ears(path.name):
                label = "Qwen3-ASR  (sharpest, all languages)"
            elif portable.english_only(path.name):
                label = label.removesuffix(".en") + "  (English only)"
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(use is not None and path.name == use.name)
            a.triggered.connect(lambda _=False, n=path.name: self._swap_ears(n))
        missing = [it for it in portable.EARS if not it.installed()]
        if missing:
            menu.addSeparator()
            best = portable.EARS[portable.best_ears()]
            for it in missing:
                a = menu.addAction(f"Get {it.label} ears, {portable.gb(it.size)}"
                                   + ("" if portable.english_only(it.files[0][1]) else "  (all languages)")
                                   + ("  ★ best for your PC" if it is best else ""))
                a.setToolTip(it.note)
                a.setEnabled(self.dl is None)
                a.triggered.connect(lambda _=False, i=it: self._get_ears(i))

    def _swap_ears(self, name: str):
        """A different whisper model: saved, and her ears restart with it."""
        self.s.set("whisper_model", name)
        self.cfg["stt"]["whisper_model"] = name
        if self.ears:
            self.ears.stopped = True
            stt = getattr(self.ears, "stt", None)
            if stt is not None and hasattr(stt, "close"):
                stt.close()
        QTimer.singleShot(1500, self._start_ears)

    # -- downloads, straight from her menu (no setup window) ---------------------------------------
    # -- clips: "capri, clip that" ---------------------------------------------------------------------------
    def _clips_dir(self) -> Path:
        from ..abilities import clips

        return Path(self.s["clips_dir"]) if self.s["clips_dir"] else clips.default_dir()

    def _set_clips(self, secs: int):
        """Clips off, or how much she keeps (30 s / 1 min / 2 min). The first time, her recorder (ffmpeg) downloads."""
        from .. import portable
        from ..abilities import clips

        self.s.set("clips", int(secs))
        if not secs:
            self._stop_clips()
            self.capri.say(i18n.t("lines.clips.off"))
            return
        if sys.platform == "win32" and clips.find_ffmpeg() is None:  # Linux: the package manager has the recorder
            self._fetch([portable.ENGINE_CLIPS], i18n.t("clips.what"), then=self._start_clips)
            return
        self._start_clips()

    def _stop_clips(self):
        c, self.clipper = getattr(self, "clipper", None), None
        if c is not None:
            threading.Thread(target=c.stop, daemon=True).start()

    def _start_clips(self):
        """(Re)start her clip loop with the menu's length and folder, off the Qt thread (finding the encoder takes
        a few seconds the first time)."""
        from ..abilities import clips
        from ..config import DATA

        self._stop_clips()
        secs = int(self.s["clips"] or 0)
        if not secs:
            return
        try:  # Windows: ffmpeg's loop; Linux: gpu-screen-recorder, else ffmpeg on X11
            rec = clips.make_recorder(secs, self._clips_dir(), DATA / "clips")
        except FileNotFoundError:  # Windows without her ffmpeg yet: the menu downloads it
            return
        except RuntimeError as e:  # Linux with nothing that can record: say what to install
            self.capri.say(f"Clips need a screen recorder here, choom: {e}.")
            return

        def go():
            try:
                rec.start()
                self.clipper = rec
                self.bridge.replied.emit(i18n.t("lines.clips.on", secs=secs), "", False)
            except Exception as e:
                print(f"[capri] clips: {e}")
                self.bridge.replied.emit(i18n.t("lines.clips.fail"), "", False)

        threading.Thread(target=go, daemon=True).start()

    def _clip(self, seconds: int | None = None) -> str:
        """For her brain: save the clip, -> what happened (said back in her own words)."""
        c = getattr(self, "clipper", None)
        if c is None or not c.running:
            return "couldn't clip: clips are off (right-click me, Settings, Clips, pick a length)"
        try:
            path = c.clip(seconds)
        except Exception as e:
            print(f"[capri] clip failed: {e}")
            return f"couldn't save the clip ({e})"
        print(f"[capri] clip saved: {path}")
        return f"saved the last {min(int(seconds or c.seconds), c.seconds)} seconds as a clip in {path.parent.name}"

    def _pick_clips_dir(self):
        from PySide6.QtWidgets import QFileDialog

        d = QFileDialog.getExistingDirectory(None, i18n.t("clips.folder"), str(self._clips_dir()))
        if not d:
            return
        self.s.set("clips_dir", d)
        if getattr(self, "clipper", None) is not None:
            self.clipper.out_dir = Path(d)
        self.capri.say(i18n.t("lines.clips.folder", folder=Path(d).name or d))

    def _open_clips_dir(self):
        d = self._clips_dir()
        d.mkdir(parents=True, exist_ok=True)
        _open_folder(d)

    def _fetch(self, items, what: str, then=None):
        """Download in the background. The progress shows at the top of her Brain menu; she says when it's done,
        then `then()` runs (on the Qt thread)."""
        from .. import portable

        if self.dl:
            self.capri.say(i18n.t("lines.dl.busy"))
            return
        items = [it for it in items if not it.installed()]
        if not items:
            if then:
                then()
            return
        self.dl = i18n.t("brain.getting", what=what)
        self.capri.say(i18n.t("lines.dl.start", what=what,
                              size=portable.gb(sum(it.size for it in items))))
        self._dl_timer = timer = QTimer()
        timer.timeout.connect(lambda: self.dl_action is not None and self.dl and self.dl_action.setText(self.dl))
        timer.start(500)

        def progress(done, total, _what):
            self.dl = i18n.t("brain.getting.pct", what=what, pct=100 * done // max(1, total))

        def work():
            try:
                portable.download(items, progress)
                line, ok = i18n.t("lines.dl.done", what=what), True
            except Exception as e:
                print(f"[capri] download failed: {e}")
                line, ok = i18n.t("lines.dl.fail", what=what, err=e), False
            self.dl = None

            def done():
                timer.stop()
                self.capri.say(line)
                if ok and then:
                    then()
            self.bridge.fetched.emit(done)

        threading.Thread(target=work, daemon=True).start()

    def _engine(self):
        """Her brain engine (llama.cpp) if it isn't there yet: CUDA on NVIDIA, Vulkan on anything else."""
        from .. import portable

        if portable.find_exe("llama-server") is not None:
            return []
        want = "cuda" if portable.has_nvidia() else "vulkan"
        return [next(it for it in portable.ENGINE_GPU if it.key == want)]

    def _get_brain(self, item):
        from ..config import ROOT

        path = str(ROOT / item.files[0][1])
        self._fetch(self._engine() + [item], item.label.split("  (")[0], then=lambda: self._swap_brain(path))

    def _get_ears(self, item):
        from .. import portable

        extra = []
        if portable.qwen_ears(item.files[0][1]):
            extra = self._engine()  # Qwen ears run on her llama.cpp engine, not whisper's
        elif not portable.ENGINE_EARS.installed():
            if portable.ENGINE_EARS.system:  # Linux: the package manager has it
                self.capri.say(i18n.t("lines.dl.whisper"))
                return
            extra = [portable.ENGINE_EARS]
        name = Path(item.files[0][1]).name

        def ready():
            if self.s["ears"] == "off":
                self.s.set("ears", "hold")
            self._swap_ears(name)
        self._fetch(extra + [item], i18n.t("brain.what.ears", label=item.label), then=ready)

    def _pick_brain(self):
        """Any .gguf already on this PC (LM Studio's, one you downloaded...): no copy, she just loads it from there."""
        from PySide6.QtWidgets import QFileDialog

        from .. import portable

        start = next((str(d) for d in portable.model_dirs()), os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(None, i18n.t("dialog.pick_brain"), start, "GGUF models (*.gguf)")
        if not path or "mmproj" in os.path.basename(path).lower():
            return
        self.s.set("my_brains", [p for p in self.s["my_brains"] if p != path] + [path])
        self._swap_brain(path)

    def _use_claude(self, smart: bool = False):
        """Claude Code as her brain: signed in, she swaps to it; not yet, a sign-in window opens and she says what to
        do. The check runs off the Qt thread (the CLI takes a moment to answer). smart: smart mode, Claude Code works
        the PC with her tools and its own, and talks as her."""
        from ..brain import claude_code

        self.s.set("cc_smart", smart)
        self.cfg.setdefault("claude_code", {})["smart"] = smart
        if self.agent is not None and (self.cfg["llm"].get("model_path") or "") == claude_code.ID:
            llm = self.agent.llm  # already her brain: just switch the mode, a fresh page for it
            if hasattr(llm, "smart"):
                llm.smart = smart
                llm.new_chat()
                self.capri.say(i18n.t("lines.smart_on" if smart else "lines.smart_off"))
                return
        exe = claude_code.find_cli(self.cfg)
        if exe is None:
            self.capri.say("Can't find Claude Code on this PC anymore, choom.")
            return

        def check():
            if claude_code.signed_in(exe):
                self.bridge.fetched.emit(lambda: self._swap_brain(claude_code.ID))
            elif claude_code.open_sign_in(exe):
                self.bridge.replied.emit("Claude Code wants you signed in first. Do it in the window I just opened, "
                                         "then pick it from my Brain menu again.", "", False)
            else:
                self.bridge.replied.emit("Claude Code wants you signed in first. Run 'claude auth login' in a "
                                         "terminal, then pick it from my Brain menu again.", "", False)

        threading.Thread(target=check, daemon=True).start()

    def _swap_brain(self, path: str):
        if self.agent is None or self.swapping or \
                os.path.normcase(path) == os.path.normcase(self.cfg["llm"].get("model_path") or ""):
            return
        if path not in ("none", "claude-code", "lm-studio") and self.cfg["llm"].get("backend") == "server" and \
                self._engine():
            # a brain without its engine (llama.cpp) only says "brain chip's not slotted": get the engine first
            self._fetch(self._engine(), i18n.t("brain.what.engine"), then=lambda: self._swap_brain(path))
            return
        self.swapping = True
        self.s.set("brain", path)
        self.capri.say(i18n.t("lines.brain.swap"))

        def work():
            try:
                swap()
            finally:
                self.swapping = False

        def swap():
            import gc

            from ..brain import llm as llm_mod

            with self.busy:  # nothing else thinks while the brain is out
                t = time.monotonic()
                with llm_mod.GPU_LOCK:
                    old = self.agent.llm
                    self.agent.llm = None
                    try:
                        old.model.close()
                    except Exception:
                        pass
                    del old
                    gc.collect()
                self.cfg["llm"]["model_path"] = path
                try:
                    self.agent.llm = llm_mod.create(self.cfg)
                    if getattr(self.agent.llm, "offline", False) and path == "lm-studio":
                        self.bridge.replied.emit("LM Studio isn't answering, choom. Open it, load a model, switch its "
                                                 "local server on (Developer tab), then pick it again.", "", False)
                        return
                    if getattr(self.agent.llm, "offline", False):  # it didn't load: say so, the reason is in the log
                        self.bridge.replied.emit("That brain chip won't slot in, choom. Try another one from my Brain "
                                                 "menu, or a lighter one if your GPU's busy.", "", False)
                        return
                    self.agent.warm_up()
                    self._brain_eyes()
                    print(f"[capri] brain swapped to {os.path.basename(path)} in {time.monotonic() - t:.1f}s")
                    self.bridge.replied.emit("Brain's out. I'll still open stuff, set timers and play music for u, "
                                             "I just won't chat much." if path == "none" else
                                             "New brain chip, who dis? ...Okay, I'm back.", "", False)
                except Exception as e:
                    print(f"[capri] brain swap failed: {e}")
                    self.bridge.replied.emit("That brain chip flatlined on me. Try another one from my Brain menu.",
                                             "", False)

        threading.Thread(target=work, daemon=True).start()

    def _fill_ears(self, mics):
        self.key_action.setText(i18n.t("ears.key", combo=self._hotkey.pretty(self.s["talk_key"])))
        mics.clear()
        from ..voice.stt import list_inputs

        default = i18n.t("ears.default")
        cur = (self.s["mic"] or self.cfg["stt"].get("input_device") or "").lower()
        names = [default] + list_inputs()
        # one mic ticked: the exact name, else the first that contains it, else the default
        ticked = next((n for n in names[1:] if cur and n.lower() == cur), None) or next(
            (n for n in names[1:] if cur and cur in n.lower()), default)
        if getattr(self, "mic_group", None) is not None:
            self.mic_group.deleteLater()
        self.mic_group = QActionGroup(mics)  # one mic at a time, like sizes and brains
        self.mic_group.setExclusive(True)
        for name in names:
            a = mics.addAction(name)
            a.setCheckable(True)
            a.setChecked(name == ticked)
            self.mic_group.addAction(a)
            a.triggered.connect(lambda _=False, n=name: self._pick_mic("" if n == default else n))

    def _pick_mic(self, name: str):
        self.s.set("mic", name)
        if self.ears:
            self.ears.reopen = True

    def _rebind(self):
        """Ask for the new combo, then catch it as soon as every key is let go."""
        self.capri.say(i18n.t("lines.key.ask"))
        cap = self._hotkey.Capture()
        timer = QTimer()
        started = time.monotonic()

        def poll():
            if time.monotonic() - started < 0.6:  # let go of the menu click first
                return
            combo = cap.poll()
            if combo:
                timer.stop()
                self.s.set("talk_key", combo)
                self.s.set("ears", "hold")
                self.ear_actions["hold"].setChecked(True)
                self.capri.say(i18n.t("lines.key.locked", combo=self._hotkey.pretty(combo)))
            elif time.monotonic() - started > 12:
                timer.stop()
                self.capri.say(i18n.t("lines.key.slow"))

        timer.timeout.connect(poll)
        timer.start(20)
        self._rebind_timer = timer

    def _update_notice(self):
        """A while after she starts: if GitHub has a newer Capri she hasn't told you about yet, she mentions it once."""
        from ..abilities import updates

        time.sleep(90)
        try:
            rel = updates.latest()
        except Exception as e:
            print(f"[capri] update check failed: {e}")
            return
        if not updates.has_update(rel) or self.s["update_told"] == rel.version:
            return
        print(f"[capri] update available: v{rel.version} (she's v{updates.current()})")
        for _ in range(40):  # wait for a good moment: not mid-reply, not napping
            if (self.capri.can_talk and not self.capri.speaking and not self.capri.bubble.isVisible()
                    and not self.busy.locked()):
                break
            time.sleep(15)
        self.s.set("update_told", rel.version)
        self.bridge.replied.emit(updates.line("news", self.agent._recent_language(), v=rel.version), "", False)

    def _install_update(self, path):
        """She says bye, then the installer starts and she closes, so none of her files are in use."""
        from ..abilities import updates

        self.capri.say(updates.line("bye", self.agent._recent_language()))

        def go():
            try:
                updates.run_installer(path)
            except Exception as e:
                print(f"[capri] couldn't start the installer: {e}")
                _open_folder(str(path.parent))  # they can double-click it themselves
                return
            self.quit()

        QTimer.singleShot(3500, go)

    def quit(self):
        """Stop the voice server, then leave without the interpreter's teardown (CUDA + PortAudio threads
        being torn down at exit crash on Windows)."""
        for hook in self.on_quit:
            try:
                hook()
            except Exception as e:
                print(f"[capri] quit hook failed: {e}")
        if self.ears:
            self.ears.stopped = True
        if getattr(self, "clipper", None) is not None:  # her clip loop stops and is wiped with her
            self.clipper.stop()
        try:
            self.agent.mcp.close()
        except Exception:
            pass
        # the portable build's helper servers (llama-server, whisper-server) go with her
        for helper in (getattr(self.agent, "llm", None), getattr(self.ears, "stt", None)):
            if helper is not None and hasattr(helper, "close"):
                try:
                    helper.close()
                except Exception:
                    pass
        sys.stdout.flush()
        os._exit(0)


def _same_song(a: str, b: str) -> bool:
    words = lambda s: set(re.findall(r"[a-z0-9']{3,}", s.lower()))  # noqa: E731
    wa, wb = words(a), words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) > 0.6


def main():
    import argparse

    ap = argparse.ArgumentParser(prog="capri")
    ap.parse_args()
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.desk = DeskApp()  # kept alive with the app
    sys.exit(app.exec())
